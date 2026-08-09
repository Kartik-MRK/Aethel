"""Tests for atomic writes and locking (aethel/core/atomic.py).

These cover defect S2.3: the original commit path had no atomicity, so an
interruption could leave a corrupt repository.
"""

import os
import threading
import time

import pytest

from aethel.core.atomic import (
    atomic_copy_file,
    atomic_write_bytes,
    atomic_write_text,
    file_lock,
)
from aethel.core.errors import LockTimeout


class TestAtomicWrite:
    def test_creates_a_file_with_the_given_bytes(self, tmp_path):
        target = tmp_path / "out.bin"
        atomic_write_bytes(target, b"hello")

        assert target.read_bytes() == b"hello"

    def test_creates_missing_parent_directories(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "out.bin"
        atomic_write_bytes(target, b"x")

        assert target.read_bytes() == b"x"

    def test_overwrites_an_existing_file(self, tmp_path):
        target = tmp_path / "out.bin"
        target.write_bytes(b"old content that is long")
        atomic_write_bytes(target, b"new")

        assert target.read_bytes() == b"new"

    def test_text_is_written_as_utf8(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write_text(target, "café ☕")

        assert target.read_bytes() == "café ☕".encode()

    def test_leaves_no_temporary_files_behind(self, tmp_path):
        atomic_write_bytes(tmp_path / "out.bin", b"data")

        assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]

    def test_failure_mid_write_leaves_the_original_intact(self, tmp_path, monkeypatch):
        """The core atomicity guarantee.

        Simulates a crash between opening the temp file and the rename. The
        original file must be untouched -- never truncated or partially
        overwritten.
        """
        target = tmp_path / "out.bin"
        target.write_bytes(b"ORIGINAL")

        def exploding_replace(*_args, **_kwargs):
            raise OSError("simulated crash before rename")

        monkeypatch.setattr(os, "replace", exploding_replace)

        with pytest.raises(OSError):
            atomic_write_bytes(target, b"NEW DATA")

        assert target.read_bytes() == b"ORIGINAL"

    def test_failure_mid_write_cleans_up_the_temp_file(self, tmp_path, monkeypatch):
        target = tmp_path / "out.bin"
        target.write_bytes(b"ORIGINAL")

        monkeypatch.setattr(
            os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
        )

        with pytest.raises(OSError):
            atomic_write_bytes(target, b"NEW")

        assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]

    def test_temp_file_is_created_beside_the_target(self, tmp_path, monkeypatch):
        """os.replace is only atomic within one filesystem.

        Putting the temp file in /tmp would break atomicity whenever the repo
        lives on a different mount, so it must be a sibling of the target.
        """
        seen: list[str] = []
        real_replace = os.replace

        def recording_replace(src, dst):
            seen.append(str(src))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", recording_replace)

        target = tmp_path / "sub" / "out.bin"
        atomic_write_bytes(target, b"data")

        assert os.path.dirname(seen[0]) == str(target.parent)

    def test_durable_false_still_writes_correctly(self, tmp_path):
        target = tmp_path / "cache.bin"
        atomic_write_bytes(target, b"derived", durable=False)

        assert target.read_bytes() == b"derived"


class TestAtomicCopy:
    def test_copies_file_content(self, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"payload" * 100)
        dst = tmp_path / "objects" / "dst.bin"

        atomic_copy_file(src, dst)

        assert dst.read_bytes() == src.read_bytes()

    def test_copies_content_spanning_multiple_chunks(self, tmp_path):
        src = tmp_path / "big.bin"
        src.write_bytes(bytes(range(256)) * 8192)  # 2 MiB
        dst = tmp_path / "dst.bin"

        atomic_copy_file(src, dst)

        assert dst.read_bytes() == src.read_bytes()

    def test_copy_failure_leaves_no_partial_destination(self, tmp_path, monkeypatch):
        src = tmp_path / "src.bin"
        src.write_bytes(b"payload")
        dst = tmp_path / "dst.bin"

        monkeypatch.setattr(
            os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
        )

        with pytest.raises(OSError):
            atomic_copy_file(src, dst)

        assert not dst.exists()
        assert [p.name for p in tmp_path.iterdir()] == ["src.bin"]


class TestFileLock:
    def test_creates_and_removes_the_lock_file(self, tmp_path):
        lock = tmp_path / "refs.lock"

        with file_lock(lock):
            assert lock.exists()

        assert not lock.exists()

    def test_lock_is_released_when_the_body_raises(self, tmp_path):
        lock = tmp_path / "refs.lock"

        with pytest.raises(ValueError):  # noqa: SIM117 - the nesting is the point
            with file_lock(lock):
                raise ValueError("failure inside the critical section")

        assert not lock.exists()

    def test_second_holder_times_out(self, tmp_path):
        lock = tmp_path / "refs.lock"

        with file_lock(lock), pytest.raises(LockTimeout), file_lock(lock, timeout=0.1):
            pass

    def test_timeout_message_names_the_file_to_remove(self, tmp_path):
        lock = tmp_path / "refs.lock"

        with (
            file_lock(lock),
            pytest.raises(LockTimeout, match="refs.lock"),
            file_lock(lock, timeout=0.05),
        ):
            pass

    def test_waiter_acquires_after_the_holder_releases(self, tmp_path):
        """The lock must block, not fail, under normal contention."""
        lock = tmp_path / "refs.lock"
        acquired = threading.Event()

        def hold_briefly():
            with file_lock(lock):
                time.sleep(0.15)

        holder = threading.Thread(target=hold_briefly)
        holder.start()
        time.sleep(0.02)  # let the holder win the race

        with file_lock(lock, timeout=5.0):
            acquired.set()

        holder.join()
        assert acquired.is_set()

    def test_serializes_concurrent_writers(self, tmp_path):
        """Two threads incrementing a counter must not lose an update.

        This is the ref-update race the lock exists to prevent: both readers
        see the same tip, and one write silently overwrites the other.
        """
        lock = tmp_path / "counter.lock"
        counter = tmp_path / "counter.txt"
        counter.write_text("0")

        def increment():
            for _ in range(25):
                with file_lock(lock, timeout=10.0):
                    value = int(counter.read_text())
                    time.sleep(0.0005)  # widen the read-modify-write window
                    counter.write_text(str(value + 1))

        threads = [threading.Thread(target=increment) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert int(counter.read_text()) == 100
