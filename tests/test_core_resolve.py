"""Tests for commit-ish resolution (aethel/core/commits.py).

`resolve_commitish` backs `checkout`, `log` and `branch`, so a bug here
silently sends every one of them to the wrong commit.
"""

import pytest

from aethel.core.commits import resolve_commitish
from aethel.core.errors import InvalidRef, ObjectNotFound
from tests.conftest import ADAPTER_BYTES_A, ADAPTER_BYTES_B, make_commit


class TestBranchResolution:
    def test_resolves_a_branch_name_to_its_tip(self, core_repo, base_hash):
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        assert resolve_commitish(core_repo, "main") == ("branch", commit_hash)

    def test_branch_wins_over_a_hash_like_name(self, core_repo, base_hash):
        """Branch names take priority, matching Git."""
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        core_repo.refs.create_branch("abcdef", commit_hash)

        kind, resolved = resolve_commitish(core_repo, "abcdef")

        assert kind == "branch"
        assert resolved == commit_hash

    def test_unborn_branch_is_rejected_with_a_useful_message(self, core_repo):
        with pytest.raises(InvalidRef, match="no commits yet"):
            resolve_commitish(core_repo, "main")


class TestHashResolution:
    def test_resolves_a_full_hash(self, core_repo, base_hash):
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        assert resolve_commitish(core_repo, commit_hash) == ("commit", commit_hash)

    def test_accepts_uppercase(self, core_repo, base_hash):
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        assert resolve_commitish(core_repo, commit_hash.upper())[1] == commit_hash

    def test_resolves_an_unambiguous_abbreviation(self, core_repo, base_hash):
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        assert resolve_commitish(core_repo, commit_hash[:12]) == ("commit", commit_hash)

    def test_abbreviation_works_without_any_index(self, core_repo, base_hash):
        """Resolution scans the object store, so no database is involved."""
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        assert not list(core_repo.aethel_dir.glob("*.db"))
        assert resolve_commitish(core_repo, commit_hash[:8])[1] == commit_hash

    def test_full_hash_of_a_missing_commit_raises(self, core_repo, base_hash):
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        with pytest.raises(ObjectNotFound):
            resolve_commitish(core_repo, "f" * 64)

    def test_ambiguous_abbreviation_is_rejected(self, core_repo, base_hash, monkeypatch):
        """An ambiguous prefix must error, never silently pick one."""
        first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        # Must be >= 4 chars: shorter prefixes are rejected as too-short
        # before ambiguity is ever considered.
        shared = "abcd"
        monkeypatch.setattr(
            core_repo.objects,
            "iter_hashes",
            lambda kind: iter([shared + first[4:], shared + second[4:]]),
        )

        with pytest.raises(InvalidRef, match="ambiguous"):
            resolve_commitish(core_repo, shared)

    @pytest.mark.parametrize("target", ["", "   ", "nope", "zzz", "!!!"])
    def test_unresolvable_targets_are_rejected(self, core_repo, base_hash, target):
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        with pytest.raises(InvalidRef):
            resolve_commitish(core_repo, target)

    def test_too_short_an_abbreviation_is_rejected(self, core_repo, base_hash):
        """Below 4 characters, collisions are too likely to guess from."""
        commit_hash = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        with pytest.raises(InvalidRef):
            resolve_commitish(core_repo, commit_hash[:3])
