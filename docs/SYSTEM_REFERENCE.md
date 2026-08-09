# Aethel — System Reference

Complete reference for how Aethel works: architecture, every command, the
storage model, and a demo walkthrough you can run end to end.

For strategy and roadmap see [`PROJECT_PLAN.md`](PROJECT_PLAN.md).
For the engineering record see [`TEAM_WALKTHROUGH.md`](TEAM_WALKTHROUGH.md).

---

## 1. What Aethel is

A version control and provenance system for **LoRA adapters**.

Fine-tuning a model normally means producing a whole new copy of it. Aethel
keeps one frozen base model and versions only the small LoRA patches trained
on top. A version is ~0.6 MB instead of ~250 MB.

The base model's weights are **never stored**. A repository records
`(model_id, revision_sha)` — an immutable Hugging Face revision — and anyone
reproducing a result downloads that exact revision themselves and applies the
patch.

Why the SHA and not a tag: tags move, SHAs don't. Pinning the SHA is what
makes "reproduce this result" mean something.

---

## 2. Architecture

```
aethel/
  core/       Pure logic. No torch, no network. Never prints, never exits.
    errors.py       exception hierarchy
    hashing.py      SHA-256 + canonical JSON
    atomic.py       crash-safe writes + locking
    objects.py      content-addressed object store
    refs.py         HEAD and branches
    repo.py         repository discovery and layout
    commits.py      commit creation and history traversal
    aggregator.py   Merkle tree (transparency log)

  commands/   Thin CLI. Parses arguments, calls core, renders output.
    _common.py      the single error-rendering boundary
    init.py  commit.py  branch.py  checkout.py  log.py  fsck.py
    train.py        the only module that needs torch
```

**The layering rule:** core raises `AethelError` and never prints;
`_common.py` is the one place where errors become terminal output and exit
codes.

Two consequences worth knowing:

- The entire VCS is testable with no GPU, no model download, and no network.
  200 tests run in ~2 seconds.
- `pip install -e .` gives a working repository tool. PyTorch is the separate
  `[ml]` extra, needed only by `aethel train`.

---

## 3. Storage model

```
.aethel/
  HEAD                          "ref: refs/heads/main"  or a raw commit hash
  config.json                   pinned base model + author
  refs/heads/<branch>           one commit hash per branch
  objects/blobs/<ab>/<rest>     file contents
  objects/trees/<ab>/<rest>     manifest: filename -> blob hash
  objects/commits/<ab>/<rest>   commit metadata
  objects/bases/<ab>/<rest>     base-model references
  workspace/                    staging area written by `aethel train`
```

### Four object types

Every object is named by the **SHA-256 of its own content**.

| Type | Holds | Keyed by |
|---|---|---|
| **blob** | raw bytes of one file | hash of the bytes |
| **tree** | `filename -> blob hash` manifest | hash of the manifest |
| **commit** | parent, tree, base, message, author, timestamp, metrics | hash of the commit |
| **base** | `model_id` + `revision_sha` | hash of the reference |

Names are sharded on the first two hex characters (`<ab>/<rest>`), as Git
does, so no single directory accumulates tens of thousands of entries.

### Why commits are keyed by their own hash

This is the single most important design decision in the storage layer.

Blobs deduplicate by content — two commits with byte-identical weights share
one blob, which is exactly what you want. But if commit *metadata* were also
keyed by the adapter's hash, those two commits would share one metadata slot
and the second would overwrite the first. Checking out the first commit would
return the second's message, author, and **parent**.

A wrong parent means a wrong commit DAG. Since that DAG is what gets anchored
on-chain, the result would be a tamper-proof record of false lineage.

Keying commits by their own hash makes the collision impossible to express.
Deduplication of weights is preserved; metadata never collides.

### A commit object

```json
{
  "schema": 2,
  "parent_hash": "<hash> | null",
  "tree": "<tree hash>",
  "adapter_blob": "<blob hash>",
  "base": "<base object hash>",
  "message": "SST-2 sentiment baseline",
  "author": "sathwik",
  "timestamp": "2026-08-08T15:16:27+00:00",
  "training_info": { "metrics": { "eval_accuracy": 0.871 }, "lora_rank": 8 }
}
```

The `tree` records the **whole workspace**, not just the weights — so a
checkout restores `adapter_config.json` and `training_info.json` from
content-addressed storage too, rather than from whatever happens to be on
disk.

