"""Tests for the Merkle transparency log (aethel/core/aggregator.py).

This structure underpins the anchoring design: the Hub publishes a root
on-chain and serves inclusion proofs, and a client verifies its commit
against that root without trusting the Hub. A forgery here would let a
malicious operator prove inclusion of something never committed.
"""

import pytest

from aethel.core.aggregator import (
    MerkleTree,
    hash_leaf,
    hash_node,
    verify_proof,
)


def commits(n: int) -> list[str]:
    """n stand-in commit hashes."""
    return [f"commit-{i:04d}" for i in range(n)]


class TestDomainSeparation:
    def test_leaf_and_node_hashes_differ_for_the_same_input(self):
        """The property that makes second-preimage forgery impossible."""
        assert hash_leaf("a" + "b") != hash_node("a", "b")

    def test_internal_node_cannot_masquerade_as_a_leaf(self):
        """Regression test for the original second-preimage defect.

        The old implementation hashed leaves and internal nodes identically,
        so a tree built over another tree's internal nodes produced an
        IDENTICAL root -- letting an operator forge an inclusion proof for a
        value that was never a committed leaf.
        """
        leaves = commits(4)
        real = MerkleTree(leaves)

        # An attacker's tree whose "leaves" are the real tree's internal nodes.
        internal = [
            hash_node(hash_leaf(leaves[0]), hash_leaf(leaves[1])),
            hash_node(hash_leaf(leaves[2]), hash_leaf(leaves[3])),
        ]
        forged = MerkleTree(internal)

        assert real.get_root() != forged.get_root(), (
            "an internal node can be presented as a committed leaf, "
            "inclusion proofs are forgeable"
        )


class TestTreeConstruction:
    def test_empty_tree_has_no_root(self):
        assert MerkleTree([]).get_root() is None

    def test_single_leaf_tree_has_a_root(self):
        tree = MerkleTree(commits(1))

        assert tree.get_root() == hash_leaf("commit-0000")

    def test_root_is_deterministic(self):
        assert MerkleTree(commits(7)).get_root() == MerkleTree(commits(7)).get_root()

    def test_leaf_order_changes_the_root(self):
        """Order matters: the log is append-only, not a set."""
        forward = MerkleTree(commits(4)).get_root()
        reversed_ = MerkleTree(list(reversed(commits(4)))).get_root()

        assert forward != reversed_

    def test_appending_a_commit_changes_the_root(self):
        """Anchoring is meaningless if adding history leaves the root fixed."""
        assert MerkleTree(commits(5)).get_root() != MerkleTree(commits(6)).get_root()

    def test_removing_a_commit_changes_the_root(self):
        """The tamper-detection property the whole design rests on."""
        full = commits(6)
        tampered = full[:3] + full[4:]  # silently drop one commit

        assert MerkleTree(full).get_root() != MerkleTree(tampered).get_root()

    def test_editing_a_commit_changes_the_root(self):
        full = commits(6)
        tampered = list(full)
        tampered[2] = "commit-FORGED"

        assert MerkleTree(full).get_root() != MerkleTree(tampered).get_root()


class TestInclusionProofs:
    @pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 31])
    def test_every_leaf_proves_against_the_root(self, size):
        """Odd sizes matter: they exercise the promotion path."""
        leaves = commits(size)
        tree = MerkleTree(leaves)
        root = tree.get_root()

        for leaf in leaves:
            proof = tree.get_proof(leaf)
            assert proof is not None, f"no proof for {leaf} in a {size}-leaf tree"
            assert verify_proof(leaf, proof, root), (
                f"proof failed for {leaf} in a {size}-leaf tree"
            )

    def test_absent_leaf_has_no_proof(self):
        assert MerkleTree(commits(4)).get_proof("commit-9999") is None

    def test_proof_fails_against_a_different_root(self):
        tree = MerkleTree(commits(4))
        proof = tree.get_proof("commit-0000")
        other_root = MerkleTree(commits(5)).get_root()

        assert verify_proof("commit-0000", proof, other_root) is False

    def test_proof_fails_for_a_substituted_leaf(self):
        """A valid proof must not validate a different commit."""
        tree = MerkleTree(commits(4))
        proof = tree.get_proof("commit-0000")

        assert verify_proof("commit-FORGED", proof, tree.get_root()) is False

    def test_tampered_sibling_invalidates_the_proof(self):
        tree = MerkleTree(commits(4))
        proof = tree.get_proof("commit-0001")
        tampered = [("f" * 64, side) for _, side in proof]

        assert verify_proof("commit-0001", tampered, tree.get_root()) is False

    def test_flipped_direction_invalidates_the_proof(self):
        """Sibling order is part of the commitment."""
        tree = MerkleTree(commits(4))
        proof = tree.get_proof("commit-0001")
        flipped = [
            (sibling, "right" if side == "left" else "left") for sibling, side in proof
        ]

        assert verify_proof("commit-0001", flipped, tree.get_root()) is False

    def test_empty_root_never_verifies(self):
        assert verify_proof("commit-0000", [], None) is False

    def test_unknown_direction_is_rejected(self):
        tree = MerkleTree(commits(4))

        assert verify_proof("commit-0000", [("a" * 64, "sideways")], tree.get_root()) is False

    def test_proof_size_is_logarithmic(self):
        """A 1024-commit log proves inclusion in ~10 hashes, not 1024.

        This is why batching into one anchored root stays cheap as history
        grows.
        """
        leaves = commits(1024)
        proof = MerkleTree(leaves).get_proof(leaves[500])

        assert len(proof) <= 11
