# Aethel-Git: Complete System Explanation

## 🎯 What is Aethel-Git?

**Aethel-Git** is a **decentralized version control system specifically designed for AI models**, similar to how Git works for code. Instead of tracking text files, Aethel tracks **LoRA adapters** (lightweight AI model modifications) using content-addressable storage and cryptographic hashing.

---

## 🏗️ System Architecture Overview

### Core Components

```
Aethel-Git
├── CLI Layer (main.py)           → User-facing commands
├── Commands Layer                 → Business logic for each command
│   ├── init.py                   → Repository initialization
│   ├── commit.py                 → Version creation (the heart)
│   └── status.py                 → History viewing
└── Core Layer                     → Domain logic
    ├── model.py                  → AI model operations (loading, training, saving)
    └── aggregator.py             → Merkle tree for cryptographic proofs
```

### Storage Structure

```
.aethel/                           → Hidden repository folder (like .git)
├── config.json                    → Repository configuration
├── repo.db                        → SQLite database for metadata
├── objects/                       → Content-addressable storage
│   └── <SHA256_HASH>/            → Each version stored by its hash
│       ├── adapter_model.safetensors   → The actual LoRA weights
│       ├── adapter_config.json         → LoRA configuration
│       ├── commit.json                 → Commit metadata
│       └── README.md                   → Documentation
└── refs/
    └── heads/                     → Branch references (future)
```

---

## 📋 Demo Flow Walkthrough (`demo_flow.ps1`)

Let's trace exactly what happens when you run the demo:

### **Step 1: Clean Previous Run**
```powershell
if (Test-Path .aethel) {
    Remove-Item -Recurse -Force .aethel
}
```
**What happens:** Deletes any existing `.aethel` folder to start fresh.

---

### **Step 2: Repository Initialization (`aethel init`)**

#### Command Flow:
```
User runs: aethel init
    ↓
main.py receives command
    ↓
Dispatches to init.py → init() function
```

#### What `init()` Does:

1. **Creates Directory Structure:**
   ```
   .aethel/
   ├── objects/        → For storing model adapters
   └── refs/heads/     → For branch pointers
   ```

2. **Initializes SQLite Database (`repo.db`):**
   ```sql
   CREATE TABLE commits (
       hash TEXT PRIMARY KEY,              -- Commit identifier
       parent_hash TEXT,                   -- Previous commit (for history chain)
       message TEXT,                       -- Commit message
       timestamp DATETIME,                 -- When it was created
       author TEXT,                        -- Who created it
       metadata_cid TEXT,                  -- IPFS hash (Phase 3)
       adapter_cid TEXT                    -- Hash of the adapter weights
   )
   ```

3. **Creates Configuration File (`config.json`):**
   ```json
   {
       "model_id": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
       "author": "User"
   }
   ```

**Result:** Empty repository ready to track model versions.

---

### **Step 3: Check Status (`aethel status`)**

```
User runs: aethel status
    ↓
Executes: status.log()
    ↓
Queries database: SELECT * FROM commits
    ↓
Result: "No commits found" (empty repo)
```

---

### **Step 4: First Commit (`aethel commit -m "Initial commit"`)** ⭐

This is where the **magic happens**. Let's break it down step-by-step:

#### Phase A: Initialization (Lines 38-45 in commit.py)

```python
# 1. Check if .aethel exists
if not os.path.exists(AETHEL_DIR):
    console.print("Not an Aethel repository")
    
# 2. Load configuration
config = json.load(open(".aethel/config.json"))
# config = {"model_id": "TinyLlama/...", "author": "User"}
```

#### Phase B: Load Base Model (Lines 47-50)

```python
manager = ModelManager(model_id=config.get("model_id"))
manager.load_base_model()
```

**Inside `load_base_model()` (model.py):**

1. **Load Tokenizer:**
   ```python
   self.tokenizer = AutoTokenizer.from_pretrained("TinyLlama/...")
   ```

