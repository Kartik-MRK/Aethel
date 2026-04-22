# Aethel-Git: Complete System Explanation

## 🎯 What is Aethel-Git?

**Aethel-Git** is a **decentralized version control system specifically designed for AI models**, similar to how Git works for code. Instead of tracking text files, Aethel tracks **LoRA adapters** (lightweight AI model modifications) using content-addressable storage and cryptographic hashing.

---

## 🏗️ System Architecture Overview

### Core Components

```
Aethel-Git/
├── CLI Layer (main.py)            → User-facing commands
├── Commands Layer                  → Business logic for each command
│   ├── init.py                    → Repository initialization
│   ├── train.py                   → LoRA training from YAML config
│   ├── commit.py                  → Version creation (snapshots workspace)
│   ├── branch.py                  → Branch creation
│   ├── checkout.py                → Switch between branches/commits
│   └── status.py                  → History viewing
└── Core Layer                      → Domain logic
    ├── model.py                   → AI model operations (loading, saving)
    └── aggregator.py              → Merkle tree for cryptographic proofs
```

### Storage Structure (`.aethel/` Internals)

Aethel uses a **folder-based, content-addressable object store** — each adapter version gets its own folder inside `.aethel/objects/`, named by the SHA256 hash of the adapter weights file. All related files (adapter weights, config, and commit metadata) live together in that folder.

```
.aethel/                            → Hidden repository folder (like .git)
├── HEAD                            → Points to the active branch (e.g. "ref: refs/heads/main")
├── config.json                     → Repository configuration (model_id, revision_hash, author)
├── repo.db                         → SQLite database for commit metadata index
├── objects/                        → Content-addressable object store
│   └── <adapter_hash>/             → Folder named by SHA256 of adapter weights
│       ├── adapter_model.safetensors   → The actual LoRA weights
│       ├── adapter_config.json         → LoRA configuration
│       ├── training_info.json          → Training hyperparameters & metrics
│       └── commit.json                 → Commit metadata (author, message, parent, etc.)
├── refs/
│   └── heads/                      → Branch tip references
│       ├── main                    → File containing the latest commit hash on main
│       └── <branch_name>           → File containing the latest commit hash on that branch
└── workspace/                      → Staging area for train output (cleared after commit)
    ├── adapter_model.safetensors   → LoRA weights (created by `aethel train`)
    ├── adapter_config.json         → LoRA configuration
    └── training_info.json          → Training hyperparameters & metrics
```

### How Objects are Stored

Each commit creates a folder named by the **adapter weights hash**. The folder bundles everything needed to reconstruct that version: the adapter files, training metadata, and the commit JSON. The commit hash (separate from the adapter hash) is computed from the metadata JSON and stored in the database. Checkout uses the database to map `commit_hash → adapter_hash → folder`.

---

## 📋 Complete Command Reference

### `aethel init --model <owner/model>`

**Purpose:** Initialize a new Aethel-Git repository.

**What it does:**
1. Prompts for (or accepts via `--model`) a Hugging Face model repository ID.
2. Fetches the **immutable revision hash** from Hugging Face Hub to pin the exact model version.
3. Creates `.aethel/` directory with `objects/`, `refs/heads/`, and `workspace/` subdirectories.
4. Creates `HEAD` file pointing to `refs/heads/main`.
5. Creates empty `refs/heads/main` file.
6. Initializes SQLite database `repo.db` with the `commits` table.
7. Writes `config.json` with `model_id`, `revision_hash`, and `author`.

**Usage:**
```bash
aethel init --model distilbert-base-uncased
```

**Result:** Empty repository ready to track model versions, pinned to a specific model revision.

---

### `aethel train --config <path.yaml>`

**Purpose:** Run actual LoRA fine-tuning and write adapter artifacts into the workspace.

**What it does:**
1. Loads the pinned model from config (`model_id` + `revision_hash`).
2. Reads training hyperparameters from a YAML config file.
3. Auto-detects whether to use `CausalLM` or `SequenceClassification` based on model architecture.
4. Automatically infers the best LoRA target modules by scanning the model's `nn.Linear` layers.
5. Runs real training using HuggingFace `Trainer` with a stub dataset.
6. Saves the adapter artifacts to `.aethel/workspace/`:
   - `adapter_model.safetensors` — the LoRA weights
   - `adapter_config.json` — the LoRA config
   - `training_info.json` — hyperparams, metrics, task type, target modules, timestamp