### No database

History is walked from refs through the object store. There is no index, so
there is nothing to fall out of sync and nothing to rebuild.

This makes a repository genuinely portable: copy `.aethel/` anywhere and it
works.

```console
$ cp -r .aethel /tmp/elsewhere/ && cd /tmp/elsewhere
$ find . -name '*.db' | wc -l
0
$ aethel log --all      # full history
$ aethel fsck           # Repository integrity OK.
```

### Crash safety

Every durable write follows the same four steps, and each one is
load-bearing:

1. **Write to a temp file in the same directory.** `os.replace` is atomic only
   *within one filesystem*; `/tmp` is often a different mount.
2. **`fsync` the temp file.** Otherwise the rename can reach disk before the
   data, leaving a correctly-named empty file.
3. **`os.replace`.** Atomic on POSIX *and* Windows, unlike `os.rename`, which
   fails on Windows when the destination exists.
4. **`fsync` the parent directory**, so the rename survives power loss.

**Ordering within a commit:** objects are written first, the ref advances
last. Objects are immutable and self-verifying, so an interrupted commit
leaves unreferenced garbage that `fsck` reports. Advancing the ref first would
leave a branch pointing at objects that don't exist — unrecoverable.

**Ref updates take a lock** (`O_CREAT | O_EXCL`). Without it, two concurrent
commits both read the same branch tip and the second silently discards the
first.

---

## 4. Command reference

### `aethel init --model <owner/model>`

Creates `.aethel/`, resolves the model's immutable revision SHA from Hugging
Face, and writes the config plus the first base-reference object.

```console
$ aethel init --model distilbert-base-uncased
Initialized Aethel repository in /path/.aethel
Base model: distilbert-base-uncased
Revision:   a1b2c3d4...
Base ref:   7f3a91c204e8
Author:     sathwik

Base weights are not stored — only the pinned reference.
```

Options: `--model/-m`, `--author` (defaults to `$AETHEL_AUTHOR`, then `$USER`).
Re-running preserves the existing author and all objects.

### `aethel train --config <yaml>`

Runs LoRA fine-tuning and writes adapter artifacts into `.aethel/workspace/`.
Requires the `[ml]` extra.

Training and committing are **decoupled**: `train` stages, `commit` snapshots.

```yaml
# train_yaml/sst2_config.yaml
dataset: ./datasets/sst2
text_column: text
label_column: label
num_labels: 2
max_samples: 500
max_length: 128
lora_rank: 8
lora_alpha: 16
batch_size: 2
gradient_accumulation_steps: 8
epochs: 1
```

**If the dataset path does not exist, training fails.** It does not fall back
to synthetic data. A typo in a path must not be able to produce a publishable
model that learned nothing. To train on synthetic text deliberately, pass
`--allow-stub`.

### `aethel commit -m "<message>"`

Snapshots the workspace into a new commit and advances the current branch.
Does not train, and does not modify the workspace.

Blocked on a detached HEAD — see §5.

### `aethel branch [name] [start-point]`

No arguments lists branches. With a name, creates one at HEAD (or at
`start-point`). `--delete/-d <name>` removes a branch ref; its commits stay in
the object store.

```console
$ aethel branch
* main      02345195560c SST-2 more epochs
  emotion   046700d80a44 Emotion 6-class
```

### `aethel checkout <target>`

Restores the workspace to a branch, a full commit hash, or a unique
abbreviated hash (4+ characters). Branch names win over hashes, as in Git.

Refuses to discard uncommitted changes unless given `--force`, comparing the
workspace against the current commit's tree **by content hash**, not mtime.

Checking out a raw hash detaches HEAD.

### `aethel log [target]`

Commit history, walked from the object store.

```console
$ aethel log --all
                                 Commit History
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ Commit       ┃ When                ┃ Author  ┃ Accuracy ┃ Message           ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ 046700d80a44 │ 2026-08-08 15:16:30 │ sathwik │    0.642 │ Emotion 6-class   │
│ 02345195560c │ 2026-08-08 15:16:27 │ sathwik │    0.903 │ SST-2 more epochs │
│ 9ee8ea047e14 │ 2026-08-08 15:16:26 │ sathwik │    0.871 │ SST-2 baseline    │
└──────────────┴─────────────────────┴─────────┴──────────┴───────────────────┘
```