2. **Load Model with 4-bit Quantization:**
   ```python
   bnb_config = BitsAndBytesConfig(
       load_in_4bit=True,                    # Compress to 4 bits
       bnb_4bit_compute_dtype=torch.float16  # Use FP16 for computation
   )
   self.model = AutoModelForCausalLM.from_pretrained(
       "TinyLlama/...",
       quantization_config=bnb_config,
       device_map="auto"  # Automatically distribute across GPU/CPU
   )
   ```

   **Result:** 1.1B parameter model loaded in ~2.5GB RAM instead of ~4.5GB

#### Phase C: Create LoRA Adapter (Line 55)

```python
manager.create_or_load_adapter()
```

**Inside `create_or_load_adapter()` (model.py):**

```python
peft_config = LoraConfig(
    task_type=TaskType.CAUSAL_LM,  # Language modeling task
    r=8,                            # Rank (controls adapter size)
    lora_alpha=32,                  # Scaling factor
    lora_dropout=0.1,               # Regularization
    target_modules=["q_proj", "v_proj"]  # Which layers to adapt
)
self.model = get_peft_model(self.model, peft_config)
```

**What is LoRA?**
- Instead of modifying all 1.1B parameters, LoRA adds small **adapter matrices** to specific layers
- Only these adapters (~4MB) are trained and saved
- The base model (TinyLlama) remains frozen

**Trainable Parameters:**
```
trainable params: 2,359,296 || all params: 1,103,308,800
trainable%: 0.21%  ← Only 0.21% of the model is modified!
```

#### Phase D: Simulate Training (Line 58)

```python
manager.train_dummy_step()
```

**Inside `train_dummy_step()` (model.py):**

```python
with torch.no_grad():
    for name, param in self.model.named_parameters():
        if "lora_B" in name:  # Only modify LoRA matrices
            param.add_(torch.randn_like(param) * 0.05)
```

**What's happening:**
- Randomly perturbs LoRA weights (simulates training)
- In real usage, this would be `model.train()` with actual data
- The perturbation makes each commit unique

#### Phase E: Save Adapter (Lines 61-69)

```python
# Save to temporary location
temp_path = ".aethel/temp_stage"
manager.save_adapter(temp_path)
```

**Files created in `temp_stage/`:**
```
adapter_model.safetensors    ← LoRA weights (~4MB)
adapter_config.json          ← LoRA configuration
README.md                    ← Adapter documentation
```

#### Phase F: Content-Addressable Storage (Lines 71-85) 🔐

This is the **Git-like magic**:

```python
# 1. Calculate SHA256 hash of the adapter weights
adapter_hash = calculate_hash("temp_stage/adapter_model.safetensors")
# Result: 3bd19556045a926685be3ba4c7666ca24343c060bc71e4f1eef4183486753033

# 2. Move to permanent storage named by hash
storage_path = ".aethel/objects/3bd195560..."
os.rename(temp_path, storage_path)
```

**Why hash the file?**
- **Content Addressability:** If two commits produce identical adapters, they share storage (deduplication)
- **Integrity:** Any corruption changes the hash → immediate detection
- **Decentralization-ready:** IPFS uses the same principle

#### Phase G: Create Commit Metadata (Lines 87-97)

```python
parent_hash = get_last_commit_hash()  # None for first commit

metadata = {
    "author": "User",
    "message": "Initial commit - Base Model State",
    "parent": None,  # First commit has no parent
    "timestamp": "2026-02-03T11:42:11.792030",
    "adapter_content_hash": "3bd195560...",
    "base_model": "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
}

# Hash the metadata itself to get the commit hash
metadata_json = json.dumps(metadata, sort_keys=True)
commit_hash = hashlib.sha256(metadata_json.encode()).hexdigest()
# Result: 2639bce957d6371db8b6fcb7bb66e0b00961b56b03277e61d0108666496eda83
```

