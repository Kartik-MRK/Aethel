# Branching README: Aethel-Git Lightweight Branch Refs

Date: 2026-04-03

## Overview

This update introduces Git-style lightweight branching for Aethel-Git.

A branch is represented as a plain text file under:

- `.aethel/refs/heads/<branch_name>`

The file stores one value:

- a 64-character commit hash pointing to the branch tip.

The currently active branch is tracked by:

- `.aethel/HEAD`

Format:

- `ref: refs/heads/main`

## Core Behavior Implemented

### Command

- `aethel branch <branch_name>`

### Branch Creation Flow

1. Verifies repository exists (`.aethel/`).
2. Reads `.aethel/HEAD` and resolves active branch reference.
3. Reads active branch commit hash from ref file.
4. If active ref is empty or missing, fails with:
   - `Cannot create a branch from an empty repository. Please make your first commit.`
5. Validates branch name safety.
6. Fails on duplicate branch names.
7. Writes new ref file with same commit hash.
8. Does not modify HEAD.

## Additional Structural Improvements

To make branching robust and consistent:

- `aethel init` now ensures:
  - `.aethel/HEAD` exists
  - `.aethel/refs/heads/main` exists
- `aethel commit` now:
  - reads parent hash from active branch ref (HEAD-driven)
  - advances current branch ref to new commit hash after successful commit

This aligns commit ancestry with branch tips, instead of global timestamp ordering.

## Files Added and Modified

### Added

- `aethel/commands/branch.py`
- `BRANCHING_README.md`

### Modified

- `aethel/main.py`
- `aethel/commands/init.py`
- `aethel/commands/commit.py`

## Branch Name Rules

Allowed characters:

- letters, digits, `.`, `_`, `-`

Rejected cases include:

- empty names
- spaces
- path separators (`/`, `\\`)
- reserved dot names (`.` and `..`)
- names ending with `.lock`

## Main CLI Registration Snippet

```python
from aethel.commands import init, commit, status, train, branch

app.add_typer(branch.app, name="branch")
```

## Usage Example

```powershell
aethel init --model "distilbert/distilbert-base-uncased"
aethel train --config aethel_train.yaml
aethel commit -m "baseline adapter"
aethel branch experiment-lora-r16
```

## Notes

- Branch creation is metadata-only and O(1): no model copy is performed.
- `aethel branch` creates refs; it does not checkout/switch branches.
- Future `checkout` can build on this HEAD/ref design directly.
