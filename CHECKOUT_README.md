# Checkout README: Branch and Detached HEAD in Aethel-Git

Date: 2026-04-03

## Overview

This update introduces:

- `aethel checkout <target>`

The command supports two checkout modes:

1. Branch checkout (symbolic HEAD):
   - HEAD content becomes `ref: refs/heads/<branch_name>`
2. Detached HEAD checkout (commit hash):
   - HEAD content becomes `<commit_hash>`

## Core Behavior

### Target Resolution

`aethel checkout <target>` resolves target in order:

1. Branch path:
   - check `.aethel/refs/heads/<target>`
   - if present, read commit hash from branch ref
2. Commit path:
   - if not a branch, check `.aethel/objects/<target>`
   - parse as JSON commit object

If neither path resolves:

- `Fatal: target '<target>' did not match any files or commits.`

### Blob Verification

Before writing HEAD, checkout validates repository state integrity:

1. Read commit object JSON from `.aethel/objects/<commit_hash>`
2. Extract `adapter_blob`
3. Verify `.aethel/objects/<adapter_blob>` exists as a file

If missing:

- `Fatal: The model weights for this commit are missing from the object store.`

### HEAD Update Rules

- Branch target:
  - write `ref: refs/heads/<target>` into `.aethel/HEAD`
- Commit target:
  - write `<target>` into `.aethel/HEAD` (detached)

## Implementation Notes

- Uses `pathlib` for path-safe cross-platform operations.
- Uses context-managed file I/O through helper wrappers.
- Commit object integrity is validated before HEAD mutation.
- Detached HEAD is supported intentionally for time-travel/debug workflows.

## Files Added and Modified

### Added

- `aethel/commands/checkout.py`
- `CHECKOUT_README.md`

### Modified

- `aethel/main.py`

## Main App Registration Snippet

```python
from aethel.commands import init, commit, status, train, branch, checkout

app.add_typer(checkout.app, name="checkout")
```

## Usage Examples

```powershell
# switch to a branch
aethel checkout feat-math

# detach HEAD at specific commit
aethel checkout a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d0e1f2
```

## Suggested Next Step

Implement `aethel status --head` to display:

- current HEAD mode (branch or detached)
- current commit hash
- current branch name if symbolic
