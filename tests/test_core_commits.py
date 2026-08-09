"""Tests for commit creation and history traversal (aethel/core/commits.py).

Includes the new-layout counterpart to `test_s1_corruption.py`: the same
scenario that corrupts metadata under the old adapter-hash-keyed layout must
be structurally impossible here.
"""

import pytest

from aethel.core.commits import (
    build_base_object,
    create_commit,
    read_commit,
    walk_history,
)
from aethel.core.errors import DetachedHead, ObjectNotFound
from tests.conftest import (
    ADAPTER_BYTES_A,
    ADAPTER_BYTES_B,
    TRAINING_INFO,
    make_commit,
    stage_adapter,
)


class TestCommitCreation:
    def test_returns_a_valid_hash(self, core_repo, base_hash):
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        assert len(commit_hash) == 64
        assert core_repo.objects.exists("commits", commit_hash)

    def test_advances_the_current_branch(self, core_repo, base_hash):
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        assert core_repo.refs.read_branch("main") == commit_hash

    def test_first_commit_has_no_parent(self, core_repo, base_hash):
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        assert read_commit(core_repo, commit_hash)["parent_hash"] is None

    def test_second_commit_points_at_the_first(self, core_repo, base_hash):
        first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        assert read_commit(core_repo, second)["parent_hash"] == first

    def test_records_the_full_workspace_not_just_weights(self, core_repo, base_hash):
        """A checkout must restore config and training info from the store."""
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        commit = read_commit(core_repo, commit_hash)

        files = core_repo.objects.read_tree(commit["tree"])

        assert set(files) == {
            "adapter_model.safetensors",
            "adapter_config.json",
            "training_info.json",
        }

    def test_adapter_blob_matches_the_staged_weights(self, core_repo, base_hash):
        from aethel.core.hashing import hash_bytes

        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        assert read_commit(core_repo, commit_hash)["adapter_blob"] == hash_bytes(
            ADAPTER_BYTES_A
        )

    def test_preserves_message_author_and_training_info(self, core_repo, base_hash):
        commit_hash = make_commit(core_repo, base_hash, "a message", ADAPTER_BYTES_A)
        commit = read_commit(core_repo, commit_hash)

        assert commit["message"] == "a message"
        assert commit["author"] == "test-author"
        assert commit["training_info"] == TRAINING_INFO

    def test_does_not_modify_the_workspace(self, core_repo, base_hash):
        """The old code deleted and restored the workspace on every commit.

        That was a destructive no-op: committing is a read of the workspace,
        so there is no reason for it to write there at all.
        """
        stage_adapter(core_repo.root, ADAPTER_BYTES_A)
        before = {
            p.name: p.read_bytes() for p in core_repo.workspace_dir.iterdir() if p.is_file()
        }

        create_commit(
            core_repo,
            message="first",
            author="test-author",
            base_hash=base_hash,
            training_info=TRAINING_INFO,
        )

        after = {
            p.name: p.read_bytes() for p in core_repo.workspace_dir.iterdir() if p.is_file()
        }
        assert after == before

    def test_commit_without_adapter_weights_is_rejected(self, core_repo, base_hash):
        core_repo.workspace_dir.mkdir(parents=True, exist_ok=True)
        (core_repo.workspace_dir / "notes.txt").write_text("no weights here")

        with pytest.raises(ObjectNotFound, match="adapter"):
            create_commit(
                core_repo,
                message="empty",
                author="test-author",
                base_hash=base_hash,
                training_info=TRAINING_INFO,
            )

    def test_commit_on_detached_head_is_blocked(self, core_repo, base_hash):
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        core_repo.refs.set_head_detached(commit_hash)
        stage_adapter(core_repo.root, ADAPTER_BYTES_B)

        with pytest.raises(DetachedHead):
            create_commit(
                core_repo,
                message="orphan",
                author="test-author",
                base_hash=base_hash,
                training_info=TRAINING_INFO,
            )