**YAML config format:**
```yaml
dataset: ./imdb_dataset
lora_rank: 8
lora_alpha: 32
batch_size: 8
epochs: 3
```

**Usage:**
```bash
aethel train --config imdb_train.yaml
```

**Result:** Workspace now contains trained adapter artifacts, ready to be committed.

---

### `aethel commit -m "message"`

**Purpose:** Snapshot the workspace into a permanent, hashed version.

**Important:** `commit` does NOT train the model. It reads whatever is already in `.aethel/workspace/` (created by `aethel train`). The workflow is:

```
aethel train → produces workspace artifacts → aethel commit → stores them permanently
```

**What it does (step by step):**

1. **Read workspace:** Finds `adapter_model.safetensors` in `.aethel/workspace/`.
2. **Hash the adapter:** Computes SHA256 of the raw adapter file → `adapter_hash`.
3. **Store adapter folder:** Creates `.aethel/objects/<adapter_hash>/` and copies all workspace files into it. If the folder already exists, it deduplicates.
4. **Read training metadata:** Loads `training_info.json` from workspace.
5. **Build commit metadata:**
   ```json
   {
     "adapter_blob": "<adapter_hash>",
     "author": "kartik local",
     "base_model": "distilbert-base-uncased",
     "message": "Initial commit",
     "parent_hash": null,
     "revision_hash": "12040accade4e8a0f71eabdb258fecc2e7e948be",
     "timestamp": "2026-04-22T07:30:00.000000",
     "training_info": { ... hyperparams and metrics ... }
   }
   ```
6. **Hash commit:** Computes SHA256 of the canonical JSON → `commit_hash`.
7. **Store commit.json:** Writes the commit JSON inside the adapter folder as `commit.json`.
8. **Update database:** Inserts a row into `repo.db`.
9. **Advance branch:** Writes the new `commit_hash` into `refs/heads/main`.
10. **Clear workspace:** Removes all files from `.aethel/workspace/`.

**Usage:**
```bash
aethel commit -m "Added sentiment analysis adapter"
```

---

### `aethel branch <name>`

**Purpose:** Create a new lightweight branch at the current commit (like `git branch`).

**What it does:**
1. Reads HEAD to find the currently active branch.
2. Reads that branch's tip commit hash.
3. Creates a new file `refs/heads/<name>` containing the same commit hash.
4. Does NOT switch HEAD (you stay on the current branch).

**Usage:**
```bash
aethel branch experiment-v2
```

**Result:** A new branch pointer exists, sharing history with the source branch up to this point.

---

### `aethel checkout <target>`

**Purpose:** Switch HEAD to a different branch or commit.

**What it does:**
1. **Resolves the target** — tries branch name first, then commit hash (looked up via database).
2. **Validates** — ensures the commit's adapter folder exists in the object store.
3. **Updates HEAD:**
   - If target is a branch: HEAD becomes `ref: refs/heads/<name>` (symbolic).
   - If target is a commit hash: HEAD becomes just the hash (detached HEAD).
4. **Restores workspace:** Copies adapter files from `.aethel/objects/<adapter_hash>/` back into `.aethel/workspace/` (skipping `commit.json`).

**Usage:**
```bash
# Switch to a branch
aethel checkout experiment-v2

# Detach HEAD at a specific commit
aethel checkout abc123def456...
```

**After checkout:** The workspace contains the adapter files from the checked-out version, ready to be loaded for inference or further training.

---

### `aethel status` / `aethel log`

**Purpose:** View commit history from the database.

**What it does:**
1. Queries all commits from SQLite, ordered by timestamp descending.
2. Displays a formatted table: Hash (first 8 chars), Timestamp, Author, Message.

**Usage:**
```bash
aethel status
aethel log
```

---

## 🔄 Complete Workflow Example

