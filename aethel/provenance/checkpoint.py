"""Checkpoint validation independent of a Hub's claims."""

from aethel.core.aggregator import MerkleTree, hash_leaf, hash_node
from aethel.core.errors import AethelError
from aethel.core.hashing import normalize_hash

MAX_PREFIX_LEAVES = 100_000


class CheckpointError(AethelError):
    pass


def checked_prefix(leaves: list[str], size: int) -> str:
    if type(size) is not int or not 0 < size <= MAX_PREFIX_LEAVES:
        raise CheckpointError("Checkpoint size is outside the supported range")
    if not isinstance(leaves, list) or len(leaves) < size:
        raise CheckpointError("The log is shorter than the checkpoint")
    values = [normalize_hash(value) for value in leaves[:size]]
    if len(set(values)) != size:
        raise CheckpointError("Log prefix contains duplicate commits")
    return MerkleTree(values).get_root()


def verify_position(proof: dict, expected_commit: str, expected_root: str, expected_size: int) -> bool:
    """Bind the proof to its exact position and tree shape, including odd nodes."""
    try:
        expected_commit = normalize_hash(expected_commit)
        index, size = proof["leaf_index"], proof["log_size"]
        if type(index) is not int or type(size) is not int or not 0 <= index < size:
            return False
        if size != expected_size or proof["commit_hash"] != expected_commit or proof["root"] != expected_root:
            return False
        steps = iter(proof["proof"])
        computed = hash_leaf(expected_commit)
        while size > 1:
            if index % 2 or index + 1 < size:
                step = next(steps)
                side = "left" if index % 2 else "right"
                if step["side"] != side:
                    return False
                sibling = normalize_hash(step["sibling"])
                computed = hash_node(sibling, computed) if side == "left" else hash_node(computed, sibling)
            index //= 2
            size = (size + 1) // 2
        return next(steps, None) is None and computed == expected_root
    except (AethelError, KeyError, TypeError, StopIteration):
        return False


def verify_bundle(bundle: dict, commit_hash: str, chain) -> dict:
    """Trust comes from the caller's chain configuration, never the bundle."""
    try:
        checkpoint, proof, leaves = bundle["checkpoint"], bundle["proof"], bundle["leaves"]
        size, root = checkpoint["size"], checkpoint["root"]
        if len(leaves) != size or checked_prefix(leaves, size) != root:
            raise CheckpointError("Bundle does not reproduce the checkpoint root")
        if not verify_position(proof, commit_hash, root, size) or leaves[proof["leaf_index"]] != commit_hash:
            raise CheckpointError("Commit proof does not match the checkpoint position")
        return chain.verify(size, root, leaves=leaves)
    except (KeyError, TypeError, IndexError) as exc:
        raise CheckpointError("Malformed checkpoint bundle") from exc
