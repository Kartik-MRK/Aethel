# Aethel-Git

**A Decentralized Version Control System for AI Models**

## Phase 2 Status: Core Engine & CLI Architecture (Completed)

We have successfully transitioned from the initial scripts of Phase 1 to a structured Python package (`aethel`). This phase focused on building the "Git-like" Command Line Interface (CLI) and the core engine for managing Model Adapters (LoRA) as versioned artifacts.

### 📂 File Structure & Purpose

The project is now organized as a proper python package:

- **`setup_env.ps1`**:
  - **Purpose**: One-click setup script. Creates the virtual environment (`.venv`), upgrades pip, and installs the `aethel` package in editable mode.
  - **Usage**: Run `.\setup_env.ps1` in PowerShell.

- **`aethel/`** (Package Source):
  - **`main.py`**: The entry point for the CLI. It uses `typer` to dispatch commands (`init`, `commit`, `log`).

  - **`commands/`**:
    - **`init.py`**: Implements `aethel init`. Creates the hidden `.aethel` repository folder, initializes the SQLite database (`repo.db`), and creates the `config.json` file.
    - **`commit.py`**: The heart of the VCS. It loads the base model, simulates training (perturbation), saves the LoRA adapter, hashes it (content-addressing), and records the commit metadata in the database.
    - **`status.py`**: Implements reading the commit history (`aethel log`) from the SQLite database.

  - **`core/`**:
    - **`model.py`**: `ModelManager` class. Encapsulates all `peft`/`transformers` logic. Handles loading the base model (TinyLlama-1.1B), creating adapters, and ensuring bit-exact reversibility.
    - **`aggregator.py`**: Implements Merkle Tree logic. This is the cryptographic foundation for the future blockchain anchoring (Phase 3).

### 🚀 How to Use (Quick Start)

#### 1. Installation
If you haven't already, run the setup script:
```powershell
.\setup_env.ps1
```
*Wait for dependencies to install.*

Activate the environment:
```powershell
.\.venv\Scripts\Activate.ps1
```

#### 2. Initialize a Repository
Create an empty Aethel repository in the current folder:
```bash
aethel init
```
*Output: Initialized empty Aethel-Git repository in ...\.aethel*

#### 3. Create a Version (Commit)
Run a commit. This simulates "work" (training) and saves a new version:
```bash
aethel commit -m "My first trained model version"
```
*Note: This will load the 1.1B parameter model, which might take a minute depending on your hardware. It saves the adapter to `.aethel/objects/`.*

#### 4. View History
See your commit log:
```bash
aethel log
```
*Output: A table showing Hash, Timestamp, Author, and Message.*

### 🛠 Technical Details
- **Base Model**: `TinyLlama/TinyLlama-1.1B-Chat-v1.0` (4-bit quantized).
- **Storage**: Adapters are stored in `.aethel/objects/<SHA256_HASH>/`.
- **Database**: Metadata is stored in `.aethel/repo.db` (SQLite).
- **Cryptography**: All artifacts are Content-Addressed (hashed) to ensure integrity.

---
**Next Steps (Phase 3)**: Decentralized Storage (IPFS) and Blockchain Anchoring.
