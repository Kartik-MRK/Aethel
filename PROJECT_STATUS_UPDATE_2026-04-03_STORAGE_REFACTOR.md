# Project Status Child Update: Storage Refactor

Parent Report: [PROJECT_STATUS_REPORT.md](PROJECT_STATUS_REPORT.md)
Date: 2026-04-03
Change Type: Architecture Refactor (Object Storage Layout)

## Summary of Changes

This update refactors Aethel-Git's local object store from a folder-per-adapter layout to a Git-like flat object model.

Previous layout (problematic):

- `.aethel/objects/<adapter_hash>/adapter_model.safetensors`
- `.aethel/objects/<adapter_hash>/commit.json`

New layout (implemented):

- Blob Object: `.aethel/objects/<blob_hash>`
- Commit Object: `.aethel/objects/<commit_hash>`

## Why This Change Was Needed

The previous design stored both model weights and commit metadata in the same adapter-hash folder. If two commits produced identical adapter weights, metadata could be overwritten.

The new design separates:

- Blob objects: immutable model weight content
- Commit objects: immutable metadata snapshots that reference blob hashes

This preserves perfect deduplication while keeping commit history immutable.

## Detailed Implementation Changes

### 1) `aethel/commands/commit.py`

- Added robust storage helpers and explicit commit-storage error handling.
- Blob flow now:
  - Hash adapter weight file first
  - Save blob as flat file `.aethel/objects/<blob_hash>`
  - Deduplicate when blob file already exists
- Commit flow now:
  - Build metadata with `adapter_blob`, `author`, `message`, `parent_hash`, `timestamp`, `base_model`
  - Serialize with deterministic key sorting
  - Hash serialized JSON for `commit_hash`
  - Save commit JSON as flat object `.aethel/objects/<commit_hash>`
- SQLite insertion now records:
  - `hash = commit_hash` (primary identifier)
  - `adapter_cid = adapter_blob`
  - `metadata_cid = commit_hash`
- Removed legacy logic that created/stored per-hash directories for new commits.
- Added staging cleanup and graceful file/database error handling.
- Added compatibility guard for legacy collisions where a directory already exists at a blob hash path.

### 2) `aethel/core/model.py`

- `save_adapter` now returns success/failure and handles file I/O exceptions.
- Added `get_adapter_weights_path(adapter_dir)` to resolve the adapter weight file path (`.safetensors` preferred, `.bin` fallback).
- Added guards for missing model state before save.

## Files Modified

- `aethel/commands/commit.py`
- `aethel/core/model.py`

## New File Added

- `PROJECT_STATUS_UPDATE_2026-04-03_STORAGE_REFACTOR.md`

## Notes

- This refactor keeps Phase 2 behavior where `commit` still simulates a training step before snapshot creation.
- Architectural next step (recommended): separate patch-generation from commit snapshotting into distinct commands.