Options: `--limit/-n` (default 20), `--all` for every branch.

### `aethel status`

Current branch or detached commit, plus workspace changes against the current
commit's tree.

### `aethel fsck`

Re-hashes every object and compares against its name. Reports three distinct
classes:

- **corrupt** — content no longer matches its name (real damage)
- **missing** — something references an object that isn't in the store
- **unreachable** — objects no ref can reach (harmless; interrupted commits)

Exits 1 on corrupt or missing.

---

## 5. Branching and HEAD

```
HEAD ──> refs/heads/main ──> <commit hash>

NULL <── c1 <── c2 <── c3   (main)
                 ↖
                  c4 <── c5 (emotion)
```

`HEAD` holds either `ref: refs/heads/<branch>` (attached) or a raw commit hash
(detached). Branch refs are files containing a single commit hash. Creating a
branch just writes one small file — no weights are copied.

### The detached-HEAD guard

**Commits are blocked while HEAD is detached.** This is stricter than Git,
which only warns, and it's deliberate: a commit made in that state is
referenced by nothing and is lost the moment you check out anything else.

```console
$ aethel commit -m "experiment"
Detached HEAD — commit blocked.

You are not on any branch.
HEAD points directly at commit 9ee8ea047e14

A commit made here would be referenced by nothing and lost
as soon as you check out another branch.

To keep this work, put a branch here first:
  aethel branch <new-branch>
  aethel checkout <new-branch>
  aethel commit -m "experiment"
```

---

## 6. Integrity and the Merkle log

### Verification on every read

Because an object's name *is* its content hash, verification is exact. It
happens on every read, not only during `fsck` — the cost is one hash of a
small file, and the benefit is that bit-rot or tampering surfaces the moment
the data is used.

```console
$ aethel fsck
Checked 14 objects (3 commits, 3 trees, 7 blobs, 1 bases)
Repository integrity OK.

$ printf 'x' >> .aethel/objects/blobs/1b/b2d78c...
$ aethel fsck
1 CORRUPT object(s):
  blob 1bb2d78c6d043feb1f72c4bb4b7294722bc7d3e7c87a0c57cb980c3797d513d3
Repository integrity check FAILED.
```

### The Merkle transparency log

`core/aggregator.py` implements the tree that will back the Hub's append-only
log: hash every commit into a tree, publish the root, serve inclusion proofs.
A client verifies its commit against the published root without trusting the
Hub.

```
             Root
            /    \
       H(AB)      H(CD)
       /   \      /   \
     A      B    C     D      <- leaves: commit hashes
```

**Domain separation is mandatory here**, following RFC 6962 (Certificate
Transparency):

```
leaf     = SHA256(0x00 || data)
internal = SHA256(0x01 || left || right)
```

Without distinct prefixes, leaves and internal nodes share a hash space, and a
tree built over another tree's internal nodes produces an **identical root** —
letting an operator forge an inclusion proof for a value that was never
committed. That defeats the entire purpose of anchoring. The prefixes make the
two hash spaces disjoint.

Odd nodes are **promoted** (carried up unchanged) rather than duplicated.
Duplicating the last node is the Bitcoin behaviour behind CVE-2012-2459, where
two different leaf sets can produce one root.

An inclusion proof is logarithmic: proving one commit in a 1024-commit log
takes ~10 hashes, which is why batching many commits under a single anchored
root stays cheap.

Implemented and tested (29 tests). Not yet wired into the commit pipeline —
that lands with the Hub.

---

## 7. Demo walkthrough

Runs on CPU. No GPU required.

### Setup

```bash
pip install -e ".[dev]"          # VCS core + test tools
pip install -e ".[ml]"           # add torch/transformers for training
python setup_demo_data.py        # downloads 5 small CSV datasets
```

`setup_demo_data.py` writes `datasets/<name>/train.csv` with `text,label`
columns for SST-2, Rotten Tomatoes, Emotion, AG News, and Tweet-Eval Hate.

### Phase 1 — initialize

```bash
mkdir demo && cd demo
aethel init --model distilbert-base-uncased
aethel log          # "No commits yet."
```

Look inside: `.aethel/objects/` has four empty subdirectories, `refs/heads/main`
is an empty file (an unborn branch), and `config.json` holds the pinned SHA.

### Phase 2 — train and commit

