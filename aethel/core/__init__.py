"""Aethel core: content-addressed storage, references, and repository layout.

This package is deliberately free of torch, transformers, and any network
access. It is pure stdlib, which means:

  - the whole VCS layer is testable without a GPU or a model download
  - CI runs in seconds
  - `pip install aethel` yields a working repository tool; ML is the [ml] extra

Nothing here prints or exits. Errors are raised as `AethelError` subclasses
and rendered by the command layer.
"""

from aethel.core.commits import (
    COMMIT_SCHEMA,
    build_base_object,
    create_commit,
    read_commit,
    walk_history,
)
from aethel.core.errors import (
    AethelError,
    BranchExists,
    BranchNotFound,
    CorruptObject,
    DetachedHead,
    InvalidHash,
    InvalidRef,
    LockTimeout,
    ObjectNotFound,
    RepositoryCorrupt,
    RepositoryNotFound,
)
from aethel.core.hashing import (
    canonical_json,
    hash_bytes,
    hash_file,
    hash_json,
    hash_text,
    is_valid_hash,
    normalize_hash,
)
from aethel.core.objects import OBJECT_KINDS, ObjectStore
from aethel.core.refs import DEFAULT_BRANCH, Head, Refs, validate_branch_name
from aethel.core.repo import SCHEMA_VERSION, Repo

__all__ = [
    "AethelError",
    "BranchExists",
    "BranchNotFound",
    "CorruptObject",
    "DetachedHead",
    "InvalidHash",
    "InvalidRef",
    "LockTimeout",
    "ObjectNotFound",
    "RepositoryCorrupt",
    "RepositoryNotFound",
    "canonical_json",
    "hash_bytes",
    "hash_file",
    "hash_json",
    "hash_text",
    "is_valid_hash",
    "normalize_hash",
    "OBJECT_KINDS",
    "ObjectStore",
    "DEFAULT_BRANCH",
    "Head",
    "Refs",
    "validate_branch_name",
    "Repo",
    "SCHEMA_VERSION",
    "COMMIT_SCHEMA",
    "build_base_object",
    "create_commit",
    "read_commit",
    "walk_history",
]