class TestS1CorruptionIsStructurallyImpossible:
    """The new-layout counterpart to tests/test_s1_corruption.py.

    Under the old layout, byte-identical weights made two commits share one
    commit.json. Here, a commit is keyed by its own hash, so the collision
    has no representable form.
    """

    @pytest.fixture
    def two_identical(self, core_repo, base_hash):
        first = make_commit(core_repo, base_hash, "first commit message", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second commit message", ADAPTER_BYTES_A)
        return core_repo, first, second

    def test_commits_are_distinct(self, two_identical):
        _, first, second = two_identical

        assert first != second

    def test_each_commit_keeps_its_own_message(self, two_identical):
        repo, first, second = two_identical

        assert read_commit(repo, first)["message"] == "first commit message"
        assert read_commit(repo, second)["message"] == "second commit message"

    def test_each_commit_keeps_its_own_parent(self, two_identical):
        repo, first, second = two_identical

        assert read_commit(repo, first)["parent_hash"] is None
        assert read_commit(repo, second)["parent_hash"] == first

    def test_weights_are_still_deduplicated(self, two_identical):
        """Deduplication is the feature; only the metadata collision was a bug."""
        repo, first, second = two_identical

        assert read_commit(repo, first)["adapter_blob"] == read_commit(repo, second)[
            "adapter_blob"
        ]
        assert len(list(repo.objects.iter_hashes("blobs"))) == 3  # weights + config + info

    def test_both_commit_objects_exist_on_disk(self, two_identical):
        repo, first, second = two_identical

        assert set(repo.objects.iter_hashes("commits")) == {first, second}


class TestDeterminism:
    def test_identical_inputs_produce_identical_hashes(self, core_repo, base_hash):
        """Reproducibility: a commit is a pure function of its content.

        Pinning the timestamp is what makes this observable; in production the
        timestamp differs, which is why two commits of identical weights still
        get distinct hashes.
        """
        first = make_commit(
            core_repo, base_hash, "same", ADAPTER_BYTES_A, timestamp="2026-01-01T00:00:00Z"
        )

        core_repo.refs.create_branch("other", None)
        core_repo.refs.set_head_to_branch("other")

        second = make_commit(
            core_repo, base_hash, "same", ADAPTER_BYTES_A, timestamp="2026-01-01T00:00:00Z"
        )

        assert first == second

    def test_a_different_message_changes_the_hash(self, core_repo, base_hash):
        first = make_commit(
            core_repo, base_hash, "one", ADAPTER_BYTES_A, timestamp="2026-01-01T00:00:00Z"
        )

        core_repo.refs.create_branch("other", None)
        core_repo.refs.set_head_to_branch("other")

        second = make_commit(
            core_repo, base_hash, "two", ADAPTER_BYTES_A, timestamp="2026-01-01T00:00:00Z"
        )

        assert first != second


class TestHistory:
    def test_walks_back_to_the_root(self, core_repo, base_hash):
        first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        assert [h for h, _ in walk_history(core_repo, second)] == [second, first]

    def test_empty_history_yields_nothing(self, core_repo):
        assert list(walk_history(core_repo, None)) == []

    def test_history_needs_no_sqlite_index(self, core_repo, base_hash):
        """Portability: the object store alone is sufficient.

        The old checkout path could not resolve a commit without repo.db
        (checkout.py:51-70), which made the documented "copy the folder"
        claim false.
        """
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        core_repo.index_path.unlink(missing_ok=True)

        assert len(list(walk_history(core_repo, second))) == 2


class TestBaseObject:
    def test_records_the_pinned_revision(self):
        base = build_base_object("distilbert-base-uncased", "c" * 40)

        assert base["model_id"] == "distilbert-base-uncased"
        assert base["revision_sha"] == "c" * 40

    def test_stores_no_weights(self):
        """Base weights are referenced, never copied into the repository."""
        base = build_base_object("distilbert-base-uncased", "c" * 40)

        assert "weights" not in base
        assert set(base) == {"schema", "source", "model_id", "revision_sha"}

    def test_commit_references_the_base_object(self, core_repo, base_hash):
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        assert read_commit(core_repo, commit_hash)["base"] == base_hash
        assert core_repo.objects.read_json("bases", base_hash)["revision_sha"] == "b" * 40
