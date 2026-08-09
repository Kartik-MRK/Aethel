"""Regression tests for defect S1.1 -- silent commit metadata corruption.

THE DEFECT (now fixed)
----------------------
The original layout stored commit metadata inside the adapter's folder:

    .aethel/objects/<adapter_hash>/commit.json

Adapter hashes deduplicate by design, so two commits with byte-identical
weights resolved to the same path and the second silently overwrote the
first. Checking out the first commit returned the SECOND commit's message,
author and parent. It was recorded as a "known limitation" in
PROJECT_STATUS_REPORT.md and shipped unfixed, because nothing executed it.

Verified failing against the old code before the rebuild:

    FAILED test_first_commit_retains_its_own_message
    FAILED test_each_commit_reports_its_own_parent
    FAILED test_no_commit_field_leaks_between_deduplicated_commits[message]
    FAILED test_no_commit_field_leaks_between_deduplicated_commits[parent_hash]
    FAILED test_no_commit_field_leaks_between_deduplicated_commits[timestamp]

To reproduce that failure, check out a commit from before the rebuild and run
this file against it.

WHY THESE TESTS STAY
--------------------
Commits are now keyed by their own hash, so the collision has no
representable form. These run through the real CLI command layer (unlike
tests/test_core_commits.py, which covers the same property at the core API),
so they also guard the wiring: a future change that reintroduces
adapter-keyed metadata fails here.
"""

import pytest

from tests.conftest import (
    ADAPTER_BYTES_A,
    ADAPTER_BYTES_B,
    commit_hashes_in_order,
    read_commit_metadata,
    stage_adapter,
    stored_blob_count,
)


@pytest.fixture
def two_identical_commits(core_repo):
    """Two distinct commits whose adapter weights are byte-identical.

    Goes through `aethel commit` itself, not the core API, so the command
    layer's base-object handling and ref advancement are exercised too.
    """
    from aethel.commands.commit import run_commit

    stage_adapter(core_repo.root, ADAPTER_BYTES_A)
    run_commit("first commit message")

    # Identical bytes are still staged: commit does not touch the workspace.
    run_commit("second commit message")

    return core_repo.root, commit_hashes_in_order(core_repo.root)


def test_two_commits_with_identical_weights_get_distinct_hashes(two_identical_commits):
    """Sanity check: these must be two genuinely different commits.

    Guaranteed by parent_hash alone -- the second commit's parent is the
    first -- so it does not depend on timestamp resolution.
    """
    _, hashes = two_identical_commits

    assert len(hashes) == 2, f"expected 2 commits, got {len(hashes)}"
    assert hashes[0] != hashes[1], "commits must have distinct hashes"


def test_first_commit_retains_its_own_message(two_identical_commits):
    """S1.1: the first commit must not be overwritten by the second."""
    root, hashes = two_identical_commits

    metadata = read_commit_metadata(hashes[0], root)

    assert metadata["message"] == "first commit message", (
        f"commit {hashes[0][:12]} returned another commit's metadata: "
        f"{metadata['message']!r}. Commit metadata is being stored per-adapter "
        f"instead of per-commit."
    )


def test_second_commit_retains_its_own_message(two_identical_commits):
    """The second commit must also be intact -- it was the surviving writer."""
    root, hashes = two_identical_commits

    assert read_commit_metadata(hashes[1], root)["message"] == "second commit message"


def test_each_commit_reports_its_own_parent(two_identical_commits):
    """Lineage must survive deduplication.

    A corrupted parent pointer is worse than a corrupted message: it breaks
    the commit DAG, and that DAG is what gets anchored to the chain. Anchoring
    a corrupted lineage would produce tamper-proof false provenance.
    """
    root, hashes = two_identical_commits
    first_hash, second_hash = hashes

    assert read_commit_metadata(first_hash, root)["parent_hash"] is None
    assert read_commit_metadata(second_hash, root)["parent_hash"] == first_hash


def test_identical_weights_are_still_stored_only_once(two_identical_commits):
    """Deduplication is a feature and must survive the fix.

    Guards against 'fixing' S1.1 by storing a full copy per commit, which
    would trade a correctness bug for unbounded storage growth. Three blobs:
    the weights, adapter_config.json and training_info.json -- shared by both
    commits rather than duplicated.
    """
    root, _ = two_identical_commits

    assert stored_blob_count(root) == 3


def test_different_weights_are_stored_separately(core_repo):
    """Different adapter content must not collide."""
    from aethel.commands.commit import run_commit

    stage_adapter(core_repo.root, ADAPTER_BYTES_A)
    run_commit("adapter A")

    stage_adapter(core_repo.root, ADAPTER_BYTES_B)
    run_commit("adapter B")

    root = core_repo.root
    hashes = commit_hashes_in_order(root)

    assert len(hashes) == 2
    # 4 blobs: two distinct weight files, plus the shared config and info.
    assert stored_blob_count(root) == 4
    assert read_commit_metadata(hashes[0], root)["message"] == "adapter A"
    assert read_commit_metadata(hashes[1], root)["message"] == "adapter B"


@pytest.mark.parametrize("field", ["message", "parent_hash", "timestamp"])
def test_no_commit_field_leaks_between_deduplicated_commits(two_identical_commits, field):
    """No field may be silently inherited from the other commit.

    Parametrized because the defect corrupted the whole metadata record, not
    just the message -- the failure output names which field leaked.
    """
    root, hashes = two_identical_commits

    first_meta = read_commit_metadata(hashes[0], root)
    second_meta = read_commit_metadata(hashes[1], root)

    assert first_meta[field] != second_meta[field], (
        f"{field!r} is identical across two distinct commits -- "
        f"one commit's metadata overwrote the other's"
    )


def test_author_survives_on_both_commits(two_identical_commits):
    """Both commits share an author here; assert it survived intact."""
    root, hashes = two_identical_commits

    assert read_commit_metadata(hashes[0], root)["author"] == "test-author"
    assert read_commit_metadata(hashes[1], root)["author"] == "test-author"
