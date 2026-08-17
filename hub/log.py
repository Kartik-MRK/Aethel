"""The append-only transparency log.

Every commit the Hub accepts becomes a leaf, in acceptance order. The Merkle
root over those leaves is the Hub's commitment to its own history: publish the
root, serve inclusion proofs, and a client can check its commit is in there
without trusting the Hub.

Design decisions worth knowing:

**Append-only is structural, not a promise.** The only mutating operation is
`append`. There is no update and no delete anywhere in this module, so the
property holds because the code has no way to violate it.

**The log file is the source of truth; the tree is derived.** Leaves live in a
JSONL file, one entry per line, and the Merkle tree is recomputed from them.
That keeps the on-disk format trivially auditable — you can read the log with
`cat` — and means a corrupted in-memory tree can always be rebuilt.

**Leaves are commit hashes, not commit bodies.** A commit hash already commits
to its entire content (it is the hash of the canonical JSON), so hashing the
hash is sufficient and keeps leaves fixed-width.

**Domain separation is mandatory.** `aethel.core.aggregator` tags leaves with
0x00 and internal nodes with 0x01, per RFC 6962. Without that, a tree built
over another tree's internal nodes yields an identical root, and an operator
could forge an inclusion proof for something never committed — defeating the
entire point of anchoring.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from aethel.core.aggregator import MerkleTree, verify_proof
from aethel.core.atomic import file_lock
from aethel.core.hashing import normalize_hash


@dataclass(frozen=True)
class LogEntry:
    """One accepted commit, at its position in the log."""

    index: int
    commit_hash: str
    repo: str
    accepted_at: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "index": self.index,
                "commit_hash": self.commit_hash,
                "repo": self.repo,
                "accepted_at": self.accepted_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def from_dict(payload: dict) -> "LogEntry":
        return LogEntry(
            index=int(payload["index"]),
            commit_hash=payload["commit_hash"],
            repo=payload["repo"],
            accepted_at=payload["accepted_at"],
        )


@dataclass(frozen=True)
class AppendResult:
    """The outcome of one batched append.

    `entries` has one entry per requested commit, in the order requested, and
    `added` lists only the leaf indices this call created. The distinction
    matters because a re-push resolves every ancestor to an existing leaf and
    adds nothing -- reporting those as "appended" would make an idempotent sync
    look like it had changed the log.
    """

    entries: list["LogEntry"]
    added: list[int]

    @property
    def indices(self) -> list[int]:
        return [entry.index for entry in self.entries]


class TransparencyLog:
    """An append-only log of accepted commits, with Merkle inclusion proofs."""

    def __init__(self, path: Path):
        self.path = Path(path)

    # -- reading -----------------------------------------------------------

    def entries(self) -> list[LogEntry]:
        """Read every entry, in log order.

        Reads from disk each time rather than caching. The log is small (one
        short line per commit) and a stale cache in a provenance system is a
        far worse failure than re-reading a file.
        """
        if not self.path.is_file():
            return []

        found: list[LogEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                found.append(LogEntry.from_dict(json.loads(line)))
        return found

    def leaves(self) -> list[str]:
        """The leaf values, in log order: commit hashes."""
        return [entry.commit_hash for entry in self.entries()]

    def size(self) -> int:
        return len(self.entries())

    def contains(self, commit_hash: str) -> bool:
        return normalize_hash(commit_hash) in set(self.leaves())

    # -- the tree ----------------------------------------------------------

    def tree(self) -> MerkleTree:
        return MerkleTree(self.leaves())

    def root(self) -> str | None:
        """The current Merkle root, or None for an empty log."""
        return self.tree().get_root()

    def inclusion_proof(self, commit_hash: str) -> dict | None:
        """Everything a third party needs to verify inclusion, without the Hub.

        Returns the leaf, its sibling path, the root, and the log size. A
        verifier recomputes the root from the leaf and the path and compares it
        against the anchored root — the Hub is not consulted in that check.
        """
        digest = normalize_hash(commit_hash)
        tree = self.tree()
        proof = tree.get_proof(digest)

        if proof is None:
            return None

        return {
            "commit_hash": digest,
            "leaf_index": tree.raw_leaves.index(digest),
            "log_size": len(tree.raw_leaves),
            "root": tree.get_root(),
            "proof": [{"sibling": sibling, "side": side} for sibling, side in proof],
        }

    @staticmethod
    def verify(commit_hash: str, proof: list[dict], root: str) -> bool:
        """Verify a proof the way an external client would.

        Exposed here so the ops board and the tests exercise exactly the same
        code path a browser-side verifier will.

        A malformed step is False, not an exception: this runs on request bodies
        from anyone, and "this proof does not verify" is the honest answer to a
        proof that is not even shaped like one. Raising would turn junk input
        into a 500 and make the Hub look broken instead of the proof.
        """
        path = []
        for step in proof:
            if not isinstance(step, dict) or "sibling" not in step or "side" not in step:
                return False
            path.append((step["sibling"], step["side"]))

        return verify_proof(commit_hash, path, root)

    # -- appending (the only mutation in this module) -----------------------

    def append(self, commit_hash: str, repo: str, accepted_at: str) -> LogEntry:
        """Add one commit to the log. Idempotent.

        Re-pushing an already-logged commit returns its existing entry rather
        than adding a duplicate leaf: a push is a sync, and syncing twice must
        not change the root.
        """
        return self.append_many([commit_hash], repo=repo, accepted_at=accepted_at).entries[0]

    def append_many(self, commit_hashes: list[str], repo: str, accepted_at: str) -> "AppendResult":
        """Add a run of commits under a single lock, oldest first.

        A push appends a commit's whole ancestry, and doing that one `append`
        call at a time would re-read the entire log and re-take the lock for
        every ancestor -- quadratic in log size, for no benefit. One lock, one
        read, one write.

        Order is the caller's: ancestors must be passed before descendants so
        leaf indices follow history. The log itself does not know about parents,
        which is deliberate -- it records the order commits were *accepted*, and
        conflating that with lineage would make the log's meaning depend on the
        commit format it is logging.

        Held under a lock because read-then-append is a race. Two concurrent
        pushes could otherwise compute the same next index and one would
        silently overwrite the other's leaf.
        """
        digests = [normalize_hash(commit_hash) for commit_hash in commit_hashes]

        self.path.parent.mkdir(parents=True, exist_ok=True)

        with file_lock(self.path.with_suffix(".lock")):
            existing = self.entries()
            by_hash = {entry.commit_hash: entry for entry in existing}

            resolved: list[LogEntry] = []
            fresh: list[LogEntry] = []
            next_index = len(existing)

            for digest in digests:
                known = by_hash.get(digest)
                if known is not None:
                    resolved.append(known)
                    continue

                entry = LogEntry(
                    index=next_index,
                    commit_hash=digest,
                    repo=repo,
                    accepted_at=accepted_at,
                )
                next_index += 1

                # Recorded immediately so a batch containing the same commit
                # twice cannot produce two leaves for it.
                by_hash[digest] = entry
                resolved.append(entry)
                fresh.append(entry)

            if fresh:
                # Append rather than rewrite: the file only ever grows, so a
                # crash mid-write can lose trailing lines but never corrupt
                # earlier ones. One fsync covers the whole batch.
                with open(self.path, "a", encoding="utf-8") as handle:
                    for entry in fresh:
                        handle.write(entry.to_json() + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())

            return AppendResult(entries=resolved, added=[entry.index for entry in fresh])


class AnchorStore:
    """Records of Merkle roots published to a chain.

    Written by the anchoring job (not yet built) and read by the ops board, so
    "last anchored root" and "is the current root anchored?" are answerable
    from day one — with an honest "never anchored" while the chain layer is
    still pending.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def records(self) -> list[dict]:
        if not self.path.is_file():
            return []

        found: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                found.append(json.loads(line))
        return found

    def latest(self) -> dict | None:
        records = self.records()
        return records[-1] if records else None

    def append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