**Commit Hash vs Adapter Hash:**
- **Adapter Hash:** Identifies the LoRA weights file
- **Commit Hash:** Identifies the entire commit (metadata + adapter reference)

#### Phase H: Store in Database (Lines 103-109)

```python
conn = sqlite3.connect(".aethel/repo.db")
cursor.execute('''
    INSERT INTO commits (hash, parent_hash, message, author, adapter_cid)
    VALUES (?, ?, ?, ?, ?)
''', (commit_hash, None, "Initial commit...", "User", adapter_hash))
```

**Database after first commit:**

| hash | parent_hash | message | timestamp | author | adapter_cid |
|------|-------------|---------|-----------|--------|-------------|
| 2639bce... | NULL | Initial commit | 2026-02-03... | User | 3bd19556... |

**Output:**
```
✅ Commit successful!
Hash: 2639bce957d6371db8b6fcb7bb66e0b00961b56b03277e61d0108666496eda83
```

---

### **Step 5: Second Commit (`aethel commit -m "Training Run 1"`)** 

Same process repeats, but now:

```python
parent_hash = get_last_commit_hash()  
# Returns: 2639bce957d... (first commit)

metadata = {
    "parent": "2639bce957d...",  # Links to previous commit
    ...
}
```

**Database after second commit:**

| hash | parent_hash | message | timestamp |
|------|-------------|---------|-----------|
| 87f3cda... | 2639bce... | Training Run 1 | 2026-02-03... |
| 2639bce... | NULL | Initial commit | 2026-02-03... |

**Commit Chain Formed:**
```
NULL ← 2639bce (first) ← 87f3cda (second)
```

---

### **Step 6: View History (`aethel log`)**

```python
cursor.execute("SELECT hash, timestamp, author, message FROM commits ORDER BY timestamp DESC")
```

**Output Table:**
```
┏━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ Hash    ┃ Timestamp            ┃ Author ┃ Message          ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ 87f3cda │ 2026-02-03 11:43:22 │ User   │ Training Run 1   │
│ 2639bce │ 2026-02-03 11:42:11 │ User   │ Initial commit   │
└─────────┴─────────────────────┴────────┴──────────────────┘
```

---

## 🔄 Versioning System Explained

### How Versioning Works

Aethel uses a **patch-based** versioning system similar to Git:

1. **Base Model (Frozen):**
   - TinyLlama-1.1B remains unchanged
   - Downloaded once from Hugging Face

2. **Patches (LoRA Adapters):**
   - Each commit stores a LoRA adapter
   - Adapter = "delta" or "patch" applied to base model
   - ~4MB per commit (vs 4.5GB full model)

3. **Version Chain:**
   ```
   Base Model (TinyLlama) + Adapter_v1 = Model_v1
   Base Model (TinyLlama) + Adapter_v2 = Model_v2
   ```

### Storage Efficiency

**Traditional Approach:**
- Version 1: 4.5GB
- Version 2: 4.5GB
- Version 3: 4.5GB
- **Total: 13.5GB for 3 versions**

**Aethel Approach:**
- Base Model: 2.5GB (quantized, shared)
- Adapter 1: 4MB
- Adapter 2: 4MB
- Adapter 3: 4MB
- **Total: ~2.51GB for 3 versions** (95% savings!)

---

## 🔐 Content-Addressable Storage Deep Dive

### What is Content Addressability?

Files are stored by their **hash** rather than filename:

```
Traditional:
my_model.bin → stored at "models/my_model.bin"

Content-Addressed:
my_model.bin → hash → 3bd19556... → stored at "objects/3bd19556.../"
```

### Benefits

1. **Deduplication:**
   - If two commits produce identical adapters, only one copy stored
   - Hash collision = identical content

2. **Integrity Verification:**
   ```python
   stored_hash = "3bd19556..."
   actual_hash = calculate_hash("objects/3bd19556.../adapter_model.safetensors")
   
   if stored_hash != actual_hash:
       print("CORRUPTED! File has been tampered with")
   ```

