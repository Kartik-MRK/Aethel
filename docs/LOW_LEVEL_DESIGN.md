# Aethel: Low-Level Design

Course UE23CS441A, Capstone Project Phase 3, Team 91.

This document specifies Aethel at the level of modules, data structures, algorithms, and
interfaces. The high-level design (what the layers are and why the project exists) is in
[PROJECT_PLAN.md](PROJECT_PLAN.md) and section 2 of [SYSTEM_REFERENCE.md](SYSTEM_REFERENCE.md).
This document answers the next question down: given those layers, what exactly does each one
store, compute, and promise.

Every signature, schema, and figure below was read off the working tree rather than written
from intent. Where a component is designed but not yet coded, it appears in section 13 and
nowhere else, so that no part of sections 1 to 12 describes something that does not run.

## Contents

1. [Scope and design principles](#1-scope-and-design-principles)
2. [Module decomposition](#2-module-decomposition)
3. [Data design](#3-data-design)
4. [Interface design](#4-interface-design)
5. [Algorithm design](#5-algorithm-design)
6. [Control flow](#6-control-flow)
7. [Concurrency, failure, and recovery](#7-concurrency-failure-and-recovery)
8. [Security design](#8-security-design)
9. [Performance design](#9-performance-design)
10. [Test design and traceability](#10-test-design-and-traceability)
11. [Deployment view](#11-deployment-view)
12. [Measured results](#12-measured-results)
13. [Designed, not yet implemented](#13-designed-not-yet-implemented)
14. [Glossary](#14-glossary)

---

## 1. Scope and design principles

### 1.1 What is being designed

Aethel versions LoRA adapters the way Git versions source. One frozen base model is referenced
but never stored. Each fine-tuning run produces a small adapter, and that adapter plus the
configuration and metrics that produced it becomes an immutable commit. Commits are published to
a Hub, which maintains an append-only transparency log over everything it has accepted and
periodically publishes the log's Merkle root to a public blockchain. A third party can then check
that a specific adapter is in a history that the Hub cannot quietly rewrite.

### 1.2 Design principles

These six rules decide most of the detailed choices in this document. Where a later section makes
a decision that looks odd, it is usually one of these being enforced.

| # | Principle | Consequence in the code |
|---|---|---|
| P1 | An object's name is the SHA-256 of its content | A name/content mismatch is always corruption, never a stale cache. Verification needs no separate checksum file. |
| P2 | The log is append-only by construction, not by policy | `TransparencyLog` exposes `append` and `append_many` and no update or delete. The property holds because there is no code path that could break it. |
| P3 | Leaves and internal nodes live in separate hash spaces | RFC 6962 domain separation. Without it an inclusion proof can be forged (section 5.5). |
| P4 | No database, no index | History is walked from refs through the object store. Nothing can fall out of sync, and `cp -r .aethel` is a complete backup. |
| P5 | The core never prints and never exits | `aethel.core` raises `AethelError`. Presentation and exit codes live only in `aethel/commands/_common.py`, which makes the core testable without a terminal. |
| P6 | The core has no ML dependency | `aethel.core`, `aethel.evaluation.protocol`, and the Merkle code import no torch and no transformers, so a CI job that installs neither still runs 347 tests. Importing either into the core is a build failure, by design. |

### 1.3 Explicit non-goals

Stated here so that section 13 is read as bounded remaining work rather than as an open list.

- Aethel does not store base model weights. It pins `model_id` plus an immutable `revision_sha`.
- Aethel does not claim a checkpoint proves honest training. It proves a history was not altered.
- Aethel targets one team with a shared push token. Per-user accounts and RBAC are out of scope.
- Aethel supports standard LoRA on sequence classification. DoRA, rsLoRA, quantised variants, and
  generation tasks are rejected explicitly rather than handled partially.

---

## 2. Module decomposition

### 2.1 Layers and the dependency rule

Dependencies point downward only. Nothing in a lower layer imports from a higher one, which is
what keeps the core independently testable.

```
  L1  aethel/commands/        CLI surface, Typer. Renders errors, sets exit codes.
        |                     Never contains storage or hashing logic.
        v
  L2  aethel/evaluation/      Training and scoring.      aethel/ml/
      aethel/remote/          Hub HTTP client.           adapter comparison
        |
        v
  L3  aethel/core/            Object store, commits, refs, atomic writes, Merkle tree.
        |                     No network, no ML, no printing.
        |
        +------------------> aethel/provenance/   IPFS mirror, EVM checkpoints.
        |                                         Depends on core hashing only.
        v
  L4  hub/                    FastAPI service, transparency log, dashboard.
                              Imports aethel.core; the CLI never imports hub.
```

`hub/` importing `aethel.core` rather than duplicating it is deliberate: the client and the server
must compute identical hashes, and the only way to guarantee that is one implementation.

### 2.2 Module inventory

Line counts are `wc -l` over the working tree.

| Module | Path | Lines | Responsibility |
|---|---|---|---|
| Core storage | `aethel/core/` | 1,600 | Content-addressed object store, commit construction and traversal, refs and HEAD, atomic writes and locking, Merkle tree, hashing, error taxonomy, workspace handling |
| CLI | `aethel/commands/` | 1,738 | 12 registered commands, argument parsing, error rendering, exit codes |
| Evaluation | `aethel/evaluation/` | 455 | Deterministic split protocol, dataset loading, inference, accuracy and macro-F1, parent comparison |
| Adapter comparison | `aethel/ml/` | 109 | Effective LoRA update difference with a bounded memory footprint |
| Provenance | `aethel/provenance/` | 465 | IPFS pinning and retrieval, EVM checkpoint publication, independent verification, persisted job state |
| Remote client | `aethel/remote/` | 452 | Hub HTTP client, negotiation, verified upload and download |
| Hub | `hub/` | 3,818 | FastAPI app, 19 JSON routes, 6 rendered pages, transparency log, anchor store, provenance views |
| Contract | `contracts/` | 89 | `AethelCheckpoint.sol` and its compile script |
| Operations | `scripts/` | 1,241 | Review server, model preparation, publication, dashboard checks |
| Tests | `tests/` | 8,470 | 663 test functions in 31 files |
| Frontend assets | `hub/templates/`, `hub/static/` | 4,300 | Jinja templates, CSS, browser-side proof verifier |

Implementation excluding tests and assets is 10,005 lines of Python and Solidity.

### 2.3 Core module responsibilities

| File | Exports | Contract it enforces |
|---|---|---|
| `hashing.py` | `canonical_json`, `hash_bytes`, `hash_text`, `hash_json`, `hash_file`, `is_valid_hash`, `normalize_hash` | One logical object has exactly one hash, in one spelling (lowercase hex, 64 chars) |
| `objects.py` | `ObjectStore` | Reads verify that content hashes to its name; writes are atomic; names are sharded two hex characters deep |
| `commits.py` | `build_base_object`, `create_commit`, `walk_history`, `find_adapter_filename`, `require_staged_adapter` | A commit is keyed by its own hash; the tree records the whole workspace |
| `refs.py` | branch read/write, HEAD resolution, detached-HEAD detection | A ref update takes a lock and is atomic |
| `atomic.py` | `atomic_write_text`, `atomic_write_bytes`, `atomic_copy_file`, `file_lock` | The four-step durable write of section 5.2 |
| `aggregator.py` | `MerkleTree`, `hash_leaf`, `hash_node`, `verify_proof` | Domain-separated hashing, odd-node promotion |
| `repo.py` | `Repo` | Repository discovery by walking upward for `.aethel` |
| `workspace.py` | `workspace_files`, `staged_workspace` | A commit reads the workspace through a guarded context and never writes to it |
| `errors.py` | `AethelError` plus 10 subclasses | Every core failure is an `AethelError` subclass |

---

## 3. Data design

### 3.1 Repository layout

```
.aethel/
  HEAD                         "ref: refs/heads/main"  or a raw 64-char commit hash
  config.json                  pinned base model reference and author
  refs/heads/<branch>          exactly one commit hash, newline terminated
  refs/heads/<branch>.lock     present only while a ref update is in flight
  objects/blobs/<ab>/<rest>    raw file bytes
  objects/trees/<ab>/<rest>    canonical JSON manifest
  objects/commits/<ab>/<rest>  canonical JSON commit
  objects/bases/<ab>/<rest>    canonical JSON base reference
  workspace/                   staging area written by `aethel train`
```

`<ab>` is the first two hex characters of the hash and `<rest>` the remaining 62, so no directory
accumulates tens of thousands of entries. The split is presentational only; the object's name is
the full 64-character digest.

### 3.2 The four object types

| Type | Content | Keyed by | Deduplicates |
|---|---|---|---|
| blob | raw bytes of one file | SHA-256 of the bytes | yes, across all commits and branches |
| tree | `{filename: blob_hash}` | SHA-256 of canonical JSON | yes, identical workspaces share a tree |
| commit | metadata below | SHA-256 of its own canonical JSON | no, by design |
| base | `{schema, source, model_id, revision_sha}` | SHA-256 of canonical JSON | yes, all commits on one base share one object |

**Why commits are keyed by their own hash rather than by the adapter hash.** This is the single
most consequential storage decision. Blobs should deduplicate by content. But if commit metadata
were also addressed by the adapter's hash, then two commits with byte-identical weights would
share one metadata slot and the second would overwrite the first. Checking out the first commit
would return the second commit's message, author, and parent. A wrong parent is a wrong history
DAG, and that DAG is what gets anchored on chain, so the result would be a tamper-evident record
of false lineage. Keying a commit by its own hash makes the collision impossible to express while
preserving weight deduplication.

### 3.3 Commit schema, version 2

```json
{
  "schema": 2,
  "parent_hash": "<64 hex> | null",
  "tree": "<64 hex>",
  "adapter_blob": "<64 hex>",
  "base": "<64 hex>",
  "message": "SST-2 sentiment baseline",
  "author": "johndoe",
  "timestamp": "ISO-8601 with offset",
  "training_info": { }
}
```

Field rules enforced on read by `HubStorage.validate_history` and by `aethel fsck`:

| Field | Rule |
|---|---|
| `schema` | must equal 2; any other value is `CorruptObject` |
| `parent_hash` | key must be present; value is `null` for a root commit or a valid digest |
| `tree`, `adapter_blob`, `base` | valid digests, and `adapter_blob` must equal the tree's adapter entry |
| `message`, `author`, `timestamp` | must be strings |
| `training_info` | must be a dict |

`adapter_blob` is redundant with the tree and is stored anyway. It lets the Hub, the dashboard,
and the IPFS mirror name the weights without reading the tree object, and the redundancy is
checked rather than trusted: a commit whose `adapter_blob` disagrees with its tree is rejected.

### 3.4 Training record

`training_info` is the commit's claim about what produced the adapter. It is written to
`workspace/training_info.json` by `aethel train` and copied verbatim into the commit, so the record
and the weights are hashed together and cannot drift apart.

```json
{
  "model_id": "distilbert/distilbert-base-uncased",
  "revision_sha": "12040accade4e8a0f71eabdb258fecc2e7e948be",
  "target_modules": ["q_lin", "v_lin"],
  "dataset_file": "data/sst2_600.csv",
  "data_manifest": { },
  "stub": false,
  "initialization": {"kind": "base", "revision_sha": "1204..."},
  "metrics": { },
  "timestamp": "ISO-8601 with offset",
  "environment": {
    "python": "3.13.x",
    "torch": "...", "transformers": "...", "peft": "...",
    "accelerate": "...", "safetensors": "...",
    "device": "cpu", "precision": "float32"
  },
  "evaluation": {
    "status": "measured",
    "current": {
      "accuracy": 0.5917, "macro_f1": 0.5017, "sample_count": 120,
      "confusion_matrix": [[ ], [ ]],
      "adapter_size_mb": 2.54,
      "adapter_sha256": "<digest of adapter_model.safetensors>",
      "adapter_config_sha256": "<digest of adapter_config.json>",
      "evaluation_spec": { }
    },
    "parent": null,
    "comparison": null
  }
}
```

Three parts of this envelope are load-bearing rather than informational.

`environment` records the exact versions of torch, transformers, peft, accelerate, and
safetensors, plus the device and precision. Without it, "the numbers differ on my machine" has no
diagnosable cause. It is evidence for a reproduction attempt, not a guarantee of one.

`initialization` distinguishes an adapter trained from the frozen base from one continued from an
earlier adapter. A branch that silently resumed from a parent adapter would make the parent
comparison meaningless.

`evaluation.current.adapter_sha256` and `adapter_config_sha256` are what the commit-time rebinding
check in section 5.4 compares against. They are the mechanism that stops metrics from one adapter
being attached to another.

`stub` records that training ran on synthetic data under `--allow-stub`, in which case
`evaluation.status` is `"skipped"` with a reason rather than a fabricated score. A stub run cannot
accidentally present itself as a measured one.

### 3.5 Dataset manifest

`data_manifest` is the part that makes evaluation defensible, so it is specified separately.

```json
{
  "schema": 1,
  "algorithm": "stratified-unique-text-v1",
  "dataset_file": "<path>",
  "raw_sha256": "<digest of the CSV as bytes>",
  "examples_sha256": "<digest of the parsed, deduplicated examples>",
  "unique_examples": 600,
  "selected_examples": 600,
  "text_column": "text",
  "label_column": "label",
  "label_ids": [0, 1],
  "seed": 42,
  "max_samples": 0,
  "validation_fraction": 0.2,
  "test_fraction": 0.2,
  "splits": { "train": [ ], "validation": [ ], "test": [ ] }
}
```

Two digests rather than one is deliberate. `raw_sha256` detects any byte change to the file.
`examples_sha256` covers the parsed and deduplicated example list, so it stays stable across
irrelevant reformatting (line endings, a trailing newline, BOM) while still changing if any text
or label changes. `load_split` rebuilds the manifest from the file on disk and compares six
fields before returning rows, so a dataset edited after training makes evaluation fail loudly
instead of silently scoring different data.

### 3.6 Evaluation specification

Produced by `evaluation_spec(info, split)` and hashed into its own identity so two evaluation
records can be compared only when they scored the same examples.

```json
{
  "schema": 1,
  "evaluator": "classification-v1",
  "split": "validation",
  "examples_sha256": "...",
  "indices_sha256": "<digest of that split's index list>",
  "label_ids": [0, 1],
  "label_names": ["negative", "positive"],
  "model_id": "distilbert/distilbert-base-uncased",
  "revision_sha": "12040accade4e8a0f71eabdb258fecc2e7e948be",
  "max_length": 128,
  "sample_count": 120,
  "hash": "<digest of every field above>"
}
```

The trailing `hash` is the comparison key. `aethel eval` refuses to compare two commits whose spec
hashes differ, which is what stops the most common and most embarrassing evaluation error:
reporting that one adapter beat another when they were scored on different rows.

### 3.7 Hub layout

```
<data-dir>/
  objects/{blobs,trees,commits,bases}/<ab>/<rest>    same format as a repository
  repos.json                                          repository index, branch tips
  log.jsonl                                           one accepted commit per line
  log.lock                                            append lock
  anchors.jsonl                                       verified checkpoint records
  mirrors.jsonl                                       IPFS mirror records
  jobs/                                               persisted transaction state
```

A log entry is fixed-shape and hashed nowhere, because the leaf is the commit hash alone:

```json
{"accepted_at": "ISO-8601", "commit_hash": "<64 hex>", "index": 0, "repo": "sst2"}
```

Two invariants are checked on every read of the log: indices must be consecutive from zero, and
commit hashes must be unique. Either violation raises `CorruptObject` rather than being repaired,
because a transparency log that silently heals is not a transparency log.

### 3.8 On-chain state

`contracts/AethelCheckpoint.sol`, pragma 0.8.30, non-upgradeable.

```solidity
struct Checkpoint {
    bytes32 root;
    uint64  blockNumber;
    uint64  previousSize;
}

address public owner;
address public pendingOwner;
bytes32 public logId;
uint64  public latestSize;
mapping(uint64 => Checkpoint) public checkpoints;

event Published(bytes32 indexed logId, uint64 indexed size, bytes32 root, uint64 previousSize);
```

| Decision | Reason |
|---|---|
| `logId` is set in the constructor and has no setter | One deployment serves exactly one log. A mutable log identity would let the owner retarget the contract at a different history and reuse its confirmations. |
| `size` must strictly increase | A checkpoint can only ever extend the log. |
| `publish` takes `expectedPreviousSize` | Optimistic concurrency. A second publisher racing on stale state reverts with "checkpoint changed" instead of overwriting. |
| No delete and no overwrite of `checkpoints[size]` | Every published root stays readable forever, which is what makes the prefix check in section 5.9 possible. |
| Two-step ownership (`proposeOwner`, then `acceptOwnership`) | A typo in an address cannot strand the contract. |
| No `repoId` | The log spans repositories. Per-repository anchoring would multiply gas cost and prove less. |

---

## 4. Interface design

### 4.1 Command-line interface

12 commands, registered in `aethel/main.py` as 10 Typer sub-applications plus `status` and `train`
declared directly. Exit code 0 on success, 1 for any `AethelError`, 2 for a usage error from
Typer. Anything that is not an `AethelError` propagates with its traceback, because an unexpected
exception is a bug and hiding it behind a tidy message is how bugs survive.

| Command | Arguments and options | Effect |
|---|---|---|
| `init` | `--model <owner/name>`, `--author` | Creates `.aethel/`, resolves and pins the base revision SHA, writes the base object |
| `train` | `--config/-c <yaml>`, `--allow-stub`, `--force` | Runs fine-tuning, writes adapter and records into `workspace/` |
| `commit` | `-m/--message <text>`, `--require-evaluation` | Snapshots the workspace as blobs, a tree, and a commit; advances the branch |
| `branch` | `[name] [start-point]`, `--delete/-d` | Lists, creates, or deletes a branch |
| `checkout` | `<target>`, `--force` | Restores a branch, commit, or short hash into the workspace |
| `log` | `[target]`, `--limit/-n 20`, `--all` | Walks history from a ref or commit |
| `status` | | Branch, HEAD state, staged workspace contents |
| `fsck` | `--verbose/-v` | Re-hashes every object, reports unreachable and corrupt ones |
| `eval` | `[target]`, `--split validation\|test`, `--json` | Scores a workspace or commit on its recorded held-out split |
| `diff` | `<left> <right>`, `--json` | Compares effective LoRA updates between two commits |
| `push` | `--remote`, `--repo`, `--branch`, `--token`, `--dry-run` | Publishes a branch and its ancestry to a Hub |
| `provenance` | 7 subcommands below | IPFS mirrors and EVM checkpoints |

`provenance` subcommands: `deploy`, `anchor`, `mirror`, `verify`, `fetch-blob`, `status`, `sync`.

Three interface decisions worth defending:

**`train` imports its implementation lazily, inside the function body.** This is principle P6
enforced at the CLI boundary. A base install has no torch, and `aethel --help`, `aethel log`, and
`aethel fsck` must still work there. A module-level import of the training code would break every
command in the program because one of them needs an optional extra. The `ImportError` is caught and
turned into the exact `pip install 'aethel[ml]'` line rather than a traceback.

**`checkout` and `branch` are registered with `allow_interspersed_args`.** They are Click groups
and would otherwise reject `aethel checkout main --force` while accepting the same options in the
reverse order. Nobody types them in the reverse order under demo pressure.

**`eval --split` defaults to `validation`.** The test split has to be asked for by name. Making
the held-out set slightly inconvenient to reach is the cheapest available guard against reporting
a tuned test score, and the help text says so.

`push --dry-run` performs negotiation and reports the delta without uploading, which is the
rehearsal path for the demo.

### 4.2 Error taxonomy

All ten are `AethelError` subclasses. The command layer catches the base class once and
discriminates only where the recovery advice differs.

| Exception | Raised when | What the user is told |
|---|---|---|
| `RepositoryNotFound` | no `.aethel` at or above the path | run `aethel init` |
| `RepositoryCorrupt` | repository metadata unusable | the specific unreadable file |
| `ObjectNotFound` | an object is absent from the store | the missing digest and kind |
| `CorruptObject` | content does not hash to its name | corruption or tampering, never a cache |
| `InvalidHash` | a value is not a 64-char lowercase hex digest | the offending value |
| `InvalidRef` | a ref is malformed, missing, or escapes the repository | the ref name |
| `DetachedHead` | HEAD points at a commit, and a commit was attempted | the three commands that recover the work |
| `BranchExists` / `BranchNotFound` | branch name collision or absence | the branch name |
| `LockTimeout` | a ref lock was not acquired before the deadline | which lock, and that a concurrent command may be running |

`DetachedHead` carries `commit_hash` as an attribute specifically so `_common.py` can print the
branch-then-checkout-then-commit recovery sequence. Aethel blocks the commit rather than warning,
which is stricter than Git and the right call here: a commit made on a detached HEAD is referenced
by nothing and is orphaned by the next checkout.

One further subclass, `InitError`, is declared in `aethel/commands/init.py` rather than in the
core, because model-revision resolution is a network concern that the core deliberately does not
have. It still inherits `AethelError`, so it renders and exits through the same single boundary.

### 4.3 HTTP interface

19 JSON routes under `/api/v1` and 6 server-rendered pages. Write routes are gated by
`AETHEL_HUB_TOKEN` when it is set.

| Method | Path | Auth | Success | Notable failure |
|---|---|---|---|---|
| `GET` | `/api/v1/version` | open | 200 | |
| `POST` | `/api/v1/repos/{repo}/negotiate` | token | 200, missing objects by kind | 401 without token |
| `PUT` | `/api/v1/blobs/{sha256}` | token | 201 with echoed digest | 422 on hash mismatch |
| `PUT` | `/api/v1/{kind}/{sha256}` | token | 201 with echoed digest | 422 on hash mismatch or non-canonical bytes |
| `POST` | `/api/v1/repos/{repo}/refs` | token | 200 with log indices and new root | 409 if ancestry is incomplete or the ref conflicts |
| `GET` | `/api/v1/repos` | open | 200 | |
| `GET` | `/api/v1/repos/{repo}` | open | 200 | 404 |
| `GET` | `/api/v1/repos/{repo}/commits` | open | 200, `?branch=` filter | 404 |
| `GET` | `/api/v1/commits/{sha256}` | open | 200 | 404 |
| `GET` | `/api/v1/trees/{sha256}` | open | 200 | 404 |
| `GET` | `/api/v1/bases/{sha256}` | open | 200 | 404 |
| `GET` | `/api/v1/blobs/{sha256}` | open | 200, streamed | 404 |
| `GET` | `/api/v1/log` | open | 200, size, root, entries, anchor state | |
| `GET` | `/api/v1/log/proof/{commit}` | open | 200, self-contained proof | 404 if not a leaf |
| `POST` | `/api/v1/log/verify` | open | 200, boolean | never 500 on malformed input |
| `GET` | `/api/v1/provenance` | open | 200, chain and mirror state | |
| `GET` | `/api/v1/mirrors/{sha256}` | open | 200, CID record | 404 |
| `GET` | `/api/v1/checkpoints/{size}/bundle/{commit}` | open | 200, downloadable proof bundle | 404 |
| `GET` | `/api/v1/health` | open | 200, eight checks | 503 if any check is critical |

Pages: `/`, `/r/{repo}`, `/c/{commit}`, `/provenance`, `/ops`, `/api`.

Two interface decisions worth defending:

**Blobs are raw request bodies, not multipart.** Content addressing makes the filename
irrelevant, so multipart would add a parser and a field that must then be deliberately ignored.

**`POST /api/v1/log/verify` is explicitly not authoritative.** Asking the Hub whether the Hub is
honest proves nothing. The route exists as a convenience and a test hook; the real check is the
browser-side verifier and `aethel provenance verify`, both of which recompute the root locally.

### 4.4 Remote client interface

`aethel/remote/http.py`, class `HubClient`, used as a context manager.

```python
version()                                 -> dict
negotiate(repo, have: dict[str, list[str]]) -> dict[str, list[str]]   # what the Hub lacks
put_blob(blob_hash, path)                 -> dict
put_object(kind, object_hash, data)       -> dict
update_ref(repo, branch, commit_hash)     -> dict
inclusion_proof(commit_hash)              -> dict | None
fetch_blob(blob_hash)                     -> Iterator[bytes]
```

`_verify_echo` re-checks the digest the Hub echoes back on every upload, so a lying or buggy
remote is caught at the client as well as at the server.

---

## 5. Algorithm design

### 5.1 Canonical serialization

```python
json.dumps(obj, sort_keys=True, separators=(",", ":"),
           ensure_ascii=False, allow_nan=False)
```

Each flag is load-bearing. `sort_keys` means key insertion order cannot change a hash.
`separators` removes incidental whitespace. `allow_nan=False` rejects `NaN` and `Infinity`, which
are not valid JSON and would produce output no other parser accepts. `ensure_ascii=False` emits
real UTF-8 rather than `\uXXXX` escapes, which matches JavaScript's `JSON.stringify` and is what
lets the browser-side verifier re-derive identical hashes. That last choice is the reason the
Python and JavaScript proof implementations agree on all 306 parity cases.

### 5.2 Durable write

Four steps, each one guarding a specific failure:

1. Write to a temporary file **in the same directory**, because `os.replace` is atomic only within
   one filesystem and `/tmp` is frequently a different mount.
2. `fsync` the temporary file, otherwise the rename can reach disk before the data and leave a
   correctly named empty file.
3. `os.replace`, which is atomic on POSIX and on Windows, unlike `os.rename`, which fails on
   Windows when the destination exists.
4. `fsync` the parent directory, so the rename itself survives power loss.

### 5.3 Object write and read

```
put(kind, content):
    digest <- SHA256(content)
    path   <- objects/<kind>/<digest[:2]>/<digest[2:]>
    if path exists: return digest            # content addressed, so already correct
    durable_write(path, content)
    return digest

get(kind, digest):
    content <- read(path(kind, digest))
    if SHA256(content) != digest: raise CorruptObject
    return content
```

Verification on read is unconditional. The cost is one hash of data already in memory, and it
converts silent bit rot into a named error at the point of use.

### 5.4 Commit construction

```
commit(message):
    branch <- require_attached_branch()      # raises DetachedHead before any write
    parent <- read_branch(branch)
    require_staged_adapter(workspace)        # fail before writing anything
    tree   <- write_tree_from_directory(workspace)    # blobs, then the tree manifest
    files  <- read_tree(tree)
    adapter_blob <- files[find_adapter_filename(files)]
    if recorded evaluation names a different adapter or adapter_config digest:
        raise InvalidRef                     # the workspace moved after evaluation
    base   <- put("bases", canonical_json(base_reference))
    body   <- {schema: 2, parent_hash: parent, tree, adapter_blob, base,
               message, author, timestamp, training_info}
    commit <- put("commits", canonical_json(body))    # keyed by its own hash
    with lock(refs/heads/<branch>):
        durable_write(refs/heads/<branch>, commit)     # ref moves LAST
    return commit
```

Three orderings matter.

`require_attached_branch` and `require_staged_adapter` both run before any write, so the two most
common user errors (committing on a detached HEAD, and committing before training) do not leave
unreferenced blobs behind on their way to an error.

The evaluation rebinding check is the one that prevents a specific dishonesty, whether accidental
or not. `aethel eval` records the digest of the adapter and the adapter config it scored. If the
workspace changed after that, the commit would attach real metrics to different weights. The
commit refuses instead, which is the same idea as the manifest rebinding in section 5.10 applied
one layer up.

The ref advances last, so an interrupted commit leaves unreachable objects that `fsck` reports,
rather than a branch pointing at objects that do not exist. The second failure is unrecoverable;
the first is harmless garbage.

### 5.5 Merkle tree

```
hash_leaf(d)        = SHA256(0x00 || d)
hash_node(l, r)     = SHA256(0x01 || l || r)
```

Domain separation is the security-critical part, and it was a fix to a real defect rather than a
precaution. The original implementation hashed leaves and internal nodes identically, which makes
a tree built over another tree's internal nodes produce the same root:

```
real   = MerkleTree([l0, l1, l2, l3])
forged = MerkleTree([H(l0+l1), H(l2+l3)])
real.get_root() == forged.get_root()        # True, under the old scheme
```

An operator could therefore serve a valid-looking inclusion proof for a value that was never a
committed leaf, which is precisely the forgery anchoring exists to prevent. The RFC 6962 tags fix
it by making the two hash spaces disjoint.

Odd nodes are **promoted** unchanged to the next level rather than duplicated. Duplicating the
last node is the Bitcoin behaviour behind CVE-2012-2459, in which two distinct leaf sets yield one
root. Promotion is what Certificate Transparency does.

Build cost is O(n) hashes and O(n) memory; proof length is ceil(log2 n).

### 5.6 Inclusion proof

Generation returns the leaf, its index, the log size, the root, and the sibling path with each
sibling tagged `left` or `right`. Verification recomputes upward:

```
verify(leaf, path, root):
    if not root: return False
    computed <- hash_leaf(leaf)
    for (sibling, side) in path:
        computed <- hash_node(sibling, computed) if side == "left"
                    else hash_node(computed, sibling) if side == "right"
                    else return False
    return computed == root
```

The verifier needs the leaf, the path, and the anchored root, and nothing else. It never contacts
the Hub, which is what makes the proof meaningful. `TransparencyLog.verify` returns `False` on a
malformed step rather than raising, because it runs on request bodies from anyone and "this does
not verify" is the honest answer to something that is not even shaped like a proof. Raising would
turn junk input into a 500 and make the Hub look broken instead of the proof.

Proofs can be requested at a historical size (`size` parameter), which is what lets a client
verify against an older anchored root rather than only the current one.

### 5.7 Push negotiation

```
client                                        Hub
  GET  /api/v1/version                        is this a Hub, and which build?
  POST /api/v1/repos/<r>/negotiate            here is everything I hold
                                              -> here is what I am missing
  PUT  /api/v1/blobs/<sha256>                 raw bytes
  PUT  /api/v1/{trees,bases,commits}/<sha256>  canonical JSON, byte for byte
  POST /api/v1/repos/<r>/refs                 move the branch, last
                                              -> log indices and the new root
```

Four rules make this trustworthy:

1. **The client names the hash, the server verifies it.** The Hub recomputes the digest from the
   bytes it actually received and rejects a mismatch with 422. The client re-checks the digest the
   Hub echoes back, so both ends catch a faulty path.
2. **Bytes are uploaded verbatim, never re-serialized.** Re-serializing anywhere could reorder
   keys and change a hash, and a hash that drifts in transit breaks every downstream proof.
3. **The server decides the delta.** The client offers what it has; the Hub answers with what it
   lacks. A Hub that lost an object self-heals on the next push rather than staying quietly
   incomplete.
4. **The ref moves last, after a re-walk.** `POST .../refs` re-walks the whole ancestry in the
   Hub's own store and returns 409 if anything is missing, so a published branch can never point
   at an object nobody can fetch.

### 5.8 Ancestry validation and its cost

`HubStorage.validate_history(commit_hash, *, accepted=None)` walks the full ancestry and checks
every field in section 3.3, every tree entry's blob, and every base reference.

Done naively this is quadratic across a session: each push re-validates the entire history behind
it, so pushing n commits one at a time costs O(n^2) object reads. The `accepted` parameter is the
fix. Commits already admitted to the transparency log were fully validated when accepted, and
object names are content hashes, so their trees and base references are not re-read. Their adapter
blob is still checked for existence, which catches a blob deleted after acceptance. Passing
`accepted=None` forces complete revalidation, and whole-store integrity remains a separate sweep
in `aethel fsck` and the Hub's integrity check.

Within a single walk, `verified_trees`, `verified_bases`, and `verified_blobs` memoise work, so a
branch whose commits share a base reads that base once.

### 5.9 Checkpoint publication and verification

Publication is a batched append. The Hub computes the root over the first `size` leaves and calls
`publish(size, root, expectedPreviousSize)`. Signed transaction bytes are persisted before
broadcast, so a crash between signing and confirmation is recoverable rather than ambiguous, and a
retry cannot produce a second distinct transaction for the same intent.

Independent verification is a ladder, and every rung must hold:

1. The RPC endpoint reports the expected `chainId`.
2. `eth_getCode` at the contract address matches the locally compiled bytecode byte for byte.
3. The contract's `logId` equals the expected log identity.
4. The checkpoint at the claimed size exists and is at least the required confirmation depth.
5. The claimed commit is at its claimed leaf index, and the sibling path recomputes the anchored
   root.
6. Every earlier checkpoint's root still recomputes from the current leaves, which is the
   append-only check: a rewritten prefix breaks an older root even if the newest one is consistent.

Step 2 is what makes the rest meaningful. Without it a verifier is trusting whatever contract
happens to sit at that address. CI recompiles the contract and diffs the artifact, so a source
change that would alter deployed behaviour fails the build.

Step 6 is the one most easily omitted and the one that carries the transparency property. Checking
only the newest root would let an operator rewrite history and publish a fresh consistent root
over the rewritten leaves.

### 5.10 Dataset split protocol

Algorithm `stratified-unique-text-v1`.

```
read_examples(csv, text_col, label_col):
    for each row:
        reject empty text, non-integer label, negative label
        key <- casefold(collapse_whitespace(text))
        if key seen with a different label: reject      # contradictory duplicate
        if key seen: skip                               # exact duplicate
    return examples in file order

build_manifest(...):
    validate fractions leave a training set
    indices <- range(len(examples)), optionally sampled to max_samples with seeded rng
    group indices by label
    reject labels that are not consecutive integers from zero, or fewer than two classes
    for each label, in sorted order:
        reject if fewer than 3 unique examples
        seeded shuffle of that label's members
        validation_count <- max(1, floor(n * validation_fraction))
        test_count       <- max(1, floor(n * test_fraction))
        reject if validation_count + test_count >= n
        take validation_count, then test_count, remainder is train
    sort each split's indices
    return examples, manifest
```

Three properties this buys, all of which are ordinary practice and all of which were absent
before:

- **Deduplication before splitting.** Near-duplicate text across train and validation is the
  classic way to report an inflated score. Normalising whitespace and case before the uniqueness
  check catches the realistic cases, and contradictory labels on the same text are rejected rather
  than silently resolved.
- **Stratification per label.** Each class is split at the same proportions, and the `max(1, ...)`
  floor guarantees every class appears in both held-out splits, so a small validation set cannot
  accidentally contain only one class. A label too small to satisfy that is rejected by name
  instead of being quietly merged or dropped.
- **Determinism from a recorded seed.** The split is a function of (file, columns, seed,
  max_samples, fractions), all of which are stored in the manifest, so it can be rebuilt exactly.

`load_split` rebuilds the manifest and compares `raw_sha256`, `examples_sha256`, `splits`,
`label_ids`, `selected_examples`, and `unique_examples` before returning rows. Any mismatch raises.

### 5.11 Metrics

`classification_metrics` builds the confusion matrix once and derives everything from it.

```
accuracy  = sum(C[i][i]) / N
f1_i      = 2*C[i][i] / (support_i + predicted_i)      # 0 when the denominator is 0
macro_f1  = fsum(f1_i) / num_labels
```

Per-class F1 is computed from the matrix rather than from separate precision and recall passes,
which avoids a second traversal and makes the zero-denominator case explicit instead of a division
guard in two places. `math.fsum` is used for the macro average because a plain sum over many
floats accumulates rounding error that would show up in the fourth decimal place of a reported
score. Labels and predictions outside the configured class range raise rather than being clamped.

### 5.12 Adapter comparison

A LoRA layer's update is `scale * B @ A` where `scale = alpha / r`. Comparing `A` and `B`
separately is meaningless, because the factorisation is not unique: `A -> QA`, `B -> BQ^-1` leaves
the product unchanged. So the comparison is over the **effective update**.

Materialising `B @ A` for every layer at once would be large. The algorithm accumulates four
scalars over row blocks of `B` instead, at `block_size` rows at a time:

```
for start in range(0, rows(B), block_size):
    L <- (B_left[start:start+block]  @ A_left)  * scale_left
    R <- (B_right[start:start+block] @ A_right) * scale_right
    left_sq       += sum(L^2)
    right_sq      += sum(R^2)
    difference_sq += sum((R - L)^2)
    dot           += sum(L * R)
```

From those four scalars: both norms, the distance, the relative change, and the cosine similarity.
Peak intermediate memory is O(block_size * out_features) rather than O(rows * out_features), and
accumulation runs in float64 on CPU regardless of the stored dtype, so a comparison of fp16
adapters does not report differences that are really rounding.

Compatibility is checked before any arithmetic, and unsupported configurations are rejected by
name rather than approximated: `use_dora`, `use_rslora`, `rank_pattern`, `alpha_pattern`,
`lora_bias`, `use_qalora`, and any non-`none` bias. Differing `task_type`, `fan_in_fan_out`,
target layers, or saved modules are errors. This is the honest boundary: the method is correct for
standard LoRA and says so rather than producing a number for cases it does not model.

### 5.13 IPFS mirror

Pinning goes through Pinata or a local Kubo node. Retrieval is the interesting half:

```
fetch(expected_digest, cid, gateways):
    for gateway in gateways:
        stream bytes, rejecting a response that exceeds the expected size
        if SHA256(bytes) == expected_digest: return bytes
        record the failure, keep the CID for retry
    raise, naming every gateway tried
```

A gateway is never trusted. The digest comes from the commit, which is anchored, so a gateway that
returns different bytes is detected rather than believed. Oversized responses are cut off during
streaming instead of after, which is what stops a hostile gateway from exhausting memory. Failed
retrievals retain their CID so a retry does not need a new pin request. During the live run shared
public gateways returned HTTP 429 and the account's dedicated gateway completed both retrievals,
with the SHA-256 check passing on each.

---

## 6. Control flow

### 6.1 Initialize

```
aethel init --model distilbert/distilbert-base-uncased
  |
  +-> resolve the model's current immutable revision SHA from the Hugging Face API
  +-> fail with InitError if no SHA can be resolved, rather than pinning a moving tag
  +-> create .aethel/, refs/heads/main (empty), HEAD -> ref: refs/heads/main
  +-> write config.json with model_id, revision_sha, author
  +-> write the base object, so the pinned reference is itself content addressed
```

Re-initialising preserves a previously configured author, so `init` is safe to rerun.

### 6.2 Train, evaluate, commit

```
aethel train --config train_yaml/sst2_config.yaml
  |
  +-> build_manifest(dataset)            deterministic stratified split, recorded
  +-> load base at the pinned revision   offline-capable once cached
  +-> attach a LoRA adapter, train one epoch on the train split
  +-> score the validation split         never the training rows
  +-> write workspace/adapter_model.safetensors
      workspace/adapter_config.json
      workspace/training_info.json       includes the manifest and the eval spec

aethel eval
  +-> load training_info, rebuild the recorded split, refuse a modified dataset
  +-> run inference, report accuracy, macro-F1, loss, confusion matrix

aethel commit -m "SST-2 baseline"
  +-> blobs, tree, base, commit          objects first
  +-> advance refs/heads/main            ref last, under a lock
```

### 6.3 Publish and anchor

```
aethel push --remote http://hub:8000 --branch main
  |
  +-> GET  /api/v1/version               confirm this is a Hub
  +-> POST negotiate                     offer local objects, learn the delta
  +-> PUT  each missing object           digest verified at both ends
  +-> POST refs                          Hub re-walks ancestry, appends leaves
  |                                      returns log indices and the new root
  v
aethel provenance anchor
  +-> compute the root over the first `size` leaves
  +-> persist signed transaction bytes BEFORE broadcast
  +-> publish(size, root, expectedPreviousSize)
  +-> wait for the required confirmation depth
  +-> append a verified record to anchors.jsonl
```

### 6.4 Independent verification

```
aethel provenance verify <commit> --bundle <file> --rpc <url> --chain-id 11155111
                         --contract 0xFe00...078E --log-id 053b...ede2
  |
  +-> chainId matches                                     else fail
  +-> eth_getCode == locally compiled bytecode            else fail
  +-> contract.logId == expected                          else fail
  +-> checkpoint(size) exists, confirmations >= required   else fail
  +-> leaf at claimed index, path recomputes root         else fail
  +-> every earlier checkpoint root still recomputes      else fail
  v
  verified
```

The bundle makes this runnable with no Hub. Given a saved bundle and any Sepolia RPC endpoint, a
third party reproduces the whole ladder, and the Merkle arithmetic in step 5 runs locally with no
network at all.

---

## 7. Concurrency, failure, and recovery

### 7.1 Lock inventory

| Lock | Guards | Acquired by |
|---|---|---|
| `refs/heads/<branch>.lock` | read-then-write of a branch tip | `commit`, `branch`, `checkout` |
| `log.lock` | read-then-append of the transparency log | `append_many`, `snapshot_entries` |
| `anchors.jsonl.lock` | anchor record rewrite | `AnchorStore.append` |

Every lock is `O_CREAT | O_EXCL` with a deadline, raising `LockTimeout` rather than blocking
forever. The ref lock exists because two concurrent commits would otherwise both read the same
tip and the second would silently discard the first. The log lock exists because two concurrent
pushes would otherwise compute the same next index and one leaf would overwrite the other.

`snapshot_entries` takes the append lock for a **read**, which is not redundant: it is what makes
a proof and a root come from the same prefix rather than from a log that grew between the two
reads.

**A crashed process leaves its lock file behind, deliberately.** The error names the exact path to
delete. Automatically clearing a lock whose owner might still be alive is worse than asking a
person to remove a stale file, because the automatic version reintroduces exactly the lost-update
race the lock exists to prevent. This is the same trade-off Git makes with `index.lock`.

### 7.2 Crash points

| Interrupted at | State left behind | Recovery |
|---|---|---|
| Between object writes | unreachable objects | `fsck` reports them; a retry reuses them by content address |
| After objects, before the ref | unreachable commit | same, and the commit is simply remade |
| Mid log append | trailing lines lost, earlier lines intact | append-only file, so no earlier leaf can be corrupted |
| After signing, before broadcast | persisted job with signed bytes | `provenance sync` rebroadcasts the same transaction |
| After broadcast, before confirmation | transaction in flight, no anchor record | `provenance sync` polls to the required depth |

Objects are written before refs, and the log is appended rather than rewritten. Both choices make
the failure mode "harmless garbage" instead of "dangling reference".

### 7.3 What `fsck` checks

Re-hashes every object and compares it to its name, walks every ref to confirm reachability,
reports unreachable objects, and reports any commit whose `adapter_blob` disagrees with its tree.
It repairs nothing, because a content-addressed store has no ambiguity to resolve: an object that
does not hash to its name is not a repairable version of itself.

---

## 8. Security design

### 8.1 Trust boundaries

```
  local repository        trusted by its owner, content-verified on every read
        | push (token-gated)
        v
  Hub                     NOT trusted for history integrity
        | Merkle root
        v
  public chain            trusted for ordering and immutability, not for meaning
        | proof bundle
        v
  independent verifier    trusts nothing, recomputes everything
```

### 8.2 Threat model

| Threat | Handled by | Residual risk |
|---|---|---|
| On-disk bit rot or edit | content verification on every read (5.3) | detected, not repaired |
| Hub silently rewrites history | earlier-checkpoint prefix check (5.9 step 6) | a rewrite before the first anchor is not detectable |
| Hub forges an inclusion proof | RFC 6962 domain separation (5.5) | none known for this construction |
| Two distinct leaf sets, one root | odd-node promotion, not duplication (5.5) | none known |
| Hostile contract at the expected address | `eth_getCode` bytecode comparison (5.9 step 2) | none, given a correct local compile |
| Concurrent publisher overwrites a checkpoint | `expectedPreviousSize` optimistic concurrency (3.8) | reverts, so a caller must retry |
| Gateway returns wrong adapter bytes | digest check against the anchored commit (5.13) | availability only, never authenticity |
| Gateway exhausts client memory | size ceiling enforced during streaming (5.13) | none |
| Stolen push token | token gates writes | no per-user attribution, so the log records that a push happened, not who pushed |
| Evaluation on contaminated data | dedupe before split, manifest rebinding (5.10) | semantic near-duplicates below the normalisation threshold |
| Comparing incomparable evaluations | spec hash equality required (3.6) | none |

### 8.3 What the chain does and does not prove

It proves that a specific commit was in the log at a specific size, that the recorded prefix has
not changed since, and that the contract holding those roots is the one whose source the team
published.

It does not prove that training was honest, that the reported metrics were produced by the stored
adapter, that the dataset was what the manifest claims, or that any particular person made the
commit. The first two need a reproduction path, the third needs a dataset fingerprint the verifier
can independently obtain, and the last needs authorship signatures. All three are named in section
13 rather than implied here.

`AETHEL_HUB_TOKEN` unset means an open Hub. The ops board reports that state as "open, no push
token set" rather than presenting it as security, which is the correct default for a laptop demo
and is not the correct default for anything else.

---

## 9. Performance design

| Operation | Cost | Design note |
|---|---|---|
| Object read | one hash of the bytes | unconditional verification, accepted cost |
| Object write | one hash, one durable write | skipped entirely if the digest already exists |
| History walk | O(n) commit reads | no index, so nothing to rebuild or desynchronise |
| Merkle build | O(n) hashes, O(n) memory | recomputed from the log rather than cached, because a stale tree in a provenance system is worse than a rebuild |
| Inclusion proof | ceil(log2 n) siblings | 2 KB of JSON at any realistic log size |
| Push of n commits | O(n) object reads with `accepted`, O(n^2) without | section 5.8 |
| Adapter comparison | O(rows * out_features) work, O(block * out_features) peak memory | blocked accumulation, section 5.12 |
| Chain verification | one `eth_getCode`, one `checkpoints` call per rung | results cached per `(chain_id, address, size)`; the unconfirmed prefix is never cached |
| Log read | full file read, uncached | the log is one short line per commit, and correctness beats a cache here |

The two caches in the system are both deliberately narrow. `ChainClient._checkpoint` caches only
**confirmed** records per `(chain_id, address, size)` and never caches the checked prefix, so a
reorg cannot be papered over. `validate_history`'s `accepted` set skips re-reading object graphs
that the log already admitted, and still checks blob existence.

Measured: 836 tests in about 15 seconds; core coverage 95.58 percent in the CI core job and 96.06
percent in the full development environment, against an 80 percent gate.

---

## 10. Test design and traceability

### 10.1 Strategy

663 test functions in 31 files, expanding to 836 collected cases. The suite is layered the way the
code is: the core is tested with no ML and no HTTP dependency, the Hub is tested through a real
ASGI client, the contract is executed on PyEVM, and the browser verifier is checked against the
Python implementation case by case.

Four CI jobs, each installing a different dependency set:

| Job | Installs | Purpose |
|---|---|---|
| `core` | `[dev]` only | Fails if the core ever acquires a torch or fastapi import. 347 passed, 158 skipped, 95.58 percent core coverage. |
| `hub` | `[dev,hub]` | Full HTTP surface. 790 passed, 13 skipped. |
| `ml` | `[dev,ml]`, CPU torch, `HF_HUB_OFFLINE=1` | Training and comparison paths without network. |
| `provenance` | `[dev,hub,provenance-test,fonts]` | Contract on PyEVM, plus `git diff --exit-code` on recompiled bytecode. 814 passed, 5 skipped. |

The `core` job is the unusual one and the most useful: its purpose is to fail. Principle P6 is
otherwise the kind of rule that decays in a week.

A module-level `importorskip` records the whole module as a single skip item, which is why skipped
and passed counts do not sum to the collected total. They are not missing tests.

### 10.2 Traceability

Function counts are per file, and they sum to exactly 663.

| Design element | Section | Tests | Functions |
|---|---|---|---|
| Canonical serialization, hashing | 5.1 | `test_core_hashing.py` | 20 |
| Durable write, locking | 5.2, 7.1 | `test_core_atomic.py` | 18 |
| Object store, verification on read | 5.3 | `test_core_objects.py` | 29 |
| Commit construction, history walk | 3.3, 5.4 | `test_core_commits.py` | 23 |
| Own-hash keying, defect S1.1 regression | 3.2 | `test_s1_corruption.py` | 8 |
| Refs, HEAD, detached-HEAD guard | 4.2 | `test_core_refs.py`, `test_core_resolve.py` | 25, 11 |
| Merkle domain separation, promotion | 5.5 | `test_core_merkle.py` | 18 |
| Inclusion proof, malformed input | 5.6 | `test_hub_log.py`, `test_hub_verify.py` | 28, 18 |
| Push negotiation, ancestry rejection | 5.7, 5.8 | `test_push.py`, `test_hub_api.py` | 41, 78 |
| Error taxonomy and HTTP mapping | 4.2, 4.3 | `test_hub_errors.py` | 50 |
| Write auth and token gating | 4.3, 8.2 | `test_hub_security.py`, `test_hub_credentials.py` | 21, 1 |
| Contract behaviour on PyEVM | 3.8, 5.9 | `test_provenance_chain.py`: ownership, stale updates, retries, confirmations, reorgs, altered prefixes | 11 |
| Chain state surfaced through the Hub | 5.9 | `test_provenance_hub.py` | 7 |
| IPFS retrieval and rejection | 5.13 | `test_provenance_ipfs.py`: corrupt bytes, oversized responses, retry state | 6 |
| Split protocol, manifest rebinding, metrics | 5.10, 5.11 | `test_evaluation_protocol.py`, `test_evaluation.py` | 12, 4 |
| Training pipeline end to end | 6.2 | `test_training_pipeline.py` | 5 |
| Adapter comparison | 5.12 | `test_adapter_diff.py` | 8 |
| Dashboard rendering and geometry | 4.3 | `test_hub_views.py`, `test_hub_motion.py`, `test_hub_fonts.py` | 104, 15, 14 |
| API documentation page | 4.3 | `test_hub_apidocs.py` | 21 |
| CLI argument handling | 4.1 | `test_cli_parsing.py` | 9 |
| Workspace safety | 6.2 | `test_workspace_safety.py` | 9 |
| Environment and configuration | 3.1 | `test_core_env.py` | 29 |
| Publication and evidence generation | 11 | `test_publication.py`, `test_commit_evaluation_display.py` | 18, 2 |
| Python/JavaScript proof parity | 5.1 | 306 generated parity cases, all agreeing | |

`test_s1_corruption.py` is worth reading in the viva. Its docstring records the four test names
that failed against the pre-rebuild layout, which is what makes section 3.2 a fixed defect with
evidence rather than a design preference.

---

## 11. Deployment view

```
  operator laptop                      demo machine
  +-------------------+                +----------------------------+
  | aethel CLI        |   HTTP push    | uvicorn + FastAPI Hub      |
  | .aethel/ repo     |--------------->| <data-dir>/                |
  +-------------------+                |   objects/ log.jsonl       |
                                       |   anchors.jsonl mirrors    |
                                       +-------------+--------------+
                                                     |
                                   +-----------------+-----------------+
                                   |                                   |
                            Pinata / Kubo                     Sepolia RPC
                            adapter mirror                  AethelCheckpoint
                                   |                         0xFe00...078E
                                   v                                   |
                            dedicated gateway                          v
                            retrieval + digest check         browser verifier
```

Packaged with systemd units, a worker timer, HTTPS proxy configuration, and recovery instructions.
A built wheel contains the compiled contract, Hub templates, JavaScript, CSS, and fonts, which is
checked in CI so the dashboard cannot ship half-assembled.

**Demo path.** The primary path is live: a real Sepolia RPC endpoint and the dedicated IPFS
gateway. The fallback needs no network at all: `aethel provenance verify --bundle
docs/evidence/checkpoint-2.json` recomputes the Merkle root locally and compares it to the saved
on-chain root, so the integrity argument survives a room with no internet. Nothing in the fallback
is simulated; only the RPC round trip is replaced by a previously saved and independently checked
response.

---

## 12. Measured results

Real runs, not fixtures. Base `distilbert/distilbert-base-uncased` at revision
`12040accade4e8a0f71eabdb258fecc2e7e948be`, 600 public rows from `stanfordnlp/sst2`, split
360/120/120 with seed 42, LoRA rank 4, alpha 8, one epoch. The test split was not scored.

| Training seed | Validation accuracy | Macro-F1 | Loss |
|---|---|---|---|
| 42 | 59.17% | 0.5017 | 0.671746 |
| 43 | 50.83% | 0.3370 | 0.672748 |

The second run regressed. These are small workflow pilots with newly initialised classification
heads, and they are reported as such: they demonstrate that the recording, comparison, and
publication path works end to end on real weights, not that the training recipe is good. Broader
tuning, repeated seeds, baselines, and a final untouched test-set evaluation are the remaining ML
work.

Provenance receipts, all publicly checkable:

| Field | Value |
|---|---|
| Network | Sepolia, chain ID 11155111 |
| Contract | `0xFe000a436a8Af301e96aFe3815Ab0ba0003C078E` |
| Log ID | `053b1222dab7d5c519cb146bd2661bd2287e17a0e9309a1e336ce9646758ede2` |
| Checkpoint size | 2 accepted commits |
| Root | `aeb33d38a626e681507a75cd94a78d8efd0a0fdf7dc95ac300196d3957cb4b28` |
| Block | 11764982 |
| Independent check | Confirmed through Tenderly, separately from the Hub's PublicNode RPC |

Both adapters are 2,667,272 bytes, pinned through Pinata, retrieved from the dedicated gateway,
and re-verified by SHA-256.

---

## 13. Designed, not yet implemented

Everything above describes code that runs. This section is the remainder, specified to the same
level so that it is a bounded backlog rather than an open question. Each item states the design,
not just the intent.

### 13.1 Verified clone, fetch, and fast-forward pull

The Hub already exposes every read route this needs (`/repos`, `/repos/{r}/commits`,
`/commits/{h}`, `/trees/{h}`, `/bases/{h}`, `/blobs/{h}`) and `HubClient.fetch_blob` already
streams with verification. What is missing is the client-side walk.

```
clone(remote, repo, destination):
    init destination from the remote's base reference
    tip <- GET /repos/<repo>            branch tips
    walk: for each commit from the tip, oldest last
        GET  /commits/<h>, verify digest, put into the local store
        GET  /trees/<h>,  verify digest, put
        GET  /bases/<h>,  verify digest, put
        GET  /blobs/<h>   for each tree entry, verify digest, put
    write refs/heads/<branch> last, under a lock

pull(remote, branch):
    remote_tip <- GET /repos/<repo>
    if remote_tip is already an ancestor of local: up to date, stop
    if local tip is NOT an ancestor of remote_tip: refuse, non-fast-forward
    fetch the missing objects as above
    advance the ref
```

Fast-forward only, with an explicit refusal otherwise. That is the honest bound until 13.2 lands,
because resolving a divergence needs a merge.

### 13.2 Two-parent history and one weighted merge

Commit schema 3 replaces `parent_hash` with `parents: [hash, ...]`, one entry for an ordinary
commit and two for a merge. Readers accept both schemas; writers emit 3. The ripple is known and
bounded: `walk_history` becomes a breadth-first traversal with a visited set, `validate_history`
validates a DAG rather than a chain, and the dashboard's history view needs a lane layout.

The merge itself operates on effective updates, reusing the compatibility gate in 5.12: refuse
unless both adapters target identical layers with identical `task_type` and rank-compatible
shapes, then combine `scale * B @ A` per layer at a stated weight, re-factor to the target rank,
and evaluate the result on the shared held-out split. The claim is one correct measured merge on
compatible adapters, not a general model-merging library.

### 13.3 Base fingerprinting

`revision_sha` pins the base, but it pins Hugging Face's word for it. A fingerprint over the
tokenizer files and the model config, stored in the base object and re-derived at verification
time, lets a verifier confirm the base independently. Cheap to add and it closes the third gap in
section 8.3.

### 13.4 Evaluation reproduction

Depends on 13.1 and 13.3. Fetch a pinned adapter by CID, confirm its digest against the anchored
commit, rebuild the recorded split, rerun inference, and compare to the recorded metrics within a
stated tolerance. This is what converts "the metrics are recorded" into "the metrics are
checkable", and it closes the first two gaps in section 8.3.

### 13.5 Advisory divergence detection

The Hub can see two accepted commits claiming the same parent. Reporting that as an advisory
signal on the repository page, rather than as an error, is a bounded first version.

### 13.6 Declared out of scope

Recorded here so the list above is not read as everything imaginable. Each of these was
considered and deliberately excluded, with the reason.

| Item | Why excluded |
|---|---|
| Ed25519 authorship attestations | Log integrity does not need a personal identity system. A key proves possession, not identity. If authorship becomes an assessed claim, the design is a domain-separated detached signature over the commit hash with published key fingerprints. |
| TIES, DARE, rank-compressed merges | One correct measured merge establishes the workflow; further methods are research cost. |
| Multi-user accounts, RBAC, OAuth | The delivery targets one team with token-gated publishing, and says so. |
| Garbage collection | Low demo value, and an append-only provenance system needs a retention policy before it gets a collector. |
| More architectures, generation, quantisation, DoRA | Each multiplies the compatibility, merge, and evaluation surface. |
| Distributed witnesses, high-throughput log service | Full anchored-prefix checks are sufficient at this deployment size. |

---

## 14. Glossary

| Term | Meaning here |
|---|---|
| Adapter | A LoRA fine-tuning result: small `A`/`B` factor matrices plus a config, a few megabytes |
| Base | The frozen pretrained model, referenced by `model_id` and `revision_sha`, never stored |
| Blob, tree, commit, base object | The four content-addressed object types of section 3.2 |
| Content addressing | An object's name is the SHA-256 of its content |
| Canonical JSON | Sorted keys, no incidental whitespace, real UTF-8, no NaN |
| Domain separation | Tagging leaves `0x00` and internal nodes `0x01` so their hash spaces are disjoint |
| Effective update | `scale * B @ A`, the quantity a LoRA layer actually adds to the base |
| Inclusion proof | A leaf, its sibling path, and a root, sufficient to verify membership without the Hub |
| Checkpoint | A `(size, root, blockNumber, previousSize)` record published on chain |
| Log ID | The immutable identity of one transparency log, bound in the contract constructor |
| Prefix check | Confirming every earlier anchored root still recomputes, which is the append-only test |
| Spec hash | The identity of an evaluation, so only comparable scores are compared |
