"""Tests for the content-addressed object store (aethel/core/objects.py)."""

import pytest

from aethel.core.errors import CorruptObject, InvalidHash, ObjectNotFound
from aethel.core.hashing import hash_bytes, hash_json
from aethel.core.objects import ObjectStore


@pytest.fixture
def store(tmp_path):
    return ObjectStore(tmp_path / "objects")


class TestBlobs:
    def test_write_returns_the_content_hash(self, store):
        assert store.write_blob(b"hello") == hash_bytes(b"hello")

    def test_round_trips_content(self, store):
        digest = store.write_blob(b"adapter weights")

        assert store.read_blob(digest) == b"adapter weights"

    def test_identical_content_deduplicates(self, store):
        """Writing the same bytes twice must not create a second object."""
        first = store.write_blob(b"same")
        second = store.write_blob(b"same")

        assert first == second
        assert len(list(store.iter_hashes("blobs"))) == 1

    def test_different_content_produces_different_objects(self, store):
        store.write_blob(b"one")
        store.write_blob(b"two")

        assert len(list(store.iter_hashes("blobs"))) == 2

    def test_writing_from_a_file_matches_writing_bytes(self, store, tmp_path):
        payload = b"weights" * 5000
        source = tmp_path / "adapter.safetensors"
        source.write_bytes(payload)

        assert store.write_blob_from_file(source) == hash_bytes(payload)

    def test_rewriting_does_not_disturb_the_stored_object(self, store):
        """Re-committing unchanged weights must be a no-op, not a rewrite.

        Rewriting an existing object can only turn a good file into a torn
        one -- the name already proves the content.
        """
        digest = store.write_blob(b"stable")
        before = store.path_for("blobs", digest).stat().st_mtime_ns

        store.write_blob(b"stable")

        assert store.path_for("blobs", digest).stat().st_mtime_ns == before

    def test_reading_a_missing_blob_raises(self, store):
        with pytest.raises(ObjectNotFound):
            store.read_blob("a" * 64)

    def test_invalid_hash_is_rejected(self, store):
        with pytest.raises(InvalidHash):
            store.read_blob("not-a-hash")


class TestSharding:
    def test_objects_shard_on_the_first_two_characters(self, store):
        digest = store.write_blob(b"payload")
        path = store.path_for("blobs", digest)

        assert path.parent.name == digest[:2]
        assert path.name == digest[2:]

    def test_iter_hashes_reconstructs_full_hashes(self, store):
        written = {store.write_blob(f"blob-{i}".encode()) for i in range(20)}

        assert set(store.iter_hashes("blobs")) == written

    def test_iter_hashes_is_empty_for_a_fresh_store(self, store):
        assert list(store.iter_hashes("commits")) == []


class TestStructuredObjects:
    def test_write_returns_the_canonical_hash(self, store):
        payload = {"message": "hello", "author": "sathwik"}

        assert store.write_json("commits", payload) == hash_json(payload)

    def test_round_trips_a_payload(self, store):
        payload = {"a": 1, "nested": {"b": [1, 2, 3]}}
        digest = store.write_json("commits", payload)

        assert store.read_json("commits", digest) == payload

    def test_key_order_does_not_create_a_second_object(self, store):
        first = store.write_json("commits", {"a": 1, "b": 2})
        second = store.write_json("commits", {"b": 2, "a": 1})

        assert first == second
        assert len(list(store.iter_hashes("commits"))) == 1

    def test_kinds_are_stored_independently(self, store):
        """A commit and a tree with identical content must not collide."""
        payload = {"same": "content"}
        store.write_json("commits", payload)
        store.write_json("trees", payload)

        assert len(list(store.iter_hashes("commits"))) == 1
        assert len(list(store.iter_hashes("trees"))) == 1

    def test_reading_a_missing_object_raises(self, store):
        with pytest.raises(ObjectNotFound):
            store.read_json("commits", "b" * 64)


