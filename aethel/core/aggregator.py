"""Merkle tree for the append-only transparency log.

This is the structure the Hub will use to commit to its history: hash every
commit into a tree, publish the root on-chain, and serve inclusion proofs. A
client checks its commit against the anchored root without trusting the Hub.

SECURITY FIX — second-preimage resistance
-----------------------------------------
The original implementation hashed leaves and internal nodes identically::

    def hash_pair(a, b): return sha256(a + b)      # internal nodes
    def hash_data(d):    return sha256(d)          # leaves

That makes an internal node indistinguishable from a leaf, so a tree built
over another tree's internal nodes produces the SAME root. Demonstrated
against the old code:

    real   = MerkleTree([l0, l1, l2, l3])
    forged = MerkleTree([H(l0+l1), H(l2+l3)])
    real.get_root() == forged.get_root()          # -> True

An operator could therefore produce a valid-looking inclusion proof for a
value that was never a committed leaf — precisely the forgery the anchored
log exists to prevent.

The fix is domain separation, as specified by RFC 6962 (Certificate
Transparency): tag leaves and internal nodes with distinct prefixes so their
hash spaces cannot overlap.

    leaf     = SHA256(0x00 || data)
    internal = SHA256(0x01 || left || right)

Odd nodes are promoted (carried up a level) rather than duplicated. Promotion
is what CT does; duplicating the last node is the Bitcoin behaviour behind
CVE-2012-2459, where two distinct leaf sets can yield one root.
"""

import hashlib

#: Domain-separation tags. Without these, leaves and internal nodes share a
#: hash space and inclusion proofs can be forged (see module docstring).
LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


def hash_leaf(data: str) -> str:
    """Hash a leaf value with the leaf domain tag."""
    return hashlib.sha256(LEAF_PREFIX + data.encode("utf-8")).hexdigest()


def hash_node(left: str, right: str) -> str:
    """Hash two child hashes with the internal-node domain tag."""
    return hashlib.sha256(NODE_PREFIX + (left + right).encode("utf-8")).hexdigest()


class MerkleTree:
    """A Merkle tree over an ordered list of leaf hashes.

    Leaves are expected to be already-hashed values -- for Aethel, commit
    hashes. `hash_leaf` is applied to each, so a raw commit hash and the tree
    leaf derived from it are different values by construction.
    """

    def __init__(self, leaves: list[str]):
        self.raw_leaves = list(leaves)
        self.leaves = [hash_leaf(leaf) for leaf in leaves]
        self.levels: list[list[str]] = [self.leaves]
        self.root: str | None = None
        self._build()

    def _build(self) -> None:
        if not self.leaves:
            self.root = None
            return

        current = self.leaves
        while len(current) > 1:
            nxt: list[str] = []
            for i in range(0, len(current), 2):
                if i + 1 < len(current):
                    nxt.append(hash_node(current[i], current[i + 1]))
                else:
                    # Promote the odd node unchanged (RFC 6962). Duplicating
                    # it instead would let two different leaf sets produce the
                    # same root.
                    nxt.append(current[i])
            self.levels.append(nxt)
            current = nxt

        self.root = current[0]

    def get_root(self) -> str | None:
        return self.root

    def get_proof(self, leaf: str) -> list[tuple[str, str]] | None:
        """Return the sibling path proving `leaf` is in this tree.

        Each element is ``(sibling_hash, side)`` where `side` says which side
        the sibling sits on. Returns None if the leaf is not present.

        `leaf` is a raw value (a commit hash), not a pre-hashed leaf.
        """
        if leaf not in self.raw_leaves:
            return None

        index = self.raw_leaves.index(leaf)
        proof: list[tuple[str, str]] = []

        for level in self.levels[:-1]:
            is_right_child = index % 2 == 1
            sibling_index = index - 1 if is_right_child else index + 1

            if sibling_index < len(level):
                side = "left" if is_right_child else "right"
                proof.append((level[sibling_index], side))

            index //= 2

        return proof


def verify_proof(leaf: str, proof: list[tuple[str, str]], root: str) -> bool:
    """Recompute a root from a leaf and its sibling path.

    This is the check a client runs against the on-chain root. It needs no
    access to the Hub or the full tree -- only the leaf, the proof, and the
    anchored root -- which is what makes the log verifiable by a third party.
    """
    if not root:
        return False

    computed = hash_leaf(leaf)

    for sibling, side in proof:
        if side == "left":
            computed = hash_node(sibling, computed)
        elif side == "right":
            computed = hash_node(computed, sibling)
        else:
            return False

    return computed == root
