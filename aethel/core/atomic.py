"""Crash-safe file writes and repository locking.

The original implementation performed five mutating steps per commit -- store
folder, write commit.json, insert into SQLite, advance the branch ref, restore
the workspace -- with no transaction, no fsync and no rollback. Interrupting
it anywhere left a corrupt or orphaned repository (defect S2.3).

Every durable write now goes through `atomic_write_*`, which guarantees a
reader sees either the complete old file or the complete new one, never a
truncated blend.

The sequence, and why each step is load-bearing:

1. Write to a temporary file **in the same directory**. `os.replace` is only
   atomic within a single filesystem, and /tmp is frequently a different one.
2. `fsync` the temp file. Without it the rename can reach disk before the
   data, so a crash yields a correctly-named, empty or partial file.
3. `os.replace` -- atomic on POSIX and on Windows, unlike `os.rename`, which
   fails on Windows when the destination exists.
4. `fsync` the parent directory, so the rename itself survives a power loss.
"""

import errno
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from aethel.core.errors import LockTimeout


def _fsync_directory(directory: Path) -> None:
    """Flush a directory entry so a rename is durable.

    POSIX only. Windows has no way to open a directory as a file descriptor
    and does not need this, so failures here are ignored rather than raised.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except (OSError, ValueError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path | str, data: bytes, *, durable: bool = True) -> None:
    """Write bytes so that readers never observe a partial file.

    Set ``durable=False`` to skip the fsync calls. That is appropriate only
    for content that can be regenerated -- the derived SQLite cache, for
    instance -- where throughput matters more than surviving a power cut.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # The PID keeps concurrent writers from colliding on the temp name.
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")

    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())

        os.replace(tmp_path, path)

        if durable:
            _fsync_directory(path.parent)
    except BaseException:
        # Never leave a temp file behind, including on KeyboardInterrupt.
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path | str, text: str, *, durable: bool = True) -> None:
    """Write UTF-8 text atomically. See `atomic_write_bytes`."""
    atomic_write_bytes(path, text.encode("utf-8"), durable=durable)


def atomic_copy_file(src: Path | str, dst: Path | str, *, durable: bool = True) -> None:
    """Copy a file into the object store atomically, streaming the content.

    Streamed rather than read-then-write so that adapter files stay off the
    heap regardless of size.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")

    try:
        with open(src, "rb") as reader, open(tmp_path, "wb") as writer:
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
            if durable:
                writer.flush()
                os.fsync(writer.fileno())

        os.replace(tmp_path, dst)

        if durable:
            _fsync_directory(dst.parent)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


@contextmanager
def file_lock(
    lock_path: Path | str,
    *,
    timeout: float = 10.0,
    poll_interval: float = 0.05,
) -> Iterator[None]:
    """Hold an exclusive repository lock for the duration of the block.

    Implemented with ``O_CREAT | O_EXCL``, which is atomic on every platform
    we target: exactly one process can create the file, and the loser retries.

    Used to serialize ref updates. Without it, two concurrent commits can both
    read the same branch tip and the second silently discards the first.

    A crashed process leaves the lock file behind. That is deliberate -- the
    same trade-off Git makes with ``index.lock``. Automatically clearing a
    lock whose owner might still be alive is far more dangerous than asking a
    human to remove a stale file, so the error names the path to delete.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + timeout
    fd = None

    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"Could not acquire lock at {lock_path} within {timeout:g}s. "
                    f"If no other aethel process is running, remove the file."
                ) from exc
            time.sleep(poll_interval)

    try:
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            os.close(fd)
        lock_path.unlink(missing_ok=True)