3. **Decentralization-Ready:**
   - IPFS (InterPlanetary File System) uses the same principle
   - Files can be distributed across multiple nodes
   - Hash proves authenticity regardless of source

---

## 🧩 Loading and Unloading Patches

### Loading a Patch (Adapter)

**Currently (Phase 2):**
```python
# In commit.py (not fully implemented yet)
manager.create_or_load_adapter(adapter_path=None)
```

**Future Implementation (Checkout):**
```python
# User runs: aethel checkout 3bd19556...
def checkout(commit_hash):
    # 1. Query database for commit
    commit = db.get_commit(commit_hash)
    
    # 2. Load base model
    manager = ModelManager()
    manager.load_base_model()
    
    # 3. Load adapter from objects/
    adapter_path = f".aethel/objects/{commit['adapter_cid']}"
    manager.create_or_load_adapter(adapter_path)
    
    # 4. Model is now at version specified by commit_hash
    # User can now run inference or continue training
```

### Unloading a Patch

```python
# Unload = Save current state and revert to base
manager.save_adapter(".aethel/temp")  # Save current work
model = load_base_model()              # Reset to base
```

### Stacking Patches (Future: Branches)

```python
# Branch = parallel version history
Base Model ─┬─ Adapter_v1 ─ Adapter_v2 (main branch)
            │
            └─ Adapter_exp1 ─ Adapter_exp2 (experimental branch)
```

---

## 🌳 Merkle Tree (aggregator.py)

### What is a Merkle Tree?

A cryptographic data structure used to verify data integrity:

```
             Root Hash
            /          \
       Hash_AB      Hash_CD
       /    \       /     \
    Hash_A Hash_B Hash_C Hash_D
      |      |      |       |
    Data1  Data2  Data3   Data4
```

### How Aethel Uses It

**Current State:** Foundation laid (Phase 2)
**Future Use (Phase 3):** Blockchain anchoring

```python
# Example: Batch of 4 commits
commits = ["hash1", "hash2", "hash3", "hash4"]

tree = MerkleTree(commits)
root = tree.get_root()

# Anchor only the root to blockchain
# Proof of existence for all 4 commits with single transaction!
```

### Merkle Proof

```python
proof = tree.get_proof("hash2")
# Returns: [
#   ("hash1", "left"),    # Sibling of hash2
#   ("hash_CD", "right")  # Sibling of parent
# ]

# Anyone can verify hash2 is in the tree by:
# 1. Hash hash1 + hash2 → Hash_AB
# 2. Hash Hash_AB + Hash_CD → Root
# 3. Compare with published root
```

**Benefits:**
- Verify inclusion without downloading entire tree
- Compact proofs (~32 bytes per level)
- Blockchain anchoring costs reduced

---

## 📊 Database Schema Deep Dive

### Commits Table

| Column | Type | Purpose |
|--------|------|---------|
| `hash` | TEXT | **Primary Key**: Commit identifier (SHA256 of metadata) |
| `parent_hash` | TEXT | Links to previous commit (NULL for first commit) |
| `message` | TEXT | Human-readable description |
| `timestamp` | DATETIME | When commit was created |
| `author` | TEXT | Who created the commit |
| `metadata_cid` | TEXT | IPFS Content ID (Phase 3) |
| `adapter_cid` | TEXT | Hash of the adapter file |

### Example Queries

**Get full history:**
```sql
SELECT * FROM commits ORDER BY timestamp DESC;
```

**Reconstruct commit chain:**
```sql
WITH RECURSIVE chain AS (
    SELECT * FROM commits WHERE hash = 'latest_hash'
    UNION ALL
    SELECT c.* FROM commits c
    JOIN chain ON c.hash = chain.parent_hash
)
SELECT * FROM chain;
```

---

## 🛠️ Command Reference

