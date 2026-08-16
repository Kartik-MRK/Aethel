# Aethel

**Version control and provenance for LoRA adapters.**

One frozen base model, fine-tuned into small LoRA patches. Each patch is a
version, managed with Git-like commands. The base model's weights are never
stored — only a pinned, immutable reference — so a version costs ~0.6 MB
instead of ~250 MB.

```bash
aethel init --model distilbert-base-uncased   # pin a base model revision
aethel train --config train_yaml/sst2_config.yaml
aethel commit -m "SST-2 sentiment baseline"
aethel branch emotion                          # branch off to a new task
aethel checkout emotion
aethel log                                     # history with accuracy per commit
aethel fsck                                    # verify every object's integrity
aethel push                                    # publish the branch to a Hub
```

## Install

The version-control core is pure standard library — no PyTorch needed to
inspect, branch, verify, or publish a repository:

```bash
pip install -e .            # CLI + object store + push
pip install -e ".[ml]"      # adds torch/transformers/peft for `aethel train`
pip install -e ".[hub]"     # adds fastapi/uvicorn/jinja2 to *run* a Hub
pip install -e ".[dev]"     # pytest + ruff
```

`[hub]` is only needed on the machine serving the Hub. Pushing to one needs
nothing beyond the base install.

`bitsandbytes` is a separate `[gpu]` extra: it is CUDA-only and breaks CPU and
macOS installs.

## How it works

### The base model is referenced, never stored

`aethel init` resolves the model's immutable revision SHA from Hugging Face and
records it. Reproducing a result means downloading that exact revision yourself
and applying the patch. Pinning the SHA rather than a tag is what makes this
exact — tags move, SHAs do not.

```json
{ "model_id": "distilbert-base-uncased", "revision_sha": "<40-hex>", "source": "huggingface" }
```

### Object store

Every object is named by the SHA-256 of its own content, sharded on the first
two characters:

```
.aethel/
  HEAD                          active branch, or a commit hash when detached
  config.json                   pinned base model + author
  refs/heads/<branch>           one commit hash per branch
  objects/blobs/<ab>/<rest>     file contents (adapter weights, configs)
  objects/trees/<ab>/<rest>     manifest: filename -> blob hash
  objects/commits/<ab>/<rest>   commit metadata
  objects/bases/<ab>/<rest>     base-model references
  workspace/                    staging area written by `aethel train`
```

Three properties follow from this, each enforced by tests:

- **Commits are keyed by their own hash.** Two commits with byte-identical
  weights share one blob (deduplication) while keeping separate metadata.
- **No database.** History is walked from refs through the object store, so
  copying `.aethel/` to another machine copies a complete, working repository.
  There is no index to fall out of sync.
- **All writes are atomic.** Write to a temp file in the same directory,
  `fsync`, then `os.replace`. A reader sees the complete old file or the
  complete new one, never a partial blend.

### Integrity

Because an object's name *is* its content hash, verification is exact — and it
happens on every read, not only during `fsck`:

```console
$ aethel fsck
Checked 14 objects (3 commits, 3 trees, 7 blobs, 1 bases)
Repository integrity OK.

$ printf 'x' >> .aethel/objects/blobs/1b/b2d78c...   # corrupt one byte
$ aethel fsck
1 CORRUPT object(s):
  blob 1bb2d78c6d043feb1f72c4bb4b7294722bc7d3e7c87a0c57cb980c3797d513d3
Repository integrity check FAILED.
```

### Publishing: the Hub and its transparency log

`aethel push` publishes a branch to a Hub — a small FastAPI server with its own
content-addressed store, a dashboard, and an **append-only Merkle transparency
log** of every commit it has accepted.

```bash
python -m hub                      # serve on 0.0.0.0:8000
aethel push --repo sentiment       # publish the current branch
```

Four properties make a push safe to trust:

- **The client names the hash; the server verifies it.** An upload is addressed
  by the hash the client computed, the Hub recomputes it from the bytes it
  actually received, and the client re-checks the hash the Hub echoes back. No
  step trusts the network.
