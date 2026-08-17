"""Tests for the Hub's append-only transparency log (hub/log.py).

The log is the bridge between the object store and the chain: its leaves are
commit hashes, its root is what gets anchored, and its inclusion proofs are what
a third party checks. Three properties have to hold, and each one has tests
here:

* **Append-only.** Nothing in the module can rewrite or remove a leaf.
* **Idempotent.** Pushing the same history twice must not change the root, or
  every re-push would look like tampering.
* **Verifiable.** Every logged commit proves against the published root, and any
  alteration to the log breaks that proof.

`aethel.core.aggregator` (the Merkle tree itself) is tested separately in
test_core_merkle.py -- these tests are about the log built on top of it.
"""

import hashlib
import json

import pytest

from aethel.core.errors import InvalidHash
from hub.log import AnchorStore, LogEntry, TransparencyLog

WHEN = "2026-01-01T00:00:00+00:00"


def digest(label: str) -> str:
    """A stand-in commit hash. Real 64-hex, because the log validates them."""
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def digests(count: int) -> list[str]:
    return [digest(f"commit-{index}") for index in range(count)]


@pytest.fixture
def log(tmp_path):
    return TransparencyLog(tmp_path / "log.jsonl")


class TestAppendOnlyByConstruction:
    def test_module_offers_no_way_to_remove_a_leaf(self):
        """The append-only claim should hold because no other verb exists.

        A test rather than a comment: someone adding a `rewrite` or `truncate`
        helper for convenience would be breaking the one property the chain
        anchoring is there to protect, and should have to delete this test to do
        it.
        """
        mutators = [
            name
            for name in dir(TransparencyLog)
            if not name.startswith("_")
            and any(verb in name for verb in ("delete", "remove", "update", "truncate", "rewrite"))
        ]
        assert mutators == []

    def test_appending_only_adds_lines_to_the_end(self, log):
        """Earlier bytes are never rewritten, so a crash cannot corrupt them."""
        log.append(digest("a"), repo="demo", accepted_at=WHEN)
        first = log.path.read_bytes()

        log.append(digest("b"), repo="demo", accepted_at=WHEN)
        second = log.path.read_bytes()

        assert second.startswith(first)
        assert len(second) > len(first)

    def test_indices_are_sequential_from_zero(self, log):
        for expected, commit in enumerate(digests(4)):
            assert log.append(commit, repo="demo", accepted_at=WHEN).index == expected

    def test_an_absent_log_reads_as_empty_rather_than_failing(self, log):
        assert log.entries() == []
        assert log.size() == 0
        assert log.root() is None
        assert log.inclusion_proof(digest("a")) is None

    def test_entries_survive_a_reopen(self, tmp_path):
        path = tmp_path / "log.jsonl"
        TransparencyLog(path).append(digest("a"), repo="demo", accepted_at=WHEN)

        reopened = TransparencyLog(path)
        assert reopened.size() == 1
        assert reopened.leaves() == [digest("a")]

    def test_the_log_is_readable_as_plain_jsonl(self, log):
        """On-disk auditability: `cat log.jsonl` has to be enough."""
        log.append(digest("a"), repo="demo", accepted_at=WHEN)

        line = log.path.read_text(encoding="utf-8").splitlines()[0]
        assert json.loads(line) == {
            "index": 0,
            "commit_hash": digest("a"),
            "repo": "demo",
            "accepted_at": WHEN,
        }

    def test_a_malformed_hash_is_refused(self, log):
        with pytest.raises(InvalidHash):
            log.append("not-a-hash", repo="demo", accepted_at=WHEN)

        assert log.size() == 0

    def test_hash_case_does_not_create_a_second_leaf(self, log):
        commit = digest("a")
        log.append(commit.upper(), repo="demo", accepted_at=WHEN)

        assert log.leaves() == [commit]
        assert log.contains(commit)
        assert log.contains(commit.upper())