### `aethel init`

**Purpose:** Initialize a new repository

**What it creates:**
- `.aethel/` directory
- `repo.db` SQLite database
- `config.json` with default settings
- `objects/` and `refs/heads/` directories

**Usage:**
```bash
cd my_project
aethel init
```

### `aethel commit -m "message"`

**Purpose:** Create a new version

**Process:**
1. Load base model (TinyLlama)
2. Create/load LoRA adapter
3. Simulate training (perturb weights)
4. Save adapter to temp location
5. Hash adapter file
6. Move to content-addressed storage
7. Create commit metadata
8. Hash metadata to get commit hash
9. Store in database

**Usage:**
```bash
aethel commit -m "Added feature X"
```

### `aethel status` / `aethel log`

**Purpose:** View commit history

**Output:**
- Table showing: Hash (first 8 chars), Timestamp, Author, Message
- Sorted by most recent first

**Usage:**
```bash
aethel log
```

---

## 🔬 Technical Deep Dives

### 1. LoRA (Low-Rank Adaptation)

**Mathematical Foundation:**

Standard fine-tuning modifies weight matrix $W$:
$$W' = W + \Delta W$$

LoRA decomposes $\Delta W$ into low-rank matrices:
$$\Delta W = BA$$

Where:
- $B \in \mathbb{R}^{d \times r}$
- $A \in \mathbb{R}^{r \times k}$
- $r \ll \min(d, k)$ (rank is much smaller)

**Why is this efficient?**
- Original: $d \times k$ parameters
- LoRA: $(d + k) \times r$ parameters
- For $r=8$, $d=4096$, $k=4096$: **99.8% parameter reduction**

### 2. 4-bit Quantization

**How it works:**

1. **Weight Representation:**
   - Float32: 32 bits per weight
   - Int4: 4 bits per weight
   - **8× compression**

2. **Quantization Process:**
   ```python
   # Simplified
   weight_int4 = round((weight_fp32 / scale) + zero_point)
   weight_fp32_reconstructed = (weight_int4 - zero_point) * scale
   ```

3. **Compute:**
   - Weights stored in 4-bit
   - Dequantized to FP16 during computation
   - Minimal accuracy loss (<1% on most tasks)

### 3. Content-Addressable Storage

**Hash Function Properties:**

1. **Deterministic:** Same input → Same hash
2. **Collision-Resistant:** Practically impossible to find two different inputs with same hash
3. **Fixed-Size:** Any input → 256-bit hash

**SHA256 Example:**
```python
import hashlib

data1 = b"Hello World"
hash1 = hashlib.sha256(data1).hexdigest()
# a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e

data2 = b"Hello Worlc"  # Changed one character
hash2 = hashlib.sha256(data2).hexdigest()
# Completely different hash!
# 67e7f8efe57be8b2dbe4e89e59c2f01f4aabbd46c0e51fbbaf3e7eabfb4f6d0f
```

### 4. Deduplication in Action

**Scenario:**
```python
# Two users independently train models
# Both produce identical adapters

# User A commits
hash_A = "3bd19556..."
storage.save("objects/3bd19556.../", adapter_A)

# User B commits
hash_B = calculate_hash(adapter_B)
# hash_B == "3bd19556..." (same hash!)

if storage.exists("objects/3bd19556.../"):
    print("Deduplicating - content already exists")
    # Don't save duplicate, just reference existing
```

**Storage Saved:** 4MB per duplicate

---

## 🚀 Phase 3 Preview: Decentralization

### Planned Features

1. **IPFS Integration:**
   ```python
   # Upload adapter to IPFS
   ipfs_hash = ipfs.add("objects/3bd19556.../adapter_model.safetensors")
   # Returns: QmX... (IPFS CID)
   
   # Store in database
   db.update_commit(adapter_cid=ipfs_hash)
   ```