```bash
# 1. Initialize repository pinned to a specific model
aethel init --model distilbert-base-uncased

# 2. Train a LoRA adapter
aethel train --config imdb_train.yaml

# 3. Commit the trained adapter
aethel commit -m "Baseline sentiment model"

# 4. Train again with different hyperparams
aethel train --config imdb_train_v2.yaml

# 5. Commit the new version
aethel commit -m "Increased rank experiment"

# 6. View history
aethel log

# 7. Create a branch for experiments
aethel branch experimental

# 8. Switch to the experiment branch
aethel checkout experimental

# 9. Train and commit on the experiment branch
aethel train --config aggressive_config.yaml
aethel commit -m "Aggressive LR on experiment branch"

# 10. Switch back to main
aethel checkout main
```

---

## 🔐 Content-Addressable Storage Deep Dive

### How It Works (Folder-Based Object Store)

Each adapter version gets its own folder inside `.aethel/objects/`, named by the SHA256 hash of the adapter weights file:

```
.aethel/objects/
├── 3bd19556045a926685.../       ← Folder named by adapter hash
│   ├── adapter_model.safetensors   ← The LoRA weights
│   ├── adapter_config.json         ← LoRA configuration
│   ├── training_info.json          ← Training metadata
│   └── commit.json                 ← Commit metadata (author, message, parent, etc.)
├── a7f3cda82e19b44f01.../       ← Another adapter version
│   └── ...
└── ...
```

The commit hash (separate from the adapter hash) is stored in the database. When you run `aethel checkout`, the database maps `commit_hash → adapter_hash → folder`.

### Why Folder-Based Storage?

| Benefit | Explanation |
|:--|:--|
| **Self-contained** | Each folder has everything needed to reconstruct that version |
| **Inspectable** | `dir .aethel/objects/<hash>` shows exactly what's inside |
| **Portable** | Copy a folder to another machine and you have the full version |
| **Deduplication** | Identical adapters share the same folder (same hash = same content) |

### Deduplication

If two training runs produce identical adapter weights, they will have the same SHA256 hash. The second commit will simply reference the existing folder — no duplicate storage.

```
Commit A ──→ folder 3bd195.../  (1.8MB stored)
Commit B ──→ folder 3bd195.../  (0 bytes additional — same folder!)
Commit C ──→ folder 7cf291.../  (1.8MB stored — different weights)
```

---

## 🌿 Branching Model

Aethel uses the same branching model as Git:

```
HEAD → refs/heads/main → <commit_hash>

Commit Chain:
NULL ← commit_1 ← commit_2 ← commit_3 (main)
                       ↖
                        commit_4 ← commit_5 (experiment)
```

- **HEAD** is a file containing either:
  - `ref: refs/heads/<branch>` — normal attached HEAD
  - `<commit_hash>` — detached HEAD
- **Branch refs** are files in `refs/heads/` containing a single commit hash
- **Creating a branch** just creates a new ref file pointing to the current commit
- **Commits** advance the active branch ref

---

## 🌳 Merkle Tree (aggregator.py)

### Purpose

The Merkle Tree module is the cryptographic foundation for future blockchain anchoring. It allows batching multiple commit hashes into a single root hash.

### How It Works

```
             Root Hash
            /          \
       Hash_AB      Hash_CD
       /    \       /     \
    Hash_A Hash_B Hash_C Hash_D
      |      |      |       |
    Commit1 Commit2 Commit3 Commit4
```

### Current Status

The Merkle Tree is implemented and functional but **not yet integrated** into the commit pipeline. It will be used in Phase 3 for blockchain anchoring — instead of anchoring every commit individually (expensive), we batch them and anchor only the Merkle root.

---

## 📊 Database Schema

### Commits Table (`repo.db`)

| Column | Type | Purpose |
|--------|------|---------|
| `hash` | TEXT (PK) | Commit identifier (SHA256 of metadata JSON) |
| `parent_hash` | TEXT | Previous commit hash (NULL for first commit) |
| `message` | TEXT | Human-readable commit message |
| `timestamp` | DATETIME | When commit was created |
| `author` | TEXT | Who created the commit |
| `metadata_cid` | TEXT | Set to the commit hash (future: IPFS CID) |
| `adapter_cid` | TEXT | Blob hash of the adapter weights file |

---

## 🔬 Technical Deep Dives

### 1. Model Pinning (Revision Hash)