class TestIdempotence:
    def test_appending_the_same_commit_twice_adds_one_leaf(self, log):
        commit = digest("a")
        first = log.append(commit, repo="demo", accepted_at=WHEN)
        second = log.append(commit, repo="demo", accepted_at="2026-06-06T00:00:00+00:00")

        assert log.size() == 1
        assert second == first

    def test_the_root_is_unchanged_by_a_repeat_append(self, log):
        for commit in digests(3):
            log.append(commit, repo="demo", accepted_at=WHEN)

        before = log.root()
        log.append(digests(3)[1], repo="demo", accepted_at=WHEN)

        assert log.root() == before

    def test_the_first_repo_to_push_a_commit_keeps_the_attribution(self, log):
        """Leaves are keyed by commit hash, so `repo` means "first accepted under".

        Two repositories pushing a byte-identical commit share one leaf -- the
        hash is the identity, and duplicating it would put the same content in
        the log twice under two names.
        """
        commit = digest("shared")
        log.append(commit, repo="first", accepted_at=WHEN)
        log.append(commit, repo="second", accepted_at=WHEN)

        assert [entry.repo for entry in log.entries()] == ["first"]


class TestBatchedAppend:
    def test_a_batch_keeps_the_order_it_was_given(self, log):
        commits = digests(5)
        result = log.append_many(commits, repo="demo", accepted_at=WHEN)

        assert result.indices == [0, 1, 2, 3, 4]
        assert log.leaves() == commits

    def test_a_batch_matches_appending_one_at_a_time(self, tmp_path):
        """Equivalence, so the fast path cannot mean a different log.

        `append_many` exists only to avoid re-reading the log once per ancestor.
        If it ever produced a different leaf order or a different root than the
        obvious loop, it would be a bug in the thing being anchored.
        """
        commits = digests(7)

        batched = TransparencyLog(tmp_path / "batched.jsonl")
        batched.append_many(commits, repo="demo", accepted_at=WHEN)

        one_by_one = TransparencyLog(tmp_path / "one-by-one.jsonl")
        for commit in commits:
            one_by_one.append(commit, repo="demo", accepted_at=WHEN)

        assert batched.leaves() == one_by_one.leaves()
        assert batched.root() == one_by_one.root()

    def test_only_genuinely_new_leaves_are_reported_as_added(self, log):
        commits = digests(4)
        log.append_many(commits[:2], repo="demo", accepted_at=WHEN)

        result = log.append_many(commits, repo="demo", accepted_at=WHEN)

        assert result.indices == [0, 1, 2, 3]
        assert result.added == [2, 3]

    def test_a_repush_adds_nothing_and_leaves_the_root_alone(self, log):
        commits = digests(3)
        log.append_many(commits, repo="demo", accepted_at=WHEN)
        before = log.root()

        result = log.append_many(commits, repo="demo", accepted_at=WHEN)

        assert result.added == []
        assert result.indices == [0, 1, 2]
        assert log.size() == 3
        assert log.root() == before

    def test_a_duplicate_within_one_batch_produces_one_leaf(self, log):
        commit = digest("a")
        result = log.append_many([commit, digest("b"), commit], repo="demo", accepted_at=WHEN)

        assert log.size() == 2
        assert result.indices == [0, 1, 0]
        assert result.added == [0, 1]

    def test_an_empty_batch_writes_nothing(self, log):
        result = log.append_many([], repo="demo", accepted_at=WHEN)

        assert result.entries == []
        assert result.added == []
        assert log.size() == 0
        assert log.root() is None

    def test_a_batch_of_malformed_hashes_writes_nothing(self, log):
        """Validation happens before the lock, so a bad batch is all-or-nothing."""
        with pytest.raises(InvalidHash):
            log.append_many([digest("a"), "nonsense"], repo="demo", accepted_at=WHEN)

        assert log.size() == 0


