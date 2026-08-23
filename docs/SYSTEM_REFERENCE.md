# Aethel — System Reference

Complete reference for how Aethel works: architecture, every command, the
storage model, the Hub and its transparency log, and a demo walkthrough you can
run end to end.

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
    aggregator.py   Merkle tree (backs the Hub's transparency log)

  remote/     Talking to a Hub. Hashes verified at both ends.
    objects.py      walks a branch into a push plan
    http.py         the HTTP client: upload, negotiate, move a ref

  commands/   Thin CLI. Parses arguments, calls core, renders output.
    _common.py      the single error-rendering boundary
    init.py  commit.py  branch.py  checkout.py  log.py  fsck.py  push.py
    train.py        the only module that needs torch

hub/          The server. Needs the [hub] extra; nothing else imports it.
  config.py       environment-driven configuration, no hard-coded hosts
  storage.py      the Hub's own content-addressed store + repo index
  log.py          the append-only transparency log and anchor records
  api.py          REST API
  views.py        server-rendered dashboard and ops board
```

**The layering rule:** core raises `AethelError` and never prints;
`_common.py` is the one place where errors become terminal output and exit
codes.

The dependency arrow points one way: `hub/` imports from `aethel.core`, and
nothing in `aethel/` imports from `hub/`. That is what lets the Hub reuse the
same hashing, the same Merkle tree, and the same commit format as the client
without the client ever depending on a web framework.

Three consequences worth knowing:

- The entire VCS is testable with no GPU, no model download, and no network.
  693 tests run in under 10 seconds — including a full push, because the Hub's tests
  run the ASGI application in-process rather than over a socket.
- `pip install -e .` gives a working repository tool *that can push*. PyTorch is
  the separate `[ml]` extra, needed only by `aethel train`; FastAPI is the
  separate `[hub]` extra, needed only to *serve* a Hub.
- The client is deliberately small: publishing is HTTP requests and hash checks,
  so the interesting logic stays in `core/` where it is cheap to test.

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
  "author": "johndoe",
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
Author:     johndoe

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
│ 046700d80a44 │ 2026-08-08 15:16:30 │ johndoe │    0.642 │ Emotion 6-class   │
│ 02345195560c │ 2026-08-08 15:16:27 │ johndoe │    0.903 │ SST-2 more epochs │
│ 9ee8ea047e14 │ 2026-08-08 15:16:26 │ johndoe │    0.871 │ SST-2 baseline    │
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

### `aethel push`

Publishes the current branch to a Hub. Uploads objects, then advances the
branch on the remote, then reports where the commits landed in the Hub's
transparency log.

```console
$ aethel push --repo sentiment
Pushing main → sentiment at http://127.0.0.1:8000
6 objects reachable from 02345195560c (2 commits, 2 trees, 4 blobs, 1 bases)
Hub 0.1.0 build 9da643c

Hub needs 6 of 6 objects.

Pushed 6 object(s) · 1.2 MB · 0 already present
sentiment/main → 02345195560c
Transparency log: 2 leaves (2 new)
Log root: 1f8c4a0b7e21e6d5…

http://127.0.0.1:8000/r/sentiment
```

Options:

| Flag | Default |
|---|---|
| `--remote/-r` | `$AETHEL_HUB_URL`, then `http://127.0.0.1:8000` |
| `--repo` | `hub_repo` from config, then the directory name |
| `--branch/-b` | the current branch |
| `--token` | `$AETHEL_HUB_TOKEN` |
| `--dry-run` | off — show what would upload, then stop |

The published name is written to `config.json` on the first push, so later
pushes need no flags and the name cannot drift if the local folder is renamed.

Refused, with nothing uploaded: a detached HEAD, a branch with no commits, an
unknown branch, or a local object that is missing from the store. The plan is
built and every object verified *before* the first byte leaves the machine, so
a push that cannot finish fails early rather than half-way.

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

`core/aggregator.py` implements the tree; `hub/log.py` is the log built on it.
Every commit the Hub accepts becomes a leaf, in acceptance order. The Merkle
root over those leaves is the Hub's commitment to its own history: publish the
root, serve inclusion proofs, and a client can verify its commit is in there
without trusting the Hub.

```
             Root
            /    \
       H(AB)      H(CD)
       /   \      /   \
     A      B    C     D      <- leaves: commit hashes
```

**Leaves are commit hashes, not commit bodies.** A commit hash already commits
to the entire commit content — it *is* the hash of the canonical JSON — so
hashing the hash is sufficient and keeps every leaf fixed-width.

**Append-only is structural, not a promise.** `hub/log.py` has exactly one
mutating operation, `append_many`. There is no update and no delete anywhere in
the module, so the property holds because the code has no way to violate it.

**The file is the source of truth; the tree is derived.** Leaves live in
`log.jsonl`, one entry per line, and the tree is recomputed from them on every
read. The on-disk format is auditable with `cat`, and a corrupted in-memory
tree can always be rebuilt.

**Re-pushing does not change the root.** An append resolves an
already-logged commit to its existing leaf rather than adding a duplicate, so
syncing twice is a no-op. The API reports `logged_indices` (where the whole
history sits) separately from `appended_indices` (what this call created), so an
idempotent push cannot be mistaken for one that changed the log.

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

```console
$ curl -s localhost:8000/api/v1/log/proof/<commit-hash>
{
  "commit_hash": "0234519556…", "leaf_index": 1, "log_size": 3,
  "root": "1f8c4a0b7e21…",
  "proof": [ {"sibling": "9ee8ea04…", "side": "left"},
             {"sibling": "c4bb4b72…", "side": "right"} ]
}
```

The response is self-contained — leaf, sibling path, root, log size. A verifier
recomputes the root from those alone and never calls back, which is precisely
why a dishonest Hub cannot fake inclusion.

### What the log does and does not prove

It proves that a commit record was accepted at a position in this log and that
the published root commits to it. It does **not** yet prove the operator never
rewrote the log wholesale, because nothing outside the Hub currently pins the
root — that is what anchoring adds, and until it lands the ops board reports
"never anchored" rather than implying a guarantee that is not there.

---

## 7. The Hub

A FastAPI server with its own content-addressed store, a REST API, a
server-rendered dashboard, and the transparency log above. Started with
`python -m hub`, configured entirely by environment variables.

| Variable | Meaning | Default |
|---|---|---|
| `AETHEL_HUB_DATA` | where the Hub keeps objects and its log | `./hub-data` |
| `AETHEL_HUB_HOST` | bind address | `0.0.0.0` |
| `AETHEL_HUB_PORT` | port | `8000` |
| `AETHEL_HUB_TOKEN` | shared secret required on every write | unset (open) |
| `AETHEL_HUB_MAX_BLOB` | largest accepted blob, in bytes | 64 MiB |
| `AETHEL_CHAIN_RPC`, `AETHEL_CHAIN_ID`, `AETHEL_ANCHOR_CONTRACT` | read now, used by the anchoring layer | unset |
| `AETHEL_PINNING_ENDPOINT` | read now, used by the IPFS mirror | unset |

Nothing is hard-coded to a host. The Hub can run on a teammate's laptop, a
cloud VM, or behind a tunnel, and which one is a deployment decision rather
than a code change. The chain and pinning settings exist already so the ops
board can honestly report "not configured" instead of crashing.

The Hub's data directory is deliberately separate from any `.aethel`
repository: the Hub is a peer that holds published objects, not a working copy.

### The push protocol

```
client                                        Hub
  |  GET  /api/v1/version                      |   is this a Hub, and which build?
  |------------------------------------------->|
  |  POST /api/v1/repos/<r>/negotiate          |   here is everything I hold
  |------------------------------------------->|   -> here is what I am missing
  |  PUT  /api/v1/blobs/<sha256>               |   raw bytes, addressed by hash
  |  PUT  /api/v1/trees|bases|commits/<sha256> |   canonical JSON, verbatim
  |------------------------------------------->|
  |  POST /api/v1/repos/<r>/refs               |   move the branch (last)
  |------------------------------------------->|   -> log indices + new root
```

Four rules make this trustworthy:

1. **The client names the hash, the server verifies it.** Every upload is
   addressed by the hash the client computed; the Hub recomputes it from the
   bytes it actually received and rejects a mismatch with 422. The client then
   re-checks the hash the Hub echoes back, so a lying or buggy remote is caught
   at both ends.
2. **Bytes are uploaded verbatim, never re-serialized.** A JSON object is sent
   as the exact bytes stored on disk. Re-serializing anywhere in the path —
   client or server — could reorder keys and change the hash, and a hash that
   drifts in transit would break every downstream proof.
3. **The server decides the delta.** The client offers what it has and the Hub
   answers with what it lacks, rather than the client guessing from a previous
   push. A Hub that lost an object therefore self-heals on the next push instead
   of staying quietly incomplete.
4. **The ref moves last, and only after a re-walk.** `POST .../refs` re-walks
   the commit's whole history in the Hub's own store and returns 409 if anything
   is missing. A published branch can never point at an object nobody can
   fetch. An interrupted push leaves unreferenced objects — retryable — which is
   the same failure mode as an interrupted local commit.

Blobs arrive as raw request bodies rather than multipart form data: content
addressing makes the filename irrelevant, so multipart would add a parser and a
field that must be ignored anyway.

### API reference

| Method | Path | Purpose |
|---|---|---|
| `PUT` | `/api/v1/blobs/{sha256}` | upload raw bytes |
| `PUT` | `/api/v1/{commits,trees,bases}/{sha256}` | upload a JSON object |
| `POST` | `/api/v1/repos/{repo}/negotiate` | which objects is the Hub missing? |
| `POST` | `/api/v1/repos/{repo}/refs` | advance a branch, append to the log |
| `GET` | `/api/v1/repos` · `/api/v1/repos/{repo}` | the repository index |
| `GET` | `/api/v1/repos/{repo}/commits` | history, optionally `?branch=` |
| `GET` | `/api/v1/commits,trees,bases/{sha256}` | read one object |
| `GET` | `/api/v1/blobs/{sha256}` | download a patch |
| `GET` | `/api/v1/log` | size, root, entries, anchor state |
| `GET` | `/api/v1/log/proof/{commit}` | a self-contained inclusion proof |
| `POST` | `/api/v1/log/verify` | verify a proof server-side (convenience only) |
| `GET` | `/api/v1/health` · `/api/v1/version` | ops board data, deployed build |

`POST /api/v1/log/verify` is a convenience and a test hook, never the
authoritative check: asking the Hub whether the Hub is honest proves nothing.
The real verification runs client-side against the anchored root.

Write endpoints are gated by `AETHEL_HUB_TOKEN` when it is set. An unset token
means an open Hub, which is the right default for a laptop demo and is reported
as "open — no push token set" on the ops board rather than passed off as
security.

### Dashboard

Server-rendered HTML, because the dashboard's job is to make provenance
legible: a page whose values are already in the HTML can be read with View
Source, screenshotted, and printed. Chart data is prepared in Python and drawn
as inline SVG — no chart library, so nothing loads from a CDN the demo network
may not reach. The same applies to the two typefaces, which are subset and
served from this origin. `docs/design/dashboard.md` records why these pages look
and move the way they do.

| Page | Shows |
|---|---|
| `/` | every repository, commit counts, the log's size and current root |
| `/r/<repo>` | commit history per branch, accuracy per commit, patch download |
| `/c/<hash>` | one commit: parent, tree, base reference, metrics, inclusion proof |
| `/api` | the REST surface: every route, its parameters, its status codes |
| `/ops` | per-subsystem health |

`/api` is hand-written from the same route table the tests read, rather than
generated by a bundled Swagger UI. The generated page would have needed an
external origin the Content-Security-Policy does not name, and a test asserts
that every path documented there is a path the application actually serves — so
the reference cannot drift from the routes.

A commit page's inclusion proof is served *by the Hub*, which means reading it is
trusting the Hub. **Recompute in this browser** removes that step: the browser
fetches the proof as JSON, folds it with a SHA-256 implementation in
`hub/static/verify.js`, and marks each rung against what it computed rather than
against what the page says. The hash is written out by hand because
`crypto.subtle` is unavailable outside a secure context, and the demo runs on a
LAN address — where a verifier built on it would be `undefined` in exactly the
place it is meant to work. It self-checks against published vectors before
rendering any verdict, because a subtly wrong hash would report tampering that
never happened.

`/ops` exists because four systems (object store, log, chain, pinning service)
fail independently, and debugging that live in front of an audience is not a
plan. Every check is wrapped so one dead dependency degrades its own row rather
than failing the page — an ops board that cannot render during an incident is
useless exactly when it is needed.

The important row is **log vs anchored root**: it recomputes the root from
`log.jsonl` and compares it against the last anchored record. That is a live
self-audit of the exact property the project claims. It reads "never anchored"
today, turns green when the roots match, and goes critical if they ever diverge.

Metrics that were never computed render as an em dash, never as zero: "not
measured" and "scored 0%" are very different claims about a model.

---

## 8. Demo walkthrough

Runs on CPU. No GPU required.

### Setup

```bash
pip install -e ".[dev]"          # VCS core + test tools
pip install -e ".[ml]"           # add torch/transformers for training
pip install -e ".[hub]"          # add fastapi/uvicorn to serve the Hub
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

### Phase 9 — publish to a Hub

In a second terminal:

```bash
AETHEL_HUB_DATA=/tmp/hub-data python -m hub      # serves on 0.0.0.0:8000
```

Back in the repository:

```bash
aethel push --dry-run             # exactly what would upload, and nothing more
aethel push --repo sentiment      # publish the current branch
aethel checkout emotion && aethel push
```

Then open `http://localhost:8000` — repositories, then `/r/sentiment` for the
commit history with accuracy per commit, then any commit for its inclusion
proof, then `/ops` for the health board.

Things worth showing here:

```bash
aethel push                       # again: "nothing to upload", root unchanged
```

A second push is a no-op. The Hub already holds every object, the log resolves
each commit to its existing leaf, and the root does not move — which is what
makes "push" a sync rather than an event.

```bash
rm /tmp/hub-data/objects/blobs/<ab>/<rest>   # the Hub loses an object
aethel push                                  # it comes back
```

The delta is decided by the Hub, not remembered by the client, so a Hub that
lost data repairs itself on the next push.

### Phase 10 — inclusion proofs

```bash
curl -s localhost:8000/api/v1/log | python3 -m json.tool
curl -s localhost:8000/api/v1/log/proof/<commit-hash> | python3 -m json.tool
```

The proof is self-contained: leaf, sibling path, root, log size. Recompute the
root from those alone and compare — nothing calls back to the Hub, which is why
a dishonest Hub cannot fake it.

```bash
# tamper: change one leaf in the Hub's log
sed -i 's/<real-commit-hash>/<fake-hash>/' /tmp/hub-data/log.jsonl
curl -s localhost:8000/api/v1/log | python3 -c "import json,sys; print(json.load(sys.stdin)['root'])"
```

The root changes. Today that is visible by comparison; once the root is anchored
on-chain, the `/ops` "log vs anchored root" row goes critical on its own — the
tamper becomes detectable without anyone knowing the old root.

### Talking points

- **Storage:** three model versions in a few MB, not a few GB.
- **Content addressing:** a filename *is* a hash, so integrity checking is
  exact rather than heuristic.
- **Deduplication:** identical weights are stored once, and commit metadata
  still stays separate.
- **Reproducibility:** the pinned revision SHA means "the same base model"
  is a verifiable claim.
- **Portability:** no database means a repository is just a directory.
- **Publishing without trust:** hashes are verified at both ends of every
  upload, and a published branch cannot point at something nobody can fetch.
- **Transparency:** the Hub commits to its own history with a Merkle root, and
  serves proofs that are checkable without it.

---

## 9. Development

```bash
python3 -m pytest                   # 693 tests, ~8s, no GPU or network
pytest tests/ --cov=aethel.core     # 96%
ruff check .
```

| Test file | Tests | Covers |
|---|---|---|
| `test_hub_api.py` | 82 | Upload, negotiate, refs, reads, log endpoints, auth, health |
| `test_core_refs.py` | 52 | HEAD, branches, validation, traversal |
| `test_push.py` | 41 | Push planning, round trips, refusals, client-side verification |
| `test_core_merkle.py` | 29 | Domain separation, proofs, tamper detection |
| `test_core_objects.py` | 29 | Store, dedup, integrity, trees |
| `test_hub_log.py` | 28 | Append-only behaviour, idempotence, proofs, anchors |
| `test_core_hashing.py` | 24 | Canonical JSON, SHA-256 |
| `test_core_commits.py` | 23 | Creation, lineage, determinism |
| `test_core_atomic.py` | 18 | Crash safety, locking, concurrency |
| `test_core_resolve.py` | 15 | Branch/hash/abbreviation resolution |
| `test_s1_corruption.py` | 10 | Metadata-collision regression, via the CLI |

**No test touches the network.** The Hub's tests run the ASGI application
in-process, so a full push — negotiate, upload, move the ref, append to the log
— is exercised without a socket, a port, or a background process. That is why
the whole suite is fast enough to run on every save.

The Hub's tests skip themselves when FastAPI is absent, so they do not break a
client-only install. CI therefore runs two jobs:

- **`core`** installs only `[dev]`, on Python 3.10–3.13. It fails if the core
  ever grows a dependency on torch or FastAPI, and the Hub's tests skip by
  design.
- **`hub`** installs `[dev,hub]`, asserts the Hub actually imports, lints the
  whole tree, and runs the same suite. Without this job those 151 Hub and push
  tests would skip on every run and CI could stay green through a Hub that does
  not even import — a passing suite that proved nothing.

One-command setup: `scripts/dev.sh` (Linux/macOS), `scripts/dev.ps1`
(Windows). Both are idempotent and do the same thing.

---

## 10. Glossary

| Term | Meaning |
|---|---|
| **Adapter / patch** | A LoRA module trained on top of the frozen base model |
| **Base reference** | `(model_id, revision_sha)` — the base model, by reference |
| **Blob** | Raw bytes of one file, stored under its content hash |
| **Tree** | Manifest mapping filenames to blob hashes |
| **Commit** | Metadata object: parent, tree, base, message, author, metrics |
| **Content addressing** | Storage where a filename is the hash of the content |
| **Detached HEAD** | HEAD pointing at a commit rather than a branch |
| **Hub** | The server that hosts published patches and keeps the transparency log |
| **Inclusion proof** | The sibling hashes proving a leaf is under a given root |
| **LoRA** | Low-Rank Adaptation — trains small matrices instead of all weights |
| **Merkle tree** | Hash tree letting one root commit to many values |
| **Negotiation** | The Hub answering which offered objects it is missing |
| **Revision SHA** | An immutable Hugging Face model version identifier |
| **Transparency log** | The Hub's append-only list of accepted commits, one leaf each |
| **Unborn branch** | A branch that exists but has no commits yet |
| **Workspace** | `.aethel/workspace/` — staged by `train`, read by `commit` |
