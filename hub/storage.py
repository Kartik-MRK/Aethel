"""Server-side storage: objects, repository index, and health checks.

The Hub reuses `aethel.core.ObjectStore` unchanged. That is deliberate, the
same content-addressed store, the same atomic writes, the same verify-on-read.
A second storage implementation would be a second set of bugs, and any drift
between them would show up as false "corrupt" reports.

What the Hub adds on top is the notion of *repositories*: a named collection of
branch refs pointing into the shared object store. Objects are global (a blob
is the same bytes wherever it came from, so two repositories sharing a base
model share the blob), while refs are per-repository.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aethel.core.atomic import atomic_write_text, file_lock
from aethel.core.commits import COMMIT_SCHEMA, find_adapter_filename, walk_history
from aethel.core.errors import AethelError, CorruptObject, ObjectNotFound
from aethel.core.hashing import hash_bytes, normalize_hash
from aethel.core.objects import OBJECT_KINDS, ObjectStore
from aethel.core.refs import validate_branch_name
from hub.log import AppendResult, TransparencyLog


class HubStorageError(AethelError):
    """Raised when a Hub storage operation fails."""


class HashMismatch(HubStorageError):
    """Uploaded content does not hash to the hash it claimed.

    The single most important rejection in the Hub. It means a client never has
    to trust that the server stored what was sent, if the bytes were altered
    in transit or substituted, the hash will not match and the write fails.
    """


class RefConflict(HubStorageError):
    """The remote branch changed or the proposed update discards history."""


@dataclass
class RepoRecord:
    """A named repository: branch refs plus a little metadata."""

    name: str
    branches: dict[str, str]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "branches": self.branches,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HubStorage:
    """Objects and repository refs for a Hub instance."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.objects = ObjectStore(self.data_dir / "objects")
        self.repos_path = self.data_dir / "repos.json"

    # -- objects -----------------------------------------------------------

    def put_blob(self, claimed_hash: str, data: bytes) -> str:
        """Store bytes, but only if they hash to the hash the client claimed.

        Verification happens before the write, not after, so a mismatched
        upload never lands on disk at all.
        """
        expected = normalize_hash(claimed_hash, label="blob hash")
        actual = hash_bytes(data)

        if actual != expected:
            raise HashMismatch(
                f"content hashes to {actual[:12]} but was uploaded as {expected[:12]}"
            )

        return self.objects.write_blob(data)

    def put_json_object(self, kind: str, claimed_hash: str, data: bytes) -> str:
        """Store a structured object, verifying its hash and its shape.

        Both checks matter: the hash proves the bytes are what was claimed, and
        parsing proves the object is usable. Storing a hash-valid but unparseable
        commit would produce an object that fails on every later read.
        """
        expected = normalize_hash(claimed_hash, label=f"{kind[:-1]} hash")
        actual = hash_bytes(data)

        if actual != expected:
            raise HashMismatch(
                f"content hashes to {actual[:12]} but was uploaded as {expected[:12]}"
            )

        try:
            decoded = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HubStorageError(f"{kind[:-1]} object is not valid JSON: {exc}") from exc

        if not isinstance(decoded, dict):
            raise HubStorageError(f"{kind[:-1]} object must be a JSON object")

        # Write the received bytes verbatim rather than re-serializing the
        # decoded form. Re-serializing risks producing different bytes (and so
        # a different hash) if the client's canonical form ever differs by a
        # whitespace or escaping convention.
        destination = self.objects.path_for(kind, expected)
        if not self.objects.verify(kind, expected):
            from aethel.core.atomic import atomic_write_bytes

            atomic_write_bytes(destination, data)

        return expected

    def has_object(self, kind: str, object_hash: str) -> bool:
        return self.objects.verify(kind, object_hash)

    def missing_objects(self, wanted: dict[str, list[str]]) -> dict[str, list[str]]:
        """Given hashes a client holds, report which the Hub lacks.

        Lets `push` upload only what is actually needed, the difference
        between re-uploading an entire history and sending one new commit.
        """
        missing: dict[str, list[str]] = {}

        for kind, hashes in wanted.items():
            if kind not in OBJECT_KINDS:
                continue
            absent = [h for h in hashes if not self.has_object(kind, h)]
            if absent:
                missing[kind] = absent

        return missing

    # -- repositories ------------------------------------------------------

    def repos(self) -> dict[str, RepoRecord]:
        if not self.repos_path.is_file():
            return {}

        try:
            payload = json.loads(self.repos_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HubStorageError(f"repository index is invalid JSON: {exc}") from exc

        return {
            name: RepoRecord(
                name=name,
                branches=dict(record.get("branches", {})),
                created_at=record.get("created_at", ""),
                updated_at=record.get("updated_at", ""),
            )
            for name, record in payload.items()
        }

    def repo(self, name: str) -> RepoRecord | None:
        return self.repos().get(name)

    def set_branch(self, repo_name: str, branch: str, commit_hash: str) -> RepoRecord:
        """Advance a complete branch without discarding its previous history."""
        record, _ = self.publish_branch(repo_name, branch, commit_hash)
        return record

    def validate_history(self, commit_hash: str) -> list[dict]:
        """Verify every reachable object, without a display pagination limit.

        Log acceptance does not prove that dependencies are still intact. Recheck
        them on each publication, including retries, and reuse verified objects
        only within this traversal.
        """
        history = self.commit_history(commit_hash, limit=None)
        verified_trees: dict[str, dict[str, str]] = {}
        verified_bases: set[str] = set()
        verified_blobs: set[str] = set()
        for commit in history:
            if commit.get("schema") != COMMIT_SCHEMA:
                raise CorruptObject(f"Unsupported commit schema: {commit.get('schema')!r}")
            if "parent_hash" not in commit:
                raise CorruptObject("Commit is missing parent_hash")
            if commit["parent_hash"] is not None:
                normalize_hash(commit["parent_hash"], label="parent hash")
            for field in ("message", "author", "timestamp"):
                if not isinstance(commit.get(field), str):
                    raise CorruptObject(f"Commit has no valid {field}")
            if not isinstance(commit.get("training_info"), dict):
                raise CorruptObject("Commit has no valid training_info")

            tree_hash = normalize_hash(commit.get("tree"), label="tree hash")
            base_hash = normalize_hash(commit.get("base"), label="base hash")
            adapter_hash = normalize_hash(commit.get("adapter_blob"), label="adapter hash")
            if tree_hash not in verified_trees:
                files = self.objects.read_tree(tree_hash)
                for blob_hash in files.values():
                    if blob_hash not in verified_blobs:
                        self.objects.blob_path(blob_hash)
                        verified_blobs.add(blob_hash)
                verified_trees[tree_hash] = files
            files = verified_trees[tree_hash]
            if files[find_adapter_filename(files)] != adapter_hash:
                raise CorruptObject("Commit adapter_blob does not match its tree")
            if base_hash not in verified_bases:
                base = self.objects.read_json("bases", base_hash)
                for field in ("model_id", "revision_sha"):
                    if not isinstance(base.get(field), str) or not base[field].strip():
                        raise CorruptObject(f"Base reference has no valid {field}")
                verified_bases.add(base_hash)
        return history

    def publish_branch(
        self,
        repo_name: str,
        branch: str,
        commit_hash: str,
        *,
        log: TransparencyLog | None = None,
        expected_tip: str | None = None,
        check_expected: bool = False,
    ) -> tuple[RepoRecord, AppendResult | None]:
        """Validate, accept, and publish under one branch-update lock.

        Rejected updates never enter the log. If the ref write fails after acceptance,
        the old ref remains readable and a retry reuses the accepted log entries.
        """
        repo_name = validate_branch_name(repo_name)
        branch = validate_branch_name(branch)
        digest = normalize_hash(commit_hash, label="commit hash")
        if expected_tip is not None:
            expected_tip = normalize_hash(expected_tip, label="expected tip")
        history = self.validate_history(digest)
        ancestors = {commit["hash"] for commit in history}

        with file_lock(self.data_dir / "repos.lock"):
            existing = self.repos()
            now = _now()

            record = existing.get(repo_name) or RepoRecord(
                name=repo_name, branches={}, created_at=now, updated_at=now
            )
            current = record.branches.get(branch)
            # Retrying an acknowledged or interrupted publication is harmless.
            if current != digest:
                if check_expected and current != expected_tip:
                    raise RefConflict(
                        f"Branch {repo_name}/{branch} changed during push. Fetch and retry."
                    )
                if current is not None and current not in ancestors:
                    raise RefConflict(
                        f"Non-fast-forward update to {repo_name}/{branch}. "
                        "Fetch the remote history or publish a new branch."
                    )

            logged = None
            if log is not None:
                logged = log.append_many(
                    [commit["hash"] for commit in reversed(history)],
                    repo=repo_name,
                    accepted_at=now,
                )
            record.branches[branch] = digest
            record.updated_at = now
            existing[repo_name] = record

            atomic_write_text(
                self.repos_path,
                json.dumps(
                    {name: r.to_dict() for name, r in existing.items()},
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )

        return record, logged

    # -- reading history ---------------------------------------------------

    def commit_history(self, commit_hash: str, limit: int | None = 100) -> list[dict]:
        """Walk a commit's ancestry, from the object store alone.

        Reuses `aethel.core.commits.walk_history` via a tiny adapter, so the
        Hub and the CLI traverse history with identical code.
        """
        adapter = _StoreOnlyRepo(self.objects)

        history: list[dict] = []
        for chash, commit in walk_history(adapter, commit_hash):
            history.append({**commit, "hash": chash})
            if limit is not None and len(history) >= limit:
                break

        return history

    def read_commit(self, commit_hash: str) -> dict:
        return self.objects.read_json("commits", commit_hash)

    def read_tree(self, tree_hash: str) -> dict[str, str]:
        return self.objects.read_tree(tree_hash)

    def read_base(self, base_hash: str) -> dict:
        return self.objects.read_json("bases", base_hash)

    def blob_path(self, blob_hash: str) -> Path:
        return self.objects.blob_path(blob_hash)

    # -- integrity ---------------------------------------------------------

    def object_counts(self) -> dict[str, int]:
        return {kind: len(list(self.objects.iter_hashes(kind))) for kind in OBJECT_KINDS}

    def verify_sample(self, per_kind: int = 25) -> dict:
        """Re-hash a bounded sample of objects and report any corruption.

        Sampled rather than exhaustive because this backs a health endpoint
        that must answer fast. `aethel fsck` is the complete check; this is the
        smoke test, and it says how many it looked at so the number is never
        mistaken for a full pass.
        """
        checked = 0
        corrupt: list[str] = []

        for kind in OBJECT_KINDS:
            for object_hash in list(self.objects.iter_hashes(kind))[:per_kind]:
                checked += 1
                if not self.objects.verify(kind, object_hash):
                    corrupt.append(f"{kind[:-1]}:{object_hash}")

        return {"checked": checked, "corrupt": corrupt}


class _StoreOnlyRepo:
    """Minimal stand-in exposing just what `walk_history` needs.

    `walk_history` only ever touches `repo.objects`, so the Hub can reuse it
    without constructing a full local repository (which would imply a HEAD, a
    workspace and refs the Hub does not have).
    """

    def __init__(self, objects: ObjectStore):
        self.objects = objects


__all__ = [
    "HubStorage",
    "HubStorageError",
    "HashMismatch",
    "RepoRecord",
    "ObjectNotFound",
]