class TestInclusionProofs:
    def test_every_logged_commit_proves_against_the_published_root(self, log):
        commits = digests(9)
        log.append_many(commits, repo="demo", accepted_at=WHEN)
        root = log.root()

        for commit in commits:
            proof = log.inclusion_proof(commit)
            assert proof is not None
            assert proof["root"] == root
            assert proof["log_size"] == len(commits)
            assert TransparencyLog.verify(commit, proof["proof"], root)

    def test_a_proof_reports_the_leaf_index(self, log):
        commits = digests(4)
        log.append_many(commits, repo="demo", accepted_at=WHEN)

        assert log.inclusion_proof(commits[2])["leaf_index"] == 2

    def test_an_unlogged_commit_has_no_proof(self, log):
        log.append_many(digests(3), repo="demo", accepted_at=WHEN)

        assert log.inclusion_proof(digest("never-pushed")) is None

    def test_a_malformed_hash_is_refused_rather_than_reported_absent(self, log):
        with pytest.raises(InvalidHash):
            log.inclusion_proof("nonsense")

    def test_tampering_with_a_leaf_invalidates_the_proof(self, log):
        """The demo's whole point, at the log layer.

        Rewriting a leaf on disk moves the root. The proof issued before the
        edit no longer verifies against the recomputed root, so the alteration
        is detectable by anyone holding the earlier root -- which is exactly what
        anchoring publishes.
        """
        commits = digests(4)
        log.append_many(commits, repo="demo", accepted_at=WHEN)

        proof = log.inclusion_proof(commits[0])
        anchored_root = log.root()

        entries = log.entries()
        forged = LogEntry(
            index=entries[2].index,
            commit_hash=digest("forged"),
            repo=entries[2].repo,
            accepted_at=entries[2].accepted_at,
        )
        rewritten = [*entries[:2], forged, *entries[3:]]
        log.path.write_text(
            "\n".join(entry.to_json() for entry in rewritten) + "\n", encoding="utf-8"
        )

        assert log.root() != anchored_root
        assert not TransparencyLog.verify(commits[0], proof["proof"], log.root())

    def test_reordering_leaves_changes_the_root(self, log):
        commits = digests(4)
        log.append_many(commits, repo="demo", accepted_at=WHEN)
        original = log.root()

        entries = log.entries()
        swapped = [entries[1], entries[0], *entries[2:]]
        log.path.write_text(
            "\n".join(entry.to_json() for entry in swapped) + "\n", encoding="utf-8"
        )

        assert log.root() != original

    def test_a_proof_from_a_shorter_log_fails_against_a_longer_one(self, log):
        """A proof is bound to a root, not to the commit alone."""
        commits = digests(5)
        log.append_many(commits[:4], repo="demo", accepted_at=WHEN)
        early_proof = log.inclusion_proof(commits[0])

        log.append(commits[4], repo="demo", accepted_at=WHEN)

        assert not TransparencyLog.verify(commits[0], early_proof["proof"], log.root())
        assert TransparencyLog.verify(commits[0], early_proof["proof"], early_proof["root"])


class TestAnchorStore:
    def test_an_unanchored_hub_reports_no_records(self, tmp_path):
        anchors = AnchorStore(tmp_path / "anchors.jsonl")

        assert anchors.records() == []
        assert anchors.latest() is None

    def test_latest_is_the_most_recent_record(self, tmp_path):
        anchors = AnchorStore(tmp_path / "anchors.jsonl")
        anchors.append({"root": "a" * 64, "block": 1})
        anchors.append({"root": "b" * 64, "block": 2})

        assert anchors.latest() == {"root": "b" * 64, "block": 2}
        assert len(anchors.records()) == 2

    def test_records_survive_a_reopen(self, tmp_path):
        path = tmp_path / "anchors.jsonl"
        AnchorStore(path).append({"root": "a" * 64, "block": 7})

        assert AnchorStore(path).latest()["block"] == 7
