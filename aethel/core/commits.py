"""Commit creation and history traversal.

A commit is an immutable JSON object keyed by its own hash::

    {
      "schema": 2,
      "parent_hash": "<hash>" | null,
      "tree": "<tree hash>",          full workspace snapshot
      "adapter_blob": "<blob hash>",  the weights specifically
      "base": "<base object hash>",   base model reference
      "message": "...",
      "author": "...",
      "timestamp": "ISO-8601",
      "training_info": {...}
    }

Three deliberate differences from the original implementation:

1. **Keyed by its own hash**, so identical weights can never make two commits
   share a metadata slot (defect S1.1).
2. **`tree` records the whole workspace**, not just the weights file, so a
   checkout can restore `adapter_config.json` and `training_info.json` from
   content-addressed storage rather than from whatever happens to be lying
   around on disk.
3. **The workspace is never touched.** The old code deleted the workspace and
   copied files back (commit.py:184-194) purely so it would not be left
   empty -- a dangerous no-op, since it destroyed and restored the user's
   staged files on every commit. Storage is a read of the workspace; there is
   no reason to write to it.
"""

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from aethel.core.errors import AethelError, CorruptObject, InvalidRef, ObjectNotFound
from aethel.core.hashing import is_valid_hash, normalize_hash
from aethel.core.repo import Repo

COMMIT_SCHEMA = 2

#: Adapter weight filenames, in preference order. safetensors first: it is the
#: safe format, whereas .bin is a pickle and executes arbitrary code on load.
ADAPTER_FILENAMES = ("adapter_model.safetensors", "adapter_model.bin")


def build_base_object(model_id: str, revision_sha: str) -> dict:
    """Describe the base model by reference, never by value.

    Base weights are deliberately NOT stored. distilbert is ~250 MB and is
    already immutably addressable on the Hub by commit SHA, so copying it into
    every repository would add hundreds of megabytes and buy nothing. Pinning
    the revision SHA -- not the tag -- is what makes this reproducible: tags
    move, SHAs do not.
    """
    return {
        "schema": COMMIT_SCHEMA,
        "source": "huggingface",
        "model_id": model_id,
        "revision_sha": revision_sha,
    }


def find_adapter_filename(files: dict[str, str]) -> str:
    """Return the adapter weights filename present in a tree."""
    for candidate in ADAPTER_FILENAMES:
        if candidate in files:
            return candidate

    raise ObjectNotFound(
        f"No adapter weights found. Expected one of: {', '.join(ADAPTER_FILENAMES)}"
    )


def require_staged_adapter(workspace: Path) -> None:
    """Fail before writing anything if no adapter is staged.

    Checked up front so an empty workspace -- much the most likely user error,
    running `commit` before `train` -- does not leave unreferenced blobs
    behind on the way to an error.
    """
    if not workspace.is_dir():
        raise ObjectNotFound(
            f"Workspace {workspace} does not exist. Run 'aethel train --config <yaml>' first."
        )

    if not any((workspace / name).is_file() for name in ADAPTER_FILENAMES):
        raise ObjectNotFound(
            f"No adapter weights in {workspace}. "
            f"Expected one of: {', '.join(ADAPTER_FILENAMES)}. "
            f"Run 'aethel train --config <yaml>' first."
        )


def create_commit(
    repo: Repo,
    *,
    message: str,
    author: str,
    base_hash: str,
    training_info: dict,
    timestamp: str | None = None,
    workspace: Path | None = None,
) -> str:
    """Snapshot the workspace as a new commit and advance the current branch.

    Raises DetachedHead when HEAD is not on a branch, before writing anything.

    `timestamp` is injectable so tests can produce deterministic commit
    hashes; production callers leave it None and get UTC now.

    Ordering matters. Objects are written first and the ref is advanced last,
    because objects are immutable and self-verifying: an interrupted commit
    leaves unreferenced objects, which are harmless garbage that `aethel fsck`
    reports. Advancing the ref first would instead leave a branch pointing at
    a commit whose objects do not exist -- an unrecoverable repository.
    """
    branch = repo.refs.require_attached_branch()

    workspace = Path(workspace) if workspace is not None else repo.workspace_dir
    require_staged_adapter(workspace)

    tree_hash = repo.objects.write_tree_from_directory(workspace)
    files = repo.objects.read_tree(tree_hash)
    adapter_blob = files[find_adapter_filename(files)]

    payload = {
        "schema": COMMIT_SCHEMA,
        "parent_hash": repo.refs.read_branch(branch),
        "tree": tree_hash,
        "adapter_blob": adapter_blob,
        "base": normalize_hash(base_hash, label="base hash"),
        "message": message,
        "author": author,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "training_info": training_info,
    }

    commit_hash = repo.objects.write_json("commits", payload)
    repo.refs.update_branch(branch, commit_hash)

    return commit_hash


def read_commit(repo: Repo, commit_hash: str) -> dict:
    """Load a commit object, verified against its own hash."""
    return repo.objects.read_json("commits", commit_hash)


def walk_history(repo: Repo, start_hash: str | None) -> Iterator[tuple[str, dict]]:
    """Yield ``(hash, commit)`` from a commit back to the root.

    Traverses the object store only -- no SQLite involved -- so history
    survives losing the index. Guards against a cycle, which content
    addressing makes effectively impossible (a commit would have to contain
    its own hash) but which a hand-edited repository could still fake.
    """
    if start_hash is None:
        return

    current = normalize_hash(start_hash, label="commit hash")
    seen: set[str] = set()

    while current is not None:
        if current in seen:
            raise CorruptObject(f"Cycle detected in commit history at {current[:12]}")
        seen.add(current)

        commit = read_commit(repo, current)
        yield current, commit

        parent = commit.get("parent_hash")
        current = normalize_hash(parent, label="parent hash") if parent else None


def resolve_commitish(repo: Repo, target: str) -> tuple[str, str]:
    """Resolve a user-supplied reference to ``(kind, commit_hash)``.

    `kind` is "branch" or "commit". Accepts, in priority order:
      1. a branch name
      2. a full 64-character commit hash
      3. a unique abbreviated commit hash (>= 4 characters)

    Branch names win over hashes, matching Git. Abbreviations resolve by
    scanning the object store, so this works with no index present.
    """
    stripped = target.strip()

    if not stripped:
        raise InvalidRef("Empty reference.")

    # 1. Branch
    try:
        if repo.refs.branch_exists(stripped):
            tip = repo.refs.read_branch(stripped)
            if tip is None:
                raise InvalidRef(
                    f"Branch '{stripped}' has no commits yet. Make your first commit."
                )
            return "branch", tip
    except InvalidRef:
        raise
    except AethelError:
        # Not a syntactically valid branch name; fall through to hash lookup.
        pass

    candidate = stripped.lower()

    # 2. Full hash
    if is_valid_hash(candidate):
        if not repo.objects.exists("commits", candidate):
            raise ObjectNotFound(f"No commit object {candidate[:12]} in the object store.")
        return "commit", candidate

    # 3. Unique abbreviation
    if len(candidate) >= 4 and all(c in "0123456789abcdef" for c in candidate):
        matches = [h for h in repo.objects.iter_hashes("commits") if h.startswith(candidate)]

        if len(matches) == 1:
            return "commit", matches[0]

        if len(matches) > 1:
            preview = ", ".join(h[:12] for h in sorted(matches)[:5])
            raise InvalidRef(
                f"'{stripped}' is ambiguous — matches {len(matches)} commits: {preview}"
            )

    raise InvalidRef(f"'{stripped}' did not match any branch or commit.")