2. **Blockchain Anchoring:**
   ```solidity
   // Smart contract on Ethereum/Polygon
   function anchorCommit(bytes32 merkleRoot, string ipfsCID) public {
       commits[merkleRoot] = Commit({
           root: merkleRoot,
           metadata: ipfsCID,
           timestamp: block.timestamp,
           author: msg.sender
       });
   }
   ```

3. **Distributed Collaboration:**
   ```bash
   # User A pushes to IPFS + Blockchain
   aethel push origin main
   
   # User B pulls from network
   aethel pull ipfs://QmX.../main
   ```

---

## 🎓 Key Concepts Summary

### Version Control
- **Commits:** Snapshots of model state
- **Parent Links:** Form history chain
- **Hashes:** Unique identifiers for each version

### Efficiency
- **LoRA:** Train only 0.21% of parameters
- **Quantization:** 8× memory reduction
- **Deduplication:** Share identical content

### Integrity
- **SHA256 Hashing:** Tamper-proof storage
- **Merkle Trees:** Efficient batch verification
- **Content Addressing:** File = Hash

### Decentralization (Phase 3)
- **IPFS:** Distributed file storage
- **Blockchain:** Immutable commit registry
- **P2P:** No central server required

---

## 📖 Glossary

- **Adapter:** Small trainable module added to frozen base model
- **CID:** Content Identifier (IPFS hash)
- **Commit:** Snapshot of model state with metadata
- **Content-Addressable:** Storage system where filename = hash of content
- **LoRA:** Low-Rank Adaptation technique for parameter-efficient fine-tuning
- **Merkle Tree:** Hash tree for efficient data verification
- **Patch:** Delta/difference between two model versions
- **Quantization:** Reducing numerical precision to save memory
- **SHA256:** Cryptographic hash function producing 256-bit digests

---

## 🤝 Comparison with Git

| Feature | Git | Aethel-Git |
|---------|-----|------------|
| **Tracks** | Text files | AI model weights |
| **Storage** | Diffs (deltas) | LoRA adapters (patches) |
| **Size** | KBs-MBs | MBs-GBs |
| **Hashing** | SHA1 | SHA256 |
| **Base** | None (full snapshots) | Frozen base model |
| **Diff Tool** | `git diff` | Adapter comparison |
| **Merge** | Text merge | Adapter merge (TBD) |

---

## ✨ What Makes Aethel Innovative?

1. **Model-Specific VCS:** First VCS designed specifically for AI models
2. **Extreme Efficiency:** 95%+ storage savings via LoRA
3. **Decentralization-Ready:** Built with IPFS/blockchain in mind from day 1
4. **Reproducibility:** Hash-based verification ensures bit-exact model reconstruction
5. **Collaboration:** Git-like workflow for ML teams

---

## 🔮 Future Roadmap

- **Phase 3:** IPFS + Blockchain integration
- **Phase 4:** Branching and merging
- **Phase 5:** Remote repositories (distributed)
- **Phase 6:** Model diff/comparison tools
- **Phase 7:** Adapter merge algorithms

---

## 💡 Usage Examples

### Typical Workflow

```bash
# 1. Start new project
cd my_model_project
aethel init

# 2. Train and save version
python train.py  # Your training script
aethel commit -m "Baseline model"

# 3. Experiment
python train.py --learning_rate 0.01
aethel commit -m "Increased LR experiment"

# 4. Review history
aethel log

# 5. (Future) Checkout specific version
aethel checkout 3bd19556...

# 6. (Future) Push to distributed network
aethel push ipfs main
```

---

## 🎯 Conclusion

Aethel-Git brings the power of version control to AI model development:

- ✅ **Efficient**: Store 100s of versions in GBs not TBs
- ✅ **Secure**: Cryptographic integrity guarantees
- ✅ **Decentralized**: No single point of failure (Phase 3)
- ✅ **Collaborative**: Git-like workflow for ML teams

**The future of AI model versioning is here! 🚀**