```bash
aethel train --config ../train_yaml/sst2_config.yaml
ls .aethel/workspace/       # adapter_model.safetensors, adapter_config.json, training_info.json

aethel commit -m "SST-2 sentiment baseline"
aethel log
```

The adapter is a few hundred KB. The 250 MB base model was downloaded to the
Hugging Face cache, not into the repository.

### Phase 3 — a second version

```bash
aethel train --config ../train_yaml/sst2_config.yaml   # more epochs
aethel commit -m "SST-2 more epochs"
aethel log
```

Two commits, two accuracy numbers.

### Phase 4 — branch to a different task

```bash
aethel branch emotion
aethel checkout emotion
aethel train --config ../train_yaml/emotion_config.yaml
aethel commit -m "Emotion 6-class"
aethel log --all
```

### Phase 5 — time travel

```bash
aethel checkout main
cat .aethel/workspace/training_info.json    # the sentiment adapter is back
aethel checkout emotion
cat .aethel/workspace/training_info.json    # now the emotion adapter
```

Each checkout swaps which capability is staged, in milliseconds, by copying
~0.6 MB. Three different models, one base.

### Phase 6 — the detached-HEAD guard

```bash
aethel log                        # copy an older commit hash
aethel checkout <old-hash>        # HEAD detaches
aethel commit -m "should fail"    # blocked, with recovery steps
```

### Phase 7 — integrity

```bash
aethel fsck                                             # OK
printf 'x' >> .aethel/objects/blobs/<ab>/<rest>         # corrupt one byte
aethel fsck                                             # names that exact object, exits 1
```

### Phase 8 — portability

```bash
cp -r .aethel /tmp/copied-repo/ && cd /tmp/copied-repo
find . -name '*.db' | wc -l       # 0 — there is no database
aethel log --all                  # complete history
aethel fsck                       # OK
```

### Talking points

- **Storage:** three model versions in a few MB, not a few GB.
- **Content addressing:** a filename *is* a hash, so integrity checking is
  exact rather than heuristic.
- **Deduplication:** identical weights are stored once, and commit metadata
  still stays separate.
- **Reproducibility:** the pinned revision SHA means "the same base model"
  is a verifiable claim.
- **Portability:** no database means a repository is just a directory.

---

## 8. Development

```bash
pytest tests/ -q                    # 200 tests, ~2s, no GPU or network
pytest tests/ --cov=aethel.core     # 96%
ruff check aethel/ tests/
```

| Test file | Tests | Covers |
|---|---|---|
| `test_core_refs.py` | 52 | HEAD, branches, validation, traversal |
| `test_core_merkle.py` | 29 | Domain separation, proofs, tamper detection |
| `test_core_objects.py` | 29 | Store, dedup, integrity, trees |
| `test_core_hashing.py` | 24 | Canonical JSON, SHA-256 |
| `test_core_commits.py` | 23 | Creation, lineage, determinism |
| `test_core_atomic.py` | 18 | Crash safety, locking, concurrency |
| `test_core_resolve.py` | 15 | Branch/hash/abbreviation resolution |
| `test_s1_corruption.py` | 10 | Metadata-collision regression, via the CLI |

One-command setup: `scripts/dev.sh` (Linux/macOS), `scripts/dev.ps1`
(Windows). Both are idempotent and do the same thing.

---

## 9. Glossary

| Term | Meaning |
|---|---|
| **Adapter / patch** | A LoRA module trained on top of the frozen base model |
| **Base reference** | `(model_id, revision_sha)` — the base model, by reference |
| **Blob** | Raw bytes of one file, stored under its content hash |
| **Tree** | Manifest mapping filenames to blob hashes |
| **Commit** | Metadata object: parent, tree, base, message, author, metrics |
| **Content addressing** | Storage where a filename is the hash of the content |
| **Detached HEAD** | HEAD pointing at a commit rather than a branch |
| **LoRA** | Low-Rank Adaptation — trains small matrices instead of all weights |
| **Merkle tree** | Hash tree letting one root commit to many values |
| **Inclusion proof** | The sibling hashes proving a leaf is under a given root |
| **Revision SHA** | An immutable Hugging Face model version identifier |
| **Unborn branch** | A branch that exists but has no commits yet |
| **Workspace** | `.aethel/workspace/` — staged by `train`, read by `commit` |