class TestIntegrity:
    def test_tampering_is_detected_on_read(self, store):
        """Every read verifies, not just fsck.

        Content addressing makes this cheap and absolute: the name IS the
        hash, so any mismatch is corruption or tampering.
        """
        digest = store.write_json("commits", {"message": "original"})
        path = store.path_for("commits", digest)

        path.write_text('{"message":"tampered"}', encoding="utf-8")

        with pytest.raises(CorruptObject):
            store.read_json("commits", digest)

    def test_single_flipped_byte_is_detected(self, store):
        digest = store.write_blob(b"important adapter weights")
        path = store.path_for("blobs", digest)

        data = bytearray(path.read_bytes())
        data[0] ^= 0x01
        path.write_bytes(bytes(data))

        assert store.verify("blobs", digest) is False

    def test_verify_passes_for_an_untouched_object(self, store):
        digest = store.write_blob(b"healthy")

        assert store.verify("blobs", digest) is True

    def test_verify_is_false_for_a_missing_object(self, store):
        assert store.verify("blobs", "c" * 64) is False

    def test_non_json_content_is_reported_as_corrupt(self, store, tmp_path):
        """A hash-valid but unparseable object must not raise JSONDecodeError.

        Callers catch AethelError; leaking a stdlib exception would escape the
        command layer's error handling and print a traceback.
        """
        raw = b"not json at all"
        digest = hash_bytes(raw)
        path = store.path_for("commits", digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

        with pytest.raises(CorruptObject):
            store.read_json("commits", digest)

    def test_json_array_is_rejected(self, store):
        raw = b"[1,2,3]"
        digest = hash_bytes(raw)
        path = store.path_for("commits", digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

        with pytest.raises(CorruptObject):
            store.read_json("commits", digest)


class TestTrees:
    def test_records_every_file_in_a_directory(self, store, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "adapter_model.safetensors").write_bytes(b"weights")
        (workspace / "adapter_config.json").write_text('{"r":8}')

        files = store.read_tree(store.write_tree_from_directory(workspace))

        assert set(files) == {"adapter_model.safetensors", "adapter_config.json"}

    def test_identical_directories_produce_identical_tree_hashes(self, store, tmp_path):
        """Makes a whole workspace content-addressable, not just one file."""
        hashes = []
        for name in ("first", "second"):
            workspace = tmp_path / name
            workspace.mkdir()
            (workspace / "a.bin").write_bytes(b"aaa")
            (workspace / "b.bin").write_bytes(b"bbb")
            hashes.append(store.write_tree_from_directory(workspace))

        assert hashes[0] == hashes[1]

    def test_differing_content_changes_the_tree_hash(self, store, tmp_path):
        hashes = []
        for name, payload in (("first", b"aaa"), ("second", b"zzz")):
            workspace = tmp_path / name
            workspace.mkdir()
            (workspace / "a.bin").write_bytes(payload)
            hashes.append(store.write_tree_from_directory(workspace))

        assert hashes[0] != hashes[1]

    def test_trainer_output_is_excluded(self, store, tmp_path):
        """Trainer scratch is not a model artifact and must not be versioned."""
        workspace = tmp_path / "workspace"
        (workspace / "trainer_output").mkdir(parents=True)
        (workspace / "adapter_model.safetensors").write_bytes(b"weights")

        files = store.read_tree(store.write_tree_from_directory(workspace))

        assert set(files) == {"adapter_model.safetensors"}

    def test_extract_restores_every_file(self, store, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "adapter_model.safetensors").write_bytes(b"weights")
        (workspace / "training_info.json").write_text('{"epochs":1}')

        tree_hash = store.write_tree_from_directory(workspace)
        destination = tmp_path / "restored"
        store.extract_tree(tree_hash, destination)

        assert (destination / "adapter_model.safetensors").read_bytes() == b"weights"
        assert (destination / "training_info.json").read_text() == '{"epochs":1}'

    def test_extract_reports_the_filenames_written(self, store, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "a.bin").write_bytes(b"a")
        (workspace / "b.bin").write_bytes(b"b")

        tree_hash = store.write_tree_from_directory(workspace)

        assert store.extract_tree(tree_hash, tmp_path / "out") == ["a.bin", "b.bin"]

    def test_tree_without_files_mapping_is_corrupt(self, store):
        digest = store.write_json("trees", {"wrong": "shape"})

        with pytest.raises(CorruptObject):
            store.read_tree(digest)
