"""HEAD and branch reference management.

Mirrors Git's model, which the original implementation already chose
correctly:

    HEAD                     "ref: refs/heads/main"  (attached)
                             "<64-hex commit hash>"  (detached)
    refs/heads/<branch>      a single commit hash, or empty when unborn

An empty branch file means the branch exists but has no commits yet -- Git's
"unborn branch" state, which is what a freshly initialized repository is in.

Ref writes go through the atomic layer and, for updates, a repository lock:
two concurrent commits reading the same branch tip would otherwise both
write, and the second would silently discard the first.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from aethel.core.atomic import atomic_write_text, file_lock
from aethel.core.errors import (
    BranchExists,
    BranchNotFound,
    DetachedHead,
    InvalidRef,
)
from aethel.core.hashing import is_valid_hash, normalize_hash

HEAD_PREFIX = "ref: "
HEADS_PREFIX = "refs/heads/"
DEFAULT_BRANCH = "main"

#: Branch names are restricted to characters that are safe as path segments
#: on every platform. Stricter than Git deliberately: a branch name becomes a
#: filename, and this closes off traversal and Windows-reserved-name issues.
BRANCH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

#: Reserved on Windows regardless of extension; a repo containing one of
#: these would be unclonable there.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


@dataclass(frozen=True)
class Head:
    """The state of HEAD.

    Exactly one of `branch` or `commit` is set: attached HEAD names a branch,
    detached HEAD names a commit directly.
    """

    branch: str | None
    commit: str | None

    @property
    def is_detached(self) -> bool:
        return self.branch is None


def validate_branch_name(name: str) -> str:
    """Return a validated branch name, or raise InvalidRef.

    Rejects path separators, traversal, whitespace, Git's `.lock` suffix, and
    Windows reserved device names.
    """
    candidate = name.strip()

    if not candidate:
        raise InvalidRef("Branch name cannot be empty.")

    if candidate in {".", ".."}:
        raise InvalidRef("Invalid branch name.")

    if "/" in candidate or "\\" in candidate:
        raise InvalidRef("Branch names cannot contain path separators.")

    if any(ch.isspace() for ch in candidate):
        raise InvalidRef("Branch names cannot contain whitespace.")

    if candidate.endswith(".lock"):
        raise InvalidRef("Branch names cannot end with '.lock'.")

    if candidate.split(".")[0].lower() in _WINDOWS_RESERVED:
        raise InvalidRef(f"'{candidate}' is a reserved name on Windows.")

    if not BRANCH_NAME_PATTERN.fullmatch(candidate):
        raise InvalidRef(
            "Invalid branch name. Use letters, digits, '.', '_' and '-'."
        )

    return candidate


class Refs:
    """Reads and writes HEAD and branch references."""

    def __init__(self, aethel_dir: Path):
        self.aethel_dir = Path(aethel_dir)

    # -- paths -------------------------------------------------------------

    @property
    def head_path(self) -> Path:
        return self.aethel_dir / "HEAD"

    @property
    def heads_dir(self) -> Path:
        return self.aethel_dir / "refs" / "heads"

    def branch_path(self, name: str) -> Path:
        """Resolve a branch ref path, refusing anything outside the repo.

        The containment check compares against `parent + os.sep` rather than a
        bare prefix. A plain `startswith` would accept a sibling directory
        whose name merely begins with the repo's -- the weaker form the old
        `branch.py:86` used, while `commit.py:97` got it right.
        """
        validated = validate_branch_name(name)

        heads_dir = self.heads_dir.resolve()
        candidate = (self.heads_dir / validated).resolve()

        if candidate.parent != heads_dir:
            raise InvalidRef(f"Branch reference '{name}' resolves outside refs/heads.")

        return self.heads_dir / validated

    # -- HEAD --------------------------------------------------------------

    def read_head(self) -> Head:
        """Parse HEAD into its attached or detached form."""
        if not self.head_path.is_file():
            raise InvalidRef("HEAD is missing. Re-run 'aethel init --model <repo>'.")

        content = self.head_path.read_text(encoding="utf-8").strip()

        if not content:
            raise InvalidRef("HEAD is empty.")

        if content.startswith(HEAD_PREFIX):
            ref = content[len(HEAD_PREFIX):].strip()

            if not ref.startswith(HEADS_PREFIX):
                raise InvalidRef(f"HEAD points to an unsupported reference: {ref!r}")

            branch = ref[len(HEADS_PREFIX):]
            return Head(branch=validate_branch_name(branch), commit=None)

        if is_valid_hash(content.lower()):
            return Head(branch=None, commit=content.lower())

        raise InvalidRef(
            "HEAD is invalid. Expected 'ref: refs/heads/<branch>' or a commit hash."
        )

    def resolve_head_commit(self) -> str | None:
        """Return the commit HEAD points at, or None on an unborn branch."""
        head = self.read_head()

        if head.is_detached:
            return head.commit

        return self.read_branch(head.branch)

    def require_attached_branch(self) -> str:
        """Return the current branch name, or raise DetachedHead.

        Committing on a detached HEAD would create an object no reference
        points at, which the next checkout would orphan. The original code
        blocked this too (commit.py:315-329) -- stricter than Git, which only
        warns -- and that behaviour is preserved.
        """
        head = self.read_head()

        if head.is_detached:
            raise DetachedHead(head.commit)

        return head.branch

    def set_head_to_branch(self, name: str) -> None:
        validated = validate_branch_name(name)
        atomic_write_text(self.head_path, f"{HEAD_PREFIX}{HEADS_PREFIX}{validated}\n")

    def set_head_detached(self, commit_hash: str) -> None:
        digest = normalize_hash(commit_hash, label="commit hash")
        atomic_write_text(self.head_path, f"{digest}\n")

    # -- branches ----------------------------------------------------------

    def branch_exists(self, name: str) -> bool:
        return self.branch_path(name).is_file()

    def read_branch(self, name: str) -> str | None:
        """Return a branch's commit hash, or None if the branch is unborn."""
        path = self.branch_path(name)

        if not path.is_file():
            raise BranchNotFound(f"Branch '{name}' does not exist.")

        value = path.read_text(encoding="utf-8").strip()

        if not value:
            return None

        return normalize_hash(value, label=f"branch '{name}' tip")

    def create_branch(self, name: str, commit_hash: str | None) -> None:
        """Create a branch. `commit_hash=None` creates an unborn branch."""
        path = self.branch_path(name)

        if path.exists():
            raise BranchExists(f"A branch named '{name}' already exists.")

        payload = "" if commit_hash is None else f"{normalize_hash(commit_hash)}\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, payload)

    def update_branch(self, name: str, commit_hash: str) -> None:
        """Advance a branch tip, serialized against concurrent writers."""
        path = self.branch_path(name)

        if not path.is_file():
            raise BranchNotFound(f"Branch '{name}' does not exist.")

        digest = normalize_hash(commit_hash, label="commit hash")

        with file_lock(self.aethel_dir / "refs.lock"):
            atomic_write_text(path, f"{digest}\n")

    def list_branches(self) -> list[str]:
        """Return every branch name, sorted."""
        if not self.heads_dir.is_dir():
            return []

        return sorted(
            entry.name
            for entry in self.heads_dir.iterdir()
            if entry.is_file() and not entry.name.startswith(".")
        )
