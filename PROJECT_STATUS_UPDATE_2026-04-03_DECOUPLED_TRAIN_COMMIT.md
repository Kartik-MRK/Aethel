# Project Status Child Update: Decoupled Training and Commit

Parent Report: [PROJECT_STATUS_REPORT.md](PROJECT_STATUS_REPORT.md)
Date: 2026-04-03
Change Type: Command Architecture Refactor

## Summary

This update fully decouples training execution from version control commit creation.

The new lifecycle is:

1. Initialize repository with pinned immutable Hugging Face base model revision.
2. Run training via YAML into workspace artifacts.
3. Commit reads workspace artifacts only and performs pure content-addressed VCS persistence.

## What Changed

### 1) Init command now pins immutable base model revision

File modified:

- `aethel/commands/init.py`

Behavior changes:

- Added optional `--model` argument for repository initialization.
- If `--model` is not provided, user is prompted via Rich for model repo id.
- Uses `huggingface_hub.model_info()` to fetch latest `sha` revision hash.
- Writes both `model_id` and `revision_hash` into `.aethel/config.json`.
- Removed hardcoded model fallback behavior.
- Ensures `.aethel/workspace/` is created during init.

### 2) New train command introduced (YAML-driven)

File added:

- `aethel/commands/train.py`

Behavior changes:

- Added `aethel train --config <yaml_path>` command.
- Reads YAML fields:
  - `dataset`
  - `lora_rank`
  - `lora_alpha`
  - `batch_size`
  - `epochs`
- Loads base model and tokenizer using pinned `model_id` and exact `revision_hash` from `.aethel/config.json`.
- Sets up PEFT LoRA configuration using YAML parameters.
- Initializes a basic Hugging Face Trainer pipeline with a synthetic stub dataset scaffold.
- Saves adapter artifacts to `.aethel/workspace/`.
- Writes `.aethel/workspace/training_info.json` with training config and metrics.

### 3) Commit command is now pure VCS (no training)

File modified:

- `aethel/commands/commit.py`

Behavior changes:

- Removed all model loading, PEFT setup, and simulated training logic.
- Commit now reads only from `.aethel/workspace/`:
  - `adapter_model.safetensors` (or `.bin` fallback)
  - `training_info.json`
- Hashes adapter blob and stores deduplicated object at `.aethel/objects/<blob_hash>`.
- Builds commit metadata with:
  - `adapter_blob`
  - `author`
  - `message`
  - `parent_hash`
  - `timestamp`
  - `base_model`
  - `revision_hash`
  - `training_info`
- Hashes metadata JSON to produce `commit_hash` and stores commit object at `.aethel/objects/<commit_hash>`.
- Updates SQLite commit table with `hash = commit_hash`.
- Clears workspace after successful commit.

### 4) CLI and dependencies updated

Files modified:

- `aethel/main.py`
- `requirements.txt`
- `setup.py`

Behavior changes:

- Registered new CLI namespace: `aethel train`.
- Added explicit dependencies:
  - `huggingface_hub`
  - `pyyaml`

## Files Modified in This Update

- `aethel/commands/init.py`
- `aethel/commands/commit.py`
- `aethel/commands/train.py` (new)
- `aethel/main.py`
- `requirements.txt`
- `setup.py`

## Notes

- Existing legacy object directories under `.aethel/objects/<hash>/` from earlier format are still handled by commit compatibility logic.
- The new command separation now aligns with reproducibility goals by pinning immutable model revision and keeping commit as metadata plus object persistence only.
