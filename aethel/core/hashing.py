"""Hashing and canonical serialization.

Two rules hold everywhere in Aethel:

1. An object's name IS the SHA-256 of its content. Storage is content
   addressed, so a name/content mismatch always means corruption.
2. Structured objects are serialized canonically before hashing, so the same
   logical object always produces the same hash.

Ported from the original implementation (commands/commit.py:302), which
already got the canonical form right: sort_keys plus compact separators.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from aethel.core.errors import InvalidHash

#: A SHA-256 digest rendered as 64 lowercase hex characters.
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: Read size for streaming file hashes. Large enough to keep syscall overhead
#: low, small enough that a multi-GB file never lands in memory.
_CHUNK_SIZE = 1024 * 1024


def canonical_json(obj: Any) -> str:
    """Serialize to a canonical JSON string suitable for hashing.

    Canonical means: identical logical content always yields an identical
    string, regardless of key insertion order.

    - ``sort_keys=True``     -- key order cannot change the hash
    - ``separators``         -- no incidental whitespace
    - ``ensure_ascii=False`` -- emit real UTF-8 rather than \\uXXXX escapes
    - ``allow_nan=False``    -- NaN/Infinity are not valid JSON; reject them
      instead of emitting output no other parser will accept

    ``ensure_ascii=False`` is a deliberate forward-looking choice: the
    verification page will re-derive hashes in the browser, and JavaScript's
    ``JSON.stringify`` does not escape non-ASCII either. Matching that now
    avoids a cross-language hash mismatch later.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def hash_bytes(data: bytes) -> str:
    """Return the SHA-256 of raw bytes as lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    """Return the SHA-256 of a string, encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_json(obj: Any) -> str:
    """Return the SHA-256 of an object's canonical JSON form."""
    return hash_text(canonical_json(obj))


def hash_file(path: Path | str) -> str:
    """Return the SHA-256 of a file, streamed rather than read whole."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def is_valid_hash(value: str) -> bool:
    """True if the value is a 64-character lowercase hex digest."""
    return bool(HASH_PATTERN.fullmatch(value))


def normalize_hash(value: str, *, label: str = "hash") -> str:
    """Lowercase and validate a hash, or raise InvalidHash.

    Accepts mixed case for convenience at CLI boundaries, but everything
    stored on disk is lowercase so that a hash has exactly one spelling.
    """
    if not isinstance(value, str):
        raise InvalidHash(f"{label} must be a string, got {type(value).__name__}")

    candidate = value.strip().lower()
    if not is_valid_hash(candidate):
        raise InvalidHash(
            f"{label} must be a 64-character hex SHA-256 digest, got {value!r}"
        )
    return candidate
