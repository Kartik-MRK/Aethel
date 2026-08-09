"""Tests for HEAD and branch references (aethel/core/refs.py)."""

import pytest

from aethel.core.errors import (
    BranchExists,
    BranchNotFound,
    DetachedHead,
    InvalidHash,
    InvalidRef,
)
from aethel.core.refs import Refs, validate_branch_name


@pytest.fixture
def refs(tmp_path):
    aethel = tmp_path / ".aethel"
    (aethel / "refs" / "heads").mkdir(parents=True)
    instance = Refs(aethel)
    instance.create_branch("main", None)
    instance.set_head_to_branch("main")
    return instance


COMMIT_A = "a" * 64
COMMIT_B = "b" * 64


class TestBranchNameValidation:
    @pytest.mark.parametrize(
        "name", ["main", "feature-1", "v1.0.2", "my_branch", "a"]
    )
    def test_accepts_reasonable_names(self, name):
        assert validate_branch_name(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "   ",
            ".",
            "..",
            "../escape",        # traversal
            "feature/nested",   # path separator
            "back\\slash",
            "has space",
            "has\ttab",
            "branch.lock",      # Git's lock suffix
            "feature@{now}",
            "tilde~1",
        ],
    )
    def test_rejects_unsafe_names(self, name):
        with pytest.raises(InvalidRef):
            validate_branch_name(name)

    @pytest.mark.parametrize("name", ["con", "PRN", "aux", "nul", "com1", "LPT9"])
    def test_rejects_windows_reserved_names(self, name):
        """A branch name becomes a filename; these are unusable on Windows."""
        with pytest.raises(InvalidRef):
            validate_branch_name(name)

    def test_surrounding_whitespace_is_stripped(self):
        assert validate_branch_name("  main  ") == "main"


class TestHead:
    def test_fresh_repo_is_attached_to_main(self, refs):
        head = refs.read_head()

        assert head.branch == "main"
        assert head.is_detached is False

    def test_unborn_branch_resolves_to_no_commit(self, refs):
        assert refs.resolve_head_commit() is None

    def test_resolves_the_branch_tip(self, refs):
        refs.update_branch("main", COMMIT_A)

        assert refs.resolve_head_commit() == COMMIT_A

    def test_detached_head_reports_its_commit(self, refs):
        refs.set_head_detached(COMMIT_B)
        head = refs.read_head()

        assert head.is_detached is True
        assert head.commit == COMMIT_B
        assert refs.resolve_head_commit() == COMMIT_B

    def test_reattaching_clears_the_detached_state(self, refs):
        refs.set_head_detached(COMMIT_B)
        refs.set_head_to_branch("main")

        assert refs.read_head().is_detached is False

    def test_missing_head_raises(self, refs):
        refs.head_path.unlink()

        with pytest.raises(InvalidRef):
            refs.read_head()

    @pytest.mark.parametrize(
        "content",
        ["", "   ", "garbage", "ref: refs/tags/v1", "ref: ", "zz" * 32],
    )
    def test_malformed_head_raises(self, refs, content):
        refs.head_path.write_text(content, encoding="utf-8")

        with pytest.raises(InvalidRef):
            refs.read_head()

    def test_detached_hash_is_normalized_to_lowercase(self, refs):
        refs.head_path.write_text("A" * 64, encoding="utf-8")

        assert refs.read_head().commit == "a" * 64


class TestDetachedHeadGuard:
    def test_attached_head_returns_the_branch(self, refs):
        assert refs.require_attached_branch() == "main"

    def test_detached_head_raises_with_the_commit(self, refs):
        """Preserves the original implementation's guard (commit.py:315-329).

        Stricter than Git, which only warns. Committing here would create an
        object no ref points at, orphaned by the next checkout.
        """
        refs.set_head_detached(COMMIT_A)

        with pytest.raises(DetachedHead) as exc_info:
            refs.require_attached_branch()

        assert exc_info.value.commit_hash == COMMIT_A


class TestBranches:
    def test_create_and_read(self, refs):
        refs.create_branch("feature", COMMIT_A)

        assert refs.read_branch("feature") == COMMIT_A

    def test_unborn_branch_reads_as_none(self, refs):
        refs.create_branch("empty", None)

        assert refs.read_branch("empty") is None

    def test_duplicate_creation_raises(self, refs):
        refs.create_branch("feature", COMMIT_A)

        with pytest.raises(BranchExists):
            refs.create_branch("feature", COMMIT_B)

    def test_reading_a_missing_branch_raises(self, refs):
        with pytest.raises(BranchNotFound):
            refs.read_branch("nope")

    def test_updating_a_missing_branch_raises(self, refs):
        with pytest.raises(BranchNotFound):
            refs.update_branch("nope", COMMIT_A)

    def test_update_advances_the_tip(self, refs):
        refs.update_branch("main", COMMIT_A)
        refs.update_branch("main", COMMIT_B)

        assert refs.read_branch("main") == COMMIT_B

    def test_list_is_sorted(self, refs):
        refs.create_branch("zeta", COMMIT_A)
        refs.create_branch("alpha", COMMIT_A)

        assert refs.list_branches() == ["alpha", "main", "zeta"]

    def test_branch_tip_is_normalized_to_lowercase(self, refs):
        refs.branch_path("main").write_text("A" * 64, encoding="utf-8")

        assert refs.read_branch("main") == "a" * 64

    def test_corrupt_branch_tip_raises(self, refs):
        refs.branch_path("main").write_text("not-a-commit-hash", encoding="utf-8")

        with pytest.raises(InvalidHash):
            refs.read_branch("main")


class TestPathContainment:
    @pytest.mark.parametrize(
        "name", ["../../etc/passwd", "../escape", "sub/dir"]
    )
    def test_traversal_attempts_are_rejected(self, refs, name):
        """Closes the weaker check the old branch.py:86 used.

        That code compared with a bare `startswith`, which accepts a sibling
        path whose name merely shares a prefix with the repository's.
        """
        with pytest.raises(InvalidRef):
            refs.branch_path(name)

    def test_valid_branch_resolves_inside_refs_heads(self, refs):
        assert refs.branch_path("feature").parent == refs.heads_dir
