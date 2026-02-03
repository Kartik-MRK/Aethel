import hashlib

def hash_data(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()

def hash_pair(a: str, b: str) -> str:
    """
    Standard Merkle Tree pair hashing.
    Typically sorted to ensure determinism, but for simple trees order matters.
    We will just concat a + b.
    For solidity combatibility, we usually use keccak256 and maybe sort, 
    but for this PoC we stick to sha256.
    """
    return hashlib.sha256((a + b).encode()).hexdigest()

class MerkleTree:
    def __init__(self, leaves: list[str]):
        self.leaves = leaves
        self.levels = [leaves]
        self.build()

    def build(self):
        if not self.leaves:
            self.root = None
            return

        current_level = self.leaves
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                if i + 1 < len(current_level):
                    # Combine pair
                    h = hash_pair(current_level[i], current_level[i+1])
                    next_level.append(h)
                else:
                    # Odd one out, duplicate it (or promote it depending on implementation)
                    # Promoting it is standard for some, duplicating for others.
                    # Let's promote (carry over)
                    next_level.append(current_level[i])
            self.levels.append(next_level)
            current_level = next_level
        
        self.root = current_level[0]

    def get_root(self):
        return self.root

    def get_proof(self, target_hash: str) -> list[tuple[str, str]]:
        """
        Returns a list of (sibling_hash, direction) needed to prove target_hash is in root.
        Direction: 'left' or 'right' indicating where the sibling is.
        """
        if target_hash not in self.leaves:
            return None
        
        proof = []
        index = self.leaves.index(target_hash)
        
        for level in self.levels[:-1]: # Don't go up to root
            is_right_child = index % 2 == 1
            sibling_index = index - 1 if is_right_child else index + 1
            
            if sibling_index < len(level):
                sibling = level[sibling_index]
                direction = 'left' if is_right_child else 'right'
                proof.append((sibling, direction))
            
            index //= 2
            
        return proof
