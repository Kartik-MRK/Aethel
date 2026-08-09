"""Exception hierarchy for the Aethel core.

Every failure the core raises is an AethelError, so CLI commands can catch
one type at the boundary and render it, while still discriminating on the
specific subclass when the recovery advice differs.

The core never prints and never calls sys.exit -- presentation belongs to the
command layer. That separation is what makes the core testable without a
terminal.
"""


class AethelError(Exception):
    """Base class for all Aethel core failures."""


class RepositoryNotFound(AethelError):
    """No .aethel directory was found at or above the starting path."""


class RepositoryCorrupt(AethelError):
    """The repository exists but its metadata is unusable."""


class ObjectNotFound(AethelError):
    """A requested object is absent from the object store."""


class CorruptObject(AethelError):
    """An object's content does not hash to the name it is stored under.

    This means on-disk corruption or tampering: object names ARE their
    content hashes, so a mismatch is never merely a stale cache.
    """


class InvalidHash(AethelError):
    """A value was expected to be a 64-character hex SHA-256 digest."""


class InvalidRef(AethelError):
    """A reference is malformed, missing, or points outside the repository."""


class DetachedHead(AethelError):
    """HEAD points directly at a commit rather than at a branch.

    Carries the commit hash so the command layer can print recovery steps.
    """

    def __init__(self, commit_hash: str):
        self.commit_hash = commit_hash
        super().__init__(f"HEAD is detached at {commit_hash}")


class BranchExists(AethelError):
    """A branch of that name already exists."""


class BranchNotFound(AethelError):
    """No branch of that name exists."""


class LockTimeout(AethelError):
    """A repository lock could not be acquired before the deadline."""
