# 🎯 Aethel-Git Capstone Demo Guide

> **Complete step-by-step walkthrough** for demonstrating Aethel-Git's full lifecycle:  
> `init` → `train` → `commit` → `branch` → `checkout` → `status`

---

## 📋 Prerequisites

Before running the demo, ensure the following are installed and ready:

```bash
# 1. Activate the virtual environment
.\.venv\Scripts\Activate.ps1          # Windows PowerShell

# 2. Install dependencies (if not done already)
pip install -r requirements.txt
pip install datasets                   # For dataset downloading

# 3. Install aethel as a CLI tool
pip install -e .

# 4. Download the demo datasets
python setup_demo_data.py
```

After running `setup_demo_data.py`, your `datasets/` folder will contain:

```
datasets/
├── sst2/train.csv              (Sentiment Analysis - 2 classes)
├── rotten_tomatoes/train.csv   (Movie Reviews - 2 classes)
├── emotion/train.csv           (Emotion Detection - 6 classes)
├── ag_news/train.csv           (Topic Classification - 4 classes)
└── tweet_eval_hate/train.csv   (Hate Speech Detection - 2 classes)
```

---

## 🧹 Phase 0: Clean Slate

If you've run the demo before, clean up first:

```powershell
# Remove previous repository state
if (Test-Path .aethel) { Remove-Item -Recurse -Force .aethel }
```

---

## 🏗️ Phase 1: Repository Initialization

### Step 1.1 — Initialize the repository

```bash
aethel init --model distilbert-base-uncased
```

**What happens under the hood:**
1. Queries Hugging Face Hub for `distilbert-base-uncased` and retrieves the **immutable revision SHA** (`12040accade4e8a0f7...`).
2. Creates `.aethel/` directory with:
   - `HEAD` → points to `refs/heads/main`
   - `config.json` → stores `model_id`, `revision_hash`, and `author`
   - `repo.db` → empty SQLite database with `commits` table
   - `objects/` → empty content-addressable store
   - `refs/heads/main` → empty (no commits yet)
   - `workspace/` → staging area

**Why this matters:** Every collaborator who runs `aethel init --model distilbert-base-uncased` will get the exact same base model weights, ensuring **bit-exact reproducibility**.

### Step 1.2 — Verify the empty state

```bash
aethel status
```

**Expected output:**
```
No commits found.
```

### Step 1.3 — Inspect the internals

```powershell
# HEAD points to main branch
Get-Content .aethel\HEAD
# Output: ref: refs/heads/main

# Config stores the pinned model
Get-Content .aethel\config.json
# Output: { "model_id": "distilbert-base-uncased", "revision_hash": "12040acc...", "author": "..." }

# Branch ref is empty (no commits)
Get-Content .aethel\refs\heads\main
# Output: (empty)
```

---

## 📚 Phase 2: The First Skill — Sentiment Analysis (Linear History)

### Step 2.1 — Train the model on SST-2

```bash
aethel train --config train_yaml/sst2_config.yaml
```