- **The delta is decided by the server.** The client offers what it holds and
  the Hub answers with what it lacks, so a Hub that lost an object re-acquires
  it on the next push instead of staying quietly incomplete.
- **Dependencies upload first, the branch ref moves last.** Blobs, trees, bases,
  then commits oldest-first. An interrupted push leaves a Hub missing objects —
  retryable — never a published branch pointing at objects nobody can fetch.
- **Accepted commits become log leaves, in acceptance order.** The Merkle root
  over those leaves is the Hub's commitment to its own history. Anyone can ask
  for an inclusion proof and recompute the root themselves; the Hub is not
  consulted in that check.

```console
$ curl -s localhost:8000/api/v1/log | jq '{size, root}'
{ "size": 3, "root": "1f8c…" }

$ curl -s localhost:8000/api/v1/log/proof/<commit-hash>
{ "commit_hash": "…", "leaf_index": 1, "log_size": 3, "root": "1f8c…",
  "proof": [ { "sibling": "…", "side": "left" }, … ] }
```

The dashboard serves `/` (repositories), `/r/<repo>` (commit DAG, accuracy per
commit, patch download), `/c/<hash>` (one commit with its inclusion proof), and
`/ops` (per-subsystem health, including a live check that the recomputed root
still matches the last anchored one).

Everything is configuration, never a hard-coded host: `AETHEL_HUB_URL`,
`AETHEL_HUB_DATA`, `AETHEL_HUB_HOST`, `AETHEL_HUB_PORT`, and `AETHEL_HUB_TOKEN`
(set it to require a token on every write; unset means an open Hub, which the
ops board reports rather than hides).

### Safety behaviours worth knowing

- **Commits are blocked on a detached HEAD.** Stricter than Git, which only
  warns. A commit made there is referenced by nothing and is lost at the next
  checkout, so Aethel refuses and prints the recovery steps.
- **`checkout` refuses to discard uncommitted work** unless given `--force`. It
  compares the workspace against the current commit's tree by hash, not mtime.
- **`train` refuses to invent data.** If the dataset path does not exist, it
  fails. Training on synthetic text requires an explicit `--allow-stub`, so a
  typo in a path cannot silently produce a publishable model that learned
  nothing.

## Development

```bash
pytest tests/ -q                    # 351 tests, no GPU or network required
pytest tests/ --cov=aethel.core     # 96% core coverage
ruff check .
```

No test touches the network: the Hub's tests run the ASGI application
in-process, so a push is exercised end to end without a socket. The VCS core
needs no `[ml]` extra and the whole suite finishes in seconds.

CI runs two jobs. `core` installs only `[dev]` on Python 3.10–3.13, which fails
if the core ever grows a dependency on torch or fastapi. `hub` installs
`[dev,hub]`, asserts the Hub imports, then runs the same suite — without that
job the Hub's tests would skip on every run and a broken Hub could stay green.

## Status

Working today: `init`, `train`, `commit`, `branch`, `checkout`, `log`,
`status`, `fsck`, `push` · a Hub with a REST API, a server-rendered dashboard,
an ops health board, and an append-only Merkle transparency log serving
inclusion proofs.

Planned, in order: anchoring the log's root to a public testnet so a model's
recorded history cannot be rewritten even by whoever runs the Hub, with a
browser verification page that recomputes the root client-side · a Pinata
mirror of patch blobs (address only — integrity always comes from
`blob_sha256`) · adapter `diff` and `merge` (task arithmetic, TIES, DARE) ·
Ed25519 commit signing · dataset fingerprinting.

The Merkle tree in `aethel/core/aggregator.py` is what the Hub's log is built
on: it is the same code the chain layer will anchor, and it is tested for
second-preimage resistance via RFC 6962 domain separation.

## Documentation

- [`docs/SYSTEM_REFERENCE.md`](docs/SYSTEM_REFERENCE.md) — architecture, every
  command, the storage model, and a runnable demo walkthrough
- [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) — roadmap, threat model, and
  design rationale
- [`docs/TEAM_WALKTHROUGH.md`](docs/TEAM_WALKTHROUGH.md) — engineering record
  of the rebuild
