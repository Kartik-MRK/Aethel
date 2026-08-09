"""Content-addressed object store.

Four object kinds, each stored under its own hash:

    objects/blobs/<ab>/<rest>     raw bytes (adapter weights, configs)
    objects/trees/<ab>/<rest>     manifest: filename -> blob hash
    objects/commits/<ab>/<rest>   commit metadata
    objects/bases/<ab>/<rest>     base-model references

THE FIX FOR S1.1
----------------
The old layout stored commit metadata *inside the adapter's folder*:

    objects/<adapter_hash>/commit.json

Adapter hashes deduplicate by design, so two commits with byte-identical
weights resolved to the same path and the second silently overwrote the
first. Checking out the first commit then returned the second's message,
author and parent.

Here, a commit is keyed by the hash of the commit itself. Two commits with
identical weights share one blob -- deduplication is preserved -- while their
metadata lives at two distinct, non-colliding paths. The defect has no
representable form in this layout; it is not merely guarded against.

Names are sharded on the first two hex characters, as Git does, so no single
directory accumulates tens of thousands of entries.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from aethel.core.atomic import atomic_copy_file, atomic_write_bytes
from aethel.core.errors import CorruptObject, ObjectNotFound
from aethel.core.hashing import (
    canonical_json,
    hash_bytes,
    hash_file,
    hash_text,
    normalize_hash,
)

ObjectKind = Literal["blobs", "trees", "commits", "bases"]

OBJECT_KINDS: tuple[ObjectKind, ...] = ("blobs", "trees", "commits", "bases")

#: Files excluded when content-addressing a workspace directory. These are
#: trainer scratch output, not model artifacts.
DEFAULT_TREE_EXCLUDES = frozenset({"trainer_output", ".DS_Store", "commit.json"})


class ObjectStore:
    """Reads and writes immutable, content-addressed objects."""

    def __init__(self, objects_dir: Path):
        self.root = Path(objects_dir)

    # -- paths ------------------------------------------------------------

    def path_for(self, kind: ObjectKind, object_hash: str) -> Path:
        """Return the on-disk path for an object, without touching the disk."""
        digest = normalize_hash(object_hash, label=f"{kind[:-1]} hash")
        return self.root / kind / digest[:2] / digest[2:]

    def exists(self, kind: ObjectKind, object_hash: str) -> bool:
        return self.path_for(kind, object_hash).is_file()

    def iter_hashes(self, kind: ObjectKind) -> Iterator[str]:
        """Yield every stored hash of a given kind.

        Reconstructs the hash from the shard directory plus filename, so it
        reports what is actually on disk rather than what an index claims.
        """
        kind_dir = self.root / kind
        if not kind_dir.is_dir():
            return

        for shard in sorted(kind_dir.iterdir()):
            if not shard.is_dir() or len(shard.name) != 2:
                continue
            for entry in sorted(shard.iterdir()):
                if entry.is_file() and not entry.name.startswith("."):
                    yield shard.name + entry.name

    # -- blobs ------------------------------------------------------------

    def write_blob(self, data: bytes) -> str:
        """Store raw bytes, returning the content hash.

        Idempotent: an existing blob is left untouched. Since the name is the
        content hash, a present file already holds exactly these bytes, and
        rewriting it would only risk turning a good object into a torn one.
        """
        digest = hash_bytes(data)
        destination = self.path_for("blobs", digest)

        if not destination.is_file():
            atomic_write_bytes(destination, data)

        return digest

    def write_blob_from_file(self, source: Path | str) -> str:
        """Store a file's contents as a blob, streaming it.

        Hashes first, then copies only on a miss, so re-committing unchanged
        weights costs one read and no write.
        """
        source = Path(source)
        digest = hash_file(source)
        destination = self.path_for("blobs", digest)

        if not destination.is_file():
            atomic_copy_file(source, destination)

        return digest

    def read_blob(self, blob_hash: str) -> bytes:
        path = self._require("blobs", blob_hash)
        return path.read_bytes()

    def blob_path(self, blob_hash: str) -> Path:
        """Path to a stored blob, for streaming copies out of the store."""
        return self._require("blobs", blob_hash)

    # -- structured objects -----------------------------------------------

    def write_json(self, kind: ObjectKind, payload: Any) -> str:
        """Store a structured object under the hash of its canonical form."""
        serialized = canonical_json(payload)
        digest = hash_text(serialized)
        destination = self.path_for(kind, digest)

        if not destination.is_file():
            atomic_write_bytes(destination, serialized.encode("utf-8"))

        return digest

    def read_json(self, kind: ObjectKind, object_hash: str) -> dict:
        """Load a structured object, verifying it against its own name.

        Verification happens on every read, not only during `fsck`. The cost
        is one hash of a small file; the benefit is that silent bit-rot or
        tampering surfaces at the moment the data is used.
        """
        import json

        path = self._require(kind, object_hash)
        raw = path.read_bytes()

        expected = normalize_hash(object_hash)
        actual = hash_bytes(raw)
        if actual != expected:
            raise CorruptObject(
                f"{kind[:-1]} object {expected[:12]} hashes to {actual[:12]}. "
                f"Content does not match its name: {path}"
            )

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CorruptObject(f"{kind[:-1]} object {expected[:12]} is not valid JSON: {exc}") from exc

        if not isinstance(decoded, dict):
            raise CorruptObject(f"{kind[:-1]} object {expected[:12]} is not a JSON object")

        return decoded

    # -- trees -------------------------------------------------------------

    def write_tree_from_directory(
        self, directory: Path | str, *, excludes: frozenset[str] = DEFAULT_TREE_EXCLUDES
    ) -> str:
        """Store every file in a directory as blobs, then record a manifest.

        Returns the tree hash. Because the manifest is a sorted mapping of
        filename to blob hash, two directories with identical contents always
        produce the same tree hash regardless of filesystem ordering.
        """
        directory = Path(directory)
        files: dict[str, str] = {}

        for entry in sorted(directory.iterdir()):
            if entry.name in excludes or entry.name.startswith("."):
                continue
            if entry.is_file():
                files[entry.name] = self.write_blob_from_file(entry)

        return self.write_json("trees", {"files": files})

    def read_tree(self, tree_hash: str) -> dict[str, str]:
        """Return a tree's ``filename -> blob hash`` mapping."""
        payload = self.read_json("trees", tree_hash)
        files = payload.get("files")

        if not isinstance(files, dict):
            raise CorruptObject(f"tree {tree_hash[:12]} has no valid 'files' mapping")

        return files

    def extract_tree(self, tree_hash: str, destination: Path | str) -> list[str]:
        """Materialize a tree's files into a directory. Returns filenames."""
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)

        written: list[str] = []
        for name, blob_hash in sorted(self.read_tree(tree_hash).items()):
            atomic_copy_file(self.blob_path(blob_hash), destination / name)
            written.append(name)

        return written

    # -- integrity ---------------------------------------------------------

    def verify(self, kind: ObjectKind, object_hash: str) -> bool:
        """True if the stored object still hashes to its own name."""
        path = self.path_for(kind, object_hash)
        if not path.is_file():
            return False
        return hash_file(path) == normalize_hash(object_hash)

    # -- internals ---------------------------------------------------------

    def _require(self, kind: ObjectKind, object_hash: str) -> Path:
        path = self.path_for(kind, object_hash)
        if not path.is_file():
            raise ObjectNotFound(
                f"{kind[:-1]} object {normalize_hash(object_hash)[:12]} "
                f"is missing from the object store"
            )
        return path

