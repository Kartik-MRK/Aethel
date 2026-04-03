# Aethel-Git Project Status Report

Date: 2026-03-31
Project: Aethel-Git
Current phase: Phase 2 (Core Engine and CLI Architecture)

## 1. Project Objective

Aethel-Git is a Git-inspired version control system for AI model evolution, focused on storing and tracking LoRA adapters instead of full model checkpoints. The current implementation emphasizes:

- Content-addressable storage using SHA256 hashes
- Lightweight model versioning through LoRA adapters
- Local commit history tracking with SQLite
- A CLI workflow similar to init/commit/log

## 2. What Has Been Completed So Far

### 2.1 Package and CLI foundation

- Converted project into an installable Python package named `aethel`
- Added console entry point:
  - `aethel = aethel.main:app`
- Implemented Typer-based command routing

### 2.2 Repository initialization workflow (`aethel init`)

Implemented in `aethel/commands/init.py`.

Completed behavior:

- Creates `.aethel/` repository directory (if missing)
- Creates object and refs folders:
  - `.aethel/objects/`
  - `.aethel/refs/heads/`
- Creates SQLite database `.aethel/repo.db`
- Creates `commits` table if not present
- Creates default config file `.aethel/config.json` with:
  - `model_id = TinyLlama/TinyLlama-1.1B-Chat-v1.0`
  - `author = User`

### 2.3 Commit workflow (`aethel commit -m "..."`)

Implemented in `aethel/commands/commit.py` and `aethel/core/model.py`.

Completed behavior:

1. Verifies current directory is an Aethel repository
2. Loads repo config
3. Loads base model using Transformers
4. Attempts 4-bit quantized loading (bitsandbytes)
5. Falls back to standard FP16 model loading if quantization fails
6. Creates a new LoRA adapter (or loads one if path is provided)
7. Runs a dummy training perturbation step (PoC simulation)
8. Saves adapter artifacts to a temporary staging folder
9. Hashes `adapter_model.safetensors` (or `.bin` fallback)
10. Moves artifact to content-addressable storage:
    - `.aethel/objects/<adapter_hash>/`
11. Deduplicates if content folder already exists
12. Builds commit metadata (author, message, parent hash, timestamp, base model, adapter hash)
13. Computes a commit hash from metadata JSON
14. Stores commit metadata and inserts a row into SQLite `commits` table

### 2.4 History display (`aethel status` and `aethel log`)

Implemented in `aethel/commands/status.py` and aliased in `aethel/main.py`.

Completed behavior:

- Reads commit rows from SQLite
- Displays hash, timestamp, author, and message in a Rich table
- Shows clear empty-state message if no commits exist

### 2.5 Merkle tree foundation

Implemented in `aethel/core/aggregator.py`.

Completed behavior:

- SHA256 data hashing helpers
- Pair hashing for internal Merkle nodes
- Merkle tree build from leaf hash list
- Root hash retrieval
- Inclusion proof generation (`get_proof`)

Note: Merkle tree logic is currently utility-only and not yet integrated into commit persistence or anchoring.

### 2.6 Environment and demo automation

- `setup_env.ps1` automates:
  - venv creation
  - pip upgrade
  - requirements installation
- `demo_flow.ps1` runs an end-to-end local demo:
  - cleanup
  - init
  - status
  - two commits
  - log

### 2.7 Additional validation script

- `experiment_lora.py` provides an aggressive validation/stress experiment for:
  - base model loading
  - adapter perturbation
  - unload/reload cycles
  - logits comparison for reversibility checks

## 3. Tools and Technologies Used

## 3.1 Core development tools

- Python (project language)
- PowerShell scripts for setup/demo automation
- Virtual environments (`venv`)
- pip for dependency installation
- setuptools for package installation and CLI entry point

### 3.2 Runtime and framework tools

- Typer: CLI framework
- Rich: console output and tables
- SQLite3: local commit metadata storage
- JSON: config and metadata serialization
- hashlib (SHA256): content and commit hashing
- OS/file operations: local object store and refs layout

### 3.3 AI/ML stack

- PyTorch: tensor/model operations
- Transformers: model/tokenizer loading
- PEFT: LoRA adapter creation/loading/saving
- bitsandbytes: 4-bit quantized inference/model loading path
- accelerate: device mapping/runtime acceleration support
- safetensors: adapter weight format
- scipy: numerical dependency used by stack

## 4. Libraries in the Project

From current `requirements.txt` and `setup.py`:

- `torch`
- `transformers`
- `peft`
- `bitsandbytes`
- `accelerate`
- `scipy`
- `safetensors`
- `typer[all]`
- `rich`
- `pydantic`

## 5. Current Codebase Structure

```text
Aethel-Git/
  README.md
  AETHEL_SYSTEM_EXPLAINED.md
  requirements.txt
  setup.py
  setup_env.ps1
  demo_flow.ps1
  experiment_lora.py

  aethel/
    __init__.py
    main.py
    commands/
      init.py
      commit.py
      status.py
    core/
      model.py
      aggregator.py

  .aethel/                    # runtime repository state (created by init)
    config.json
    repo.db
    objects/
      <adapter_hash>/
        adapter_model.safetensors
        adapter_config.json
        commit.json
        README.md
    refs/
      heads/
```

## 6. What Tasks It Can Do Right Now

User-facing capabilities available now:

- Initialize a model-versioning repository in current directory
- Create commit entries for model state changes (simulated training)
- Save LoRA adapters as version artifacts
- Hash adapter content and store by hash (content-addressed storage)
- Deduplicate adapter storage when identical content appears
- Persist commit history in SQLite
- Display commit history in a readable CLI table

Internal capabilities already available:

- Base model loading with quantized and fallback paths
- LoRA adapter creation and persistence
- Merkle root and proof generation utility
- Automated setup and demo scripts for reproducible local runs

## 7. Current Feature Set (Implemented vs Planned)

### Implemented now

- `aethel init`
- `aethel commit -m "..."`
- `aethel status`
- `aethel log` alias
- Local object storage and SQLite commit tracking

### Planned/not yet implemented (from docs and code comments)

- Checkout specific commit/version
- Branching and merge workflows
- Remote/distributed collaboration
- IPFS storage integration
- Blockchain anchoring of Merkle roots
- Integrated Merkle proofs in commit lifecycle

## 8. Known Limitations and Risks at Current Stage

- Training step is simulated (not dataset-driven fine-tuning)
- No automated unit/integration test suite yet
- Merkle tree is not wired into commit records yet
- Commit metadata currently saved inside adapter-hash object folder; this can cause metadata overwrite risk when multiple logical commits reference identical adapter content
- No conflict resolution, branch semantics, or checkout state management yet

## 9. Practical Summary

The project has a working local Phase-2 prototype with a complete CLI path for repository initialization, model adapter commit creation, and history display. It already demonstrates key ideas (LoRA-based efficient versioning, hash-addressed artifacts, and local metadata tracking). The next major implementation step is to formalize commit-object modeling (separate from adapter-object modeling), then add checkout and decentralized features.