**What happens:**
1. Loads `distilbert-base-uncased` at the pinned revision
2. Auto-detects model type → `SequenceClassification` with 2 labels
3. Infers LoRA target modules → `q_lin`, `v_lin` (DistilBERT's attention layers)
4. Creates LoRA adapter (rank=8, alpha=16) → only **0.3%** of parameters are trainable
5. Trains on 500 SST-2 samples for 1 epoch
6. Saves adapter artifacts to `.aethel/workspace/`

**Verify workspace contents:**
```powershell
dir .aethel\workspace
# adapter_model.safetensors   ← LoRA weights (~1.8 MB)
# adapter_config.json         ← LoRA configuration
# training_info.json          ← Hyperparameters + metrics
```

### Step 2.2 — Commit the trained adapter

```bash
aethel commit -m "Taught model sentiment analysis via SST-2"
```

**What happens step-by-step:**
1. **Hash** — SHA256 of `adapter_model.safetensors` → `<adapter_hash>` (e.g., `a3f7c2...`)
2. **Store** — All workspace files copied to `.aethel/objects/<adapter_hash>/`
3. **Metadata** — Commit JSON written as `commit.json` inside the same folder
4. **Database** — Row inserted into `repo.db` with commit_hash, parent_hash, message
5. **Branch** — `refs/heads/main` updated to point to the new commit hash
6. **Cleanup** — Workspace cleared

**Expected output:**
```
Commit successful!
Commit:  <64-char commit hash>
Adapter: <64-char adapter hash>
Stored:  .aethel/objects/<adapter_hash>
```

### Step 2.3 — Verify the object store

```powershell
# The commit created a folder named by the adapter hash
dir .aethel\objects

# Inside that folder: adapter files + commit metadata
dir .aethel\objects\<adapter_hash>
# adapter_model.safetensors
# adapter_config.json
# training_info.json
# commit.json               ← The commit metadata
```

### Step 2.4 — Inspect the commit metadata

```powershell
# Read the commit JSON
Get-Content .aethel\objects\<adapter_hash>\commit.json | python -m json.tool
```

You'll see:
```json
{
  "adapter_blob": "<adapter_hash>",
  "author": "...",
  "base_model": "distilbert-base-uncased",
  "message": "Taught model sentiment analysis via SST-2",
  "parent_hash": null,
  "revision_hash": "12040acc...",
  "timestamp": "2026-04-22T...",
  "training_info": { ... }
}
```

> **Key observation:** `parent_hash` is `null` because this is the first commit.

---

## 🌿 Phase 3: Branching — Parallel Universes

Now we'll create a branch to experiment with a different task (emotion detection) without affecting the main sentiment model.

### Step 3.1 — Create a new branch

```bash
aethel branch feat-emotions
```

**What happens:**
- Creates file `.aethel/refs/heads/feat-emotions` containing the same commit hash as `main`
- HEAD does **not** move (you're still on `main`)

**Verify:**
```powershell
# Both branches point to the same commit
Get-Content .aethel\refs\heads\main
Get-Content .aethel\refs\heads\feat-emotions
# Both output: <same commit hash>

# HEAD still on main
Get-Content .aethel\HEAD
# Output: ref: refs/heads/main
```

### Step 3.2 — Switch to the new branch

```bash
aethel checkout feat-emotions
```

**What happens:**
1. HEAD updated to `ref: refs/heads/feat-emotions`
2. Adapter files from the commit's object folder are **restored to workspace**

**Expected output:**
```
Switched to branch feat-emotions.
HEAD now points to commit: <commit_hash>
Restored adapter to workspace from: .aethel/objects/<adapter_hash>
```

**Verify:**
```powershell
Get-Content .aethel\HEAD
# Output: ref: refs/heads/feat-emotions

# Workspace now has the sentiment adapter restored
dir .aethel\workspace
# adapter_model.safetensors, adapter_config.json, training_info.json
```

---

## 🧠 Phase 4: The Second Skill — Emotion Detection

### Step 4.1 — Train on the Emotion dataset

```bash
aethel train --config train_yaml/emotion_config.yaml
```

**What happens:**
- Loads fresh `distilbert-base-uncased` base model
- Creates a **new** LoRA adapter for 6-class emotion classification
- Trains on 500 Emotion dataset samples
- Saves new adapter to workspace (replaces the old one)

### Step 4.2 — Commit the emotion adapter

```bash
aethel commit -m "Added emotion detection capabilities (6 classes)"
```

**What happens:**
- New adapter folder created in objects (different hash from the sentiment adapter)
- Commit has `parent_hash` pointing to the previous commit
- Branch `feat-emotions` advances to this new commit
- Branch `main` still points to the old sentiment commit

**Verify divergence:**
```powershell
# feat-emotions has advanced
Get-Content .aethel\refs\heads\feat-emotions
# Output: <new commit hash>

# main is unchanged
Get-Content .aethel\refs\heads\main
# Output: <old commit hash>
```

---

## 📊 Phase 5: Viewing History

### Step 5.1 — View the commit log

```bash
aethel status
```

**Expected output:**
```
                    Commit History
┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Hash     ┃ Timestamp              ┃ Author ┃ Message                     ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ b7e2f1a3 │ 2026-04-22 08:15:00   │ user   │ Added emotion detection     │
│ a3f7c2d8 │ 2026-04-22 08:05:00   │ user   │ Taught model sentiment via  │
└──────────┴───────────────────────┴────────┴────────────────────────────┘
```

### Step 5.2 — Visualize the branch topology

```
main           feat-emotions
  │                 │
  ▼                 ▼
  ┌──────────┐     ┌──────────────────┐
  │ a3f7c2d8 │─────│ b7e2f1a3         │
  │ SST-2    │     │ Emotion          │
  │ Sentiment│     │ 6-class          │
  └──────────┘     └──────────────────┘
       ↑
     (root)
```

---

## ⏪ Phase 6: Time Travel & Verification

### Step 6.1 — Switch back to main (remount sentiment adapter)

```bash
aethel checkout main
```

**What happens:**
1. HEAD changes to `ref: refs/heads/main`
2. The **sentiment** adapter files are restored to workspace (from the main branch's commit)
3. If you loaded the model, you'd get the SST-2 sentiment version — **not** the emotion version

**Expected output:**
```
Switched to branch main.
HEAD now points to commit: <sentiment commit hash>
Restored adapter to workspace from: .aethel/objects/<sentiment adapter hash>
```

**Verify the workspace has the sentiment adapter:**
```powershell
# Check training_info.json to confirm which dataset this adapter was trained on
Get-Content .aethel\workspace\training_info.json | python -m json.tool
# "dataset": "./datasets/sst2"
```

### Step 6.2 — Detached HEAD checkout (time travel to specific commit)

```bash
# Use the full 64-character commit hash from `aethel status`
aethel checkout <full-64-char-commit-hash>
```

**What happens:**
- HEAD becomes "detached" — it points directly to a commit hash instead of a branch
- Workspace is restored from that commit's adapter folder
- You can inspect the model at that exact point in history

**Expected output:**
```
HEAD is now detached at <commit_hash>.
Restored adapter to workspace from: .aethel/objects/<adapter_hash>
```

> ⚠️ **Warning:** In detached HEAD state, any new commits won't be on any branch. Create a branch first with `aethel branch <name>` if you want to continue working.

### Step 6.3 — Return to a branch

```bash
aethel checkout main
```

---

## 🔬 Phase 7: Advanced — Multiple Skills on Main

Let's add more skills to the main branch linearly:

```bash
# Make sure we're on main
aethel checkout main

# Train & commit topic classification
aethel train --config train_yaml/ag_news_config.yaml
aethel commit -m "Added topic classification via AG News (4 classes)"

# Train & commit hate speech detection
aethel train --config train_yaml/tweet_eval_hate_config.yaml
aethel commit -m "Added hate speech detection via Tweet Eval"

# View the complete history
aethel log
```

**Final commit graph:**
```
main (4 commits)                  feat-emotions (2 commits)
       │                                  │
       ▼                                  ▼
  [Tweet Eval Hate]                [Emotion 6-class]
       │                                  │
  [AG News 4-class]                       │
       │                                  │
  [SST-2 Sentiment] ◄────────────────────┘
       │
     (root)
```

---

## 📁 Object Store Anatomy

After the full demo, inspect the object store:

```powershell
dir .aethel\objects
```

You should see **4 folders** (one per unique adapter, assuming no deduplication):

```
.aethel/objects/
├── <sst2_adapter_hash>/
│   ├── adapter_model.safetensors
│   ├── adapter_config.json
│   ├── training_info.json
│   └── commit.json
├── <emotion_adapter_hash>/
│   ├── adapter_model.safetensors
│   ├── adapter_config.json
│   ├── training_info.json
│   └── commit.json
├── <ag_news_adapter_hash>/
│   └── ...
└── <tweet_eval_adapter_hash>/
    └── ...
```

Each folder is named by the **SHA256 hash** of the adapter weights file. This is content-addressable storage — if two training runs produce identical weights, they share the same folder (deduplication).

---

## ✅ Demo Checklist

| # | Command | Expected Result |
|---|---------|----------------|
| 1 | `aethel init --model distilbert-base-uncased` | Repository created, model pinned |
| 2 | `aethel status` | "No commits found" |
| 3 | `aethel train --config train_yaml/sst2_config.yaml` | Workspace populated |
| 4 | `aethel commit -m "SST-2 sentiment"` | Folder created in objects/ |
| 5 | `aethel branch feat-emotions` | New ref file created |
| 6 | `aethel checkout feat-emotions` | HEAD switched, workspace restored |
| 7 | `aethel train --config train_yaml/emotion_config.yaml` | New adapter in workspace |
| 8 | `aethel commit -m "Emotion detection"` | Second folder in objects/ |
| 9 | `aethel status` | Two commits shown |
| 10 | `aethel checkout main` | Workspace has SST-2 adapter |
| 11 | `aethel train --config train_yaml/ag_news_config.yaml` | AG News adapter |
| 12 | `aethel commit -m "Topic classification"` | Third folder in objects/ |
| 13 | `aethel log` | Full history displayed |

---

## 🎓 Key Talking Points for Presentation

1. **Storage Efficiency:** Each adapter is ~1.8MB vs ~250MB for the full model — **99% savings**
2. **Reproducibility:** Revision hash pinning ensures everyone uses the exact same base
3. **Content Addressing:** Folder names = SHA256 hashes → automatic integrity verification
4. **Branching:** Zero-cost branching (just a small ref file), enables parallel experiments
5. **Checkout:** Instantly restores any version's adapter to the workspace
6. **Decoupled Train/Commit:** Training and versioning are separate concerns, just like `git add` vs `git commit`

---

## 🛸 Phase 8: Detached HEAD & Rescue — The Rotten Tomatoes Experiment

This phase demonstrates what happens when you time-travel to a past commit and try to do new work from there — and how Aethel-Git protects you from silently losing it.

### The Setup — Get a Commit Hash to Travel To

```bash
# Get all commit hashes from the history
aethel status
```

Copy the **full 64-character hash** of the SST-2 commit (the first one, at the bottom of the list). We'll use it to go back in time.

---

### Step 8.1 — Enter Detached HEAD State

```bash
# Replace <COMMIT_HASH> with the actual 64-char hash
aethel checkout <COMMIT_HASH>
```

**What happens:**
- HEAD stops pointing to a branch and instead points directly to a commit hash
- The workspace is restored with the SST-2 adapter from that exact point in history

**Expected output:**
```
HEAD is now detached at <commit_hash>.
Restored adapter to workspace from: .aethel\objects\<adapter_hash>
```

**Verify:**
```powershell
Get-Content .aethel\HEAD
# Output: <64-char hash>   ← raw hash, NOT "ref: refs/heads/..."
```

> 💡 **Talking point:** This is identical to how `git checkout <hash>` works. You're now at an exact historical snapshot. The model in the workspace is the SST-2 adapter at that exact point.

---

### Step 8.2 — Train a New Skill From This Historic Point

You decide to train a Rotten Tomatoes movie-review model, branching off from this old SST-2 checkpoint.

```bash
aethel train --config train_yaml/rotten_tomatoes_config.yaml
```

Training proceeds normally — the workspace now has a fresh Rotten Tomatoes adapter.

---

### Step 8.3 — Try to Commit (The Warning Fires 🔥)

```bash
aethel commit -m "Added Rotten Tomatoes movie review sentiment"
```

**Expected output — Aethel blocks the commit and shows exactly what to do:**
```
⚠  Detached HEAD — commit blocked.

You are not on any branch.
HEAD points directly to commit: 1d6207414816562a...

Commits made in this state would be lost when you checkout another branch.
To save your work, create a new branch first:

  aethel branch <new-branch-name>
  aethel checkout <new-branch-name>
  aethel commit -m "Added Rotten Tomatoes movie review sentiment"
```

> 💡 **Talking point:** This is a **safety net**. Standard Git lets you commit in detached HEAD state — but those commits become dangling and are garbage-collected. Aethel refuses entirely and tells you exactly what to do, preventing data loss by design.

---

### Step 8.4 — Rescue the Work (Create Branch & Commit)

Follow the instructions from the warning exactly:

```bash
# Step 1: Create a branch at the current (detached) position
aethel branch feat-rotten-tomatoes
```

**What happens:**
- Creates `.aethel/refs/heads/feat-rotten-tomatoes` pointing to the current detached commit hash
- HEAD is still detached (the branch is created, not checked out yet)

```bash
# Step 2: Switch to the new branch (HEAD becomes attached again)
aethel checkout feat-rotten-tomatoes
```

**Expected output:**
```
Switched to branch feat-rotten-tomatoes.
HEAD now points to commit: <sst2-commit-hash>
Restored adapter to workspace from: .aethel\objects\<sst2-adapter-hash>
```

> ⚠️ **Note:** Checkout restores the workspace from the branch's current commit (the SST-2 point where we branched from). The Rotten Tomatoes adapter you just trained is now **overwritten** in workspace!

```bash
# Step 3: Re-run training (workspace was reset by checkout), then commit
aethel train --config train_yaml/rotten_tomatoes_config.yaml
aethel commit -m "Added Rotten Tomatoes movie review sentiment"
```

**Expected output:**
```
✅ Commit successful!
Commit:   <new-commit-hash>
Adapter:  <rotten-tomatoes-adapter-hash>
Stored:   .aethel\objects\<rotten-tomatoes-adapter-hash>
Workspace: restored ✓
```

> 💡 **Note on workspace restore:** After every `aethel commit`, the workspace is automatically restored from the folder that was just committed. It is never left empty — you can always inspect what is currently "loaded".

---

### Step 8.5 — Verify the Branch Structure

```bash
aethel status
```

You'll now see **3 branches** diverging from the original SST-2 commit:

```
                    Branch Topology After Phase 8
                    
         ┌──── main ────────────────────────────────┐
         │                                           │
         │  ┌── feat-emotions ─────────────────┐     │
         │  │                                  │     │
         │  │  ┌── feat-rotten-tomatoes ────┐  │     │
         │  │  │                            │  │     │
[SST-2]──┴──┴──┴──[Rotten Tom]  [Emotion]  │  │  [AG News]─[Tweet Eval]
                                            │  │
                           (branched from SST-2 commit)
```

```powershell
# Confirm all 3 branch ref files exist
dir .aethel\refs\heads
# main
# feat-emotions
# feat-rotten-tomatoes
```

---

### Step 8.6 — Switch Between Branches to Demo Workspace Swap

```bash
# Load the main branch (AG News + Tweet Eval stack)
aethel checkout main
# Workspace: tweet_eval adapter (latest on main)

aethel checkout feat-emotions
# Workspace: emotion adapter (6-class)

aethel checkout feat-rotten-tomatoes
# Workspace: rotten tomatoes adapter (2-class)
```

Each checkout instantly swaps which adapter is in workspace. This is the **"parallel universes"** demo — three different AI capabilities, each isolated on its own branch, each restorable in seconds.

---

## ✅ Updated Demo Checklist

| # | Command | Expected Result |
|---|---------|----------------|
| 1–13 | *(Phases 1–7, as before)* | *(As shown above)* |
| 14 | `aethel checkout <commit-hash>` | Detached HEAD, workspace restored |
| 15 | `aethel train --config train_yaml/rotten_tomatoes_config.yaml` | Rotten Tom adapter in workspace |
| 16 | `aethel commit -m "..."` (while detached) | ⚠️ Warning printed, commit blocked |
| 17 | `aethel branch feat-rotten-tomatoes` | Branch created at detached position |
| 18 | `aethel checkout feat-rotten-tomatoes` | Branch attached, workspace restored |
| 19 | `aethel train --config train_yaml/rotten_tomatoes_config.yaml` | Re-train (workspace was reset) |
| 20 | `aethel commit -m "Rotten Tomatoes sentiment"` | ✅ Commit on branch, workspace restored |
| 21 | `aethel checkout main` / `feat-emotions` / `feat-rotten-tomatoes` | Instant workspace swap demo |

