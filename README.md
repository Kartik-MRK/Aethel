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
```

## Install

The version-control core is pure standard library — no PyTorch needed to
inspect, branch, or verify a repository:

```bash
pip install -e .            # CLI + object store
pip install -e ".[ml]"      # adds torch/transformers/peft for `aethel train`
pip install -e ".[dev]"     # pytest + ruff
```

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
pytest tests/ -q                    # 200 tests, no GPU or network required
pytest tests/ --cov=aethel.core     # 96% core coverage
ruff check aethel/ tests/
```

The test suite runs without the `[ml]` extra: the core is pure stdlib, so CI
finishes in seconds.

## Status

Working today: `init`, `train`, `commit`, `branch`, `checkout`, `log`,
`status`, `fsck`. Local, single-user repositories.

Planned, in order: adapter `diff` and `merge` (task arithmetic, TIES, DARE) ·
Ed25519 commit signing · dataset fingerprinting · a Hub with an append-only
Merkle transparency log · anchoring that log's root to a public testnet so a
model's recorded history cannot be rewritten, even by whoever runs the Hub.

The Merkle tree in `aethel/core/aggregator.py` is the foundation for that last
step. It is implemented and tested — including second-preimage resistance via
RFC 6962 domain separation — but is not yet wired into the commit pipeline.

## Documentation

- [`docs/SYSTEM_REFERENCE.md`](docs/SYSTEM_REFERENCE.md) — architecture, every
  command, the storage model, and a runnable demo walkthrough
- [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) — roadmap, threat model, and
  design rationale
- [`docs/TEAM_WALKTHROUGH.md`](docs/TEAM_WALKTHROUGH.md) — engineering record
  of the rebuild