When you run `aethel init --model distilbert-base-uncased`, Aethel fetches the model's current SHA from Hugging Face Hub and stores it in `config.json`. Every subsequent `aethel train` uses this exact revision:

```python
model = AutoModelForCausalLM.from_pretrained(model_id, revision=revision_hash)
```

This ensures **reproducibility** — even if the model author pushes updates, your repo always uses the same checkpoint.

### 2. LoRA (Low-Rank Adaptation)

Standard fine-tuning modifies all weights. LoRA decomposes the update into low-rank matrices:

$$\Delta W = BA$$

Where $B$ and $A$ are small matrices with rank $r \ll d$. Benefits:
- Original: $d \times k$ parameters to store
- LoRA: $(d + k) \times r$ parameters (~99.8% reduction for $r=8$)
- Each "version" is ~4MB instead of ~4.5GB

### 3. Target Module Auto-Detection

The `train.py` command automatically scans the model for `nn.Linear` layers and selects LoRA targets from a priority list: `q_proj`, `v_proj`, `k_proj`, `o_proj`, `c_attn`, `query_key_value`, etc. This means the same code works across different model architectures.

### 4. Commit Hashing

The commit hash is deterministic — given the same metadata, you get the same hash:

```python
metadata_json = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
commit_hash = hashlib.sha256(metadata_json.encode("utf-8")).hexdigest()
```

Using `sort_keys=True` and compact separators ensures canonical JSON regardless of insertion order.

---

## 🤝 Comparison with Git

| Feature | Git | Aethel-Git |
|---------|-----|------------|
| **Tracks** | Text files | AI model adapters (LoRA weights) |
| **Storage** | Diffs (deltas) | Full adapter snapshots (flat blobs) |
| **Object store** | `.git/objects/<2char>/<38char>` | `.aethel/objects/<64char>` |
| **Size per version** | KBs-MBs | ~4MB (adapter only) |
| **Hashing** | SHA1 | SHA256 |
| **Base** | None (full copy) | Frozen base model (shared) |
| **HEAD** | Symbolic ref or detached hash | Same |
| **Branches** | Ref files in refs/heads/ | Same |
| **Training** | N/A | `aethel train` (decoupled from commit) |

---

## ✨ Key Design Decisions

1. **Train/Commit are decoupled.** Unlike the early prototype where `commit` did training internally, the current design separates concerns: `train` writes to workspace, `commit` snapshots workspace to objects.

2. **Flat object store.** Blobs and commits are stored as flat files named by hash, not in subdirectories. This is cleaner, avoids metadata-overwrite risks, and maps cleanly to IPFS.

3. **Model pinning via revision hash.** Ensures bit-exact reproducibility across collaborators.

4. **Workspace as staging area.** The workspace is cleared after every commit, enforcing a clean state.

---

## 🔮 Future Roadmap

- **Phase 3:** IPFS integration for distributed adapter storage
- **Phase 4:** Blockchain anchoring of Merkle roots (Solidity smart contracts)
- **Phase 5:** Remote push/pull workflows
- **Phase 6:** Model diff/comparison tools
- **Phase 7:** Adapter merge algorithms

---

## 📖 Glossary

- **Adapter:** Small trainable LoRA module added to a frozen base model
- **Blob:** Raw binary object in the object store (the adapter weights file)
- **Branch:** A named pointer to a commit hash (file in `refs/heads/`)
- **CID:** Content Identifier (future: IPFS hash)
- **Commit:** JSON metadata object referencing a blob, parent, message, etc.
- **Content-Addressable:** Storage where filename = hash of file content
- **Detached HEAD:** HEAD pointing directly to a commit hash (not a branch)
- **HEAD:** File indicating the currently active branch or commit
- **LoRA:** Low-Rank Adaptation technique for parameter-efficient fine-tuning
- **Merkle Tree:** Hash tree for efficient batch verification
- **Object Store:** The `.aethel/objects/` directory containing all blobs and commits
- **Revision Hash:** Immutable SHA of a Hugging Face model version
- **SHA256:** Cryptographic hash function producing 256-bit (64 hex char) digests
- **Workspace:** Temporary staging area (`.aethel/workspace/`) populated by `train`, consumed by `commit`
