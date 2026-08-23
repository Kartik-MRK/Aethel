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

Or in one command, on either platform — both scripts do the same steps, are
idempotent, and are safe to re-run:

```bash
scripts/dev.sh              # Linux / macOS
scripts\dev.ps1             # Windows
```

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
{ "size": 8, "root": "e8165226…" }

$ curl -s localhost:8000/api/v1/log/proof/<commit-hash>
{ "commit_hash": "…", "leaf_index": 4, "log_size": 8, "root": "e8165226…",
  "proof": [ { "sibling": "469f8328…", "side": "right" }, … ] }
```

One tree covers the whole Hub, not one per repository. A per-repo tree would
let the operator drop a repository entirely and still publish a root that
verified, because nothing outside it would notice the absence.

### The dashboard

Five pages, server-rendered, no build step and no client framework:

| Page | Shows |
|---|---|
| `/` | every repository, store totals, the current log root |
| `/r/<repo>` | accuracy per commit as one line per branch, and a history table with a DAG rail showing where branches part |
| `/c/<hash>` | one commit's provenance, its base reference, its files, and its inclusion proof rung by rung |
| `/api` | the REST surface — every route, its parameters, and its status codes |
| `/ops` | per-subsystem health, including a live recomputation of the log root |

The inclusion proof on a commit page is served by the Hub, which means reading
it is trusting the Hub. **Recompute in this browser** removes that: it fetches
the proof as JSON, folds it with a SHA-256 implementation shipped in
`hub/static/verify.js`, and marks each rung against what it computed. Nothing on
the page is taken as input.

That hash is written out by hand rather than delegated to `crypto.subtle`, which
is unavailable outside a secure context. The demo runs on two laptops over a
LAN at `http://192.168.x.x:8000`, and only `https`, `localhost` and `127.0.0.1`
count as secure — so a verifier built on `crypto.subtle` would be `undefined`
in exactly the place it is meant to be used. A hand-written hash has one
dangerous failure mode, though: if it were subtly wrong it would report
tampering that never happened. So it checks itself against published vectors
before rendering any verdict and refuses to answer if they disagree, and
`scripts/check_verify_js.py` cross-checks it against Python's `hashlib` on the
same inputs so the two implementations cannot quietly drift.

The `/ops` board reports whatever is not wired up — an unanchored log, an
unconfigured chain, a Hub accepting pushes with no token — as amber rows rather
than hiding them or failing the page. A dependency that is missing, slow, or
misconfigured has to be visible, because the alternative is discovering it live.
It is also where the log audits itself: one row recomputes the root from the
leaves and compares it against the last anchored one, which is a live check of
the exact property the project claims.

Everything is configuration, never a hard-coded host: `AETHEL_HUB_URL`,
`AETHEL_HUB_DATA`, `AETHEL_HUB_HOST`, `AETHEL_HUB_PORT`, and `AETHEL_HUB_TOKEN`
(set it to require a token on every write; unset means an open Hub, which the
ops board reports rather than hides). `.env.example` is the tracked template
documenting all of them; copy it to `.env`, which is ignored. A real shell
variable always beats the file, so a one-off override never fights a default.

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

## Running the demo

### The dashboard alone, in one command

`scripts/seed_demo.py` builds a Hub store with eight commits across three
branches, using synthetic adapter weights, so every page has real content
without a training run:

```bash
python scripts/seed_demo.py --force
AETHEL_HUB_DATA=.demo/hub-data python -m hub
```

The store lands in `.demo/` and is git-ignored — the script is committed, the
~5 MB of synthetic bytes it produces are not, because one command regenerates
them byte for byte.

### The full path, from training to a published branch

Needs the `[ml]` extra and one download of the datasets. Run from the
repository root:

```bash
scripts/dev.sh                     # venv + install + checks
pip install -e ".[ml]"             # torch/transformers/peft — large
python setup_demo_data.py          # demo datasets, ~800 samples each
```

Then the version-control story, in a scratch directory:

```bash
mkdir demo && cd demo

aethel init --model distilbert-base-uncased

aethel train --config ../train_yaml/sst2_config.yaml
aethel commit -m "SST-2 sentiment baseline"

aethel train --config ../train_yaml/sst2_config.yaml   # more epochs
aethel commit -m "SST-2, longer schedule"

aethel branch emotion                                  # branch to a new task
aethel checkout emotion
aethel train --config ../train_yaml/emotion_config.yaml
aethel commit -m "Emotion, 6 classes"

aethel log --all                                       # both branches
aethel checkout main                                   # the sentiment adapter is back
aethel checkout emotion                                # and the emotion one
aethel fsck                                            # every object re-hashed
```

Serve a Hub in a second terminal and publish to it:

```bash
python -m hub                                          # from the repo root
```

```bash
aethel push --remote http://localhost:8000 --repo my-model
aethel push --remote http://localhost:8000 --repo my-model --branch emotion
```

Both branches now appear at `http://localhost:8000/r/my-model`, each commit
carries its accuracy, and every commit page will fold its own inclusion proof
back to the root the Hub publishes.

## Development

```bash
python -m pytest                    # 693 tests, no GPU or network required
python -m pytest --cov=aethel.core  # 96% core coverage
ruff check .
```

No test touches the network: the Hub's tests run the ASGI application
in-process, so a push is exercised end to end without a socket. The VCS core
needs no `[ml]` extra and the whole suite finishes in seconds.

The front end is tested rather than eyeballed, because its failures are quiet
ones. The stylesheet's motion tokens are asserted to exist on bare `:root` and
to be the only source of any duration in the file — a `var()` that stops
resolving degrades to an instant state change, which no screenshot would catch.
The served fonts are asserted to resolve at the URLs the CSS names, the error
pages to keep their status codes and their security headers, and the verifier's
JavaScript to agree with `hashlib`.

CI runs two jobs. `core` installs only `[dev]` on Python 3.10–3.13, which fails
if the core ever grows a dependency on torch or fastapi. `hub` installs
`[dev,hub]`, asserts the Hub imports, then runs the same suite — without that
job the Hub's tests would skip on every run and a broken Hub could stay green.

Two scripts regenerate committed assets rather than leaving them unexplained.
`scripts/build_fonts.py` subsets the two typefaces the Hub serves and writes
`hub/static/fonts/`; the built `woff2` files are committed so a clean checkout
renders correctly offline, and only the upstream cache is ignored.
`scripts/check_verify_js.py` is the JavaScript-versus-Python cross-check
described above.

## Status

Working today: `init`, `train`, `commit`, `branch`, `checkout`, `log`,
`status`, `fsck`, `push` · a Hub with a REST API, a five-page server-rendered
dashboard, an ops health board, an append-only Merkle transparency log serving
inclusion proofs, and an in-browser verifier that recomputes a root without
trusting the page it is on.

Planned, in order: anchoring the log's root to a public testnet so a model's
recorded history cannot be rewritten even by whoever runs the Hub · a Pinata
mirror of patch blobs (address only — integrity always comes from
`blob_sha256`) · adapter `diff` and `merge` (task arithmetic, TIES, DARE) ·
Ed25519 commit signing · dataset fingerprinting.

The Merkle tree in `aethel/core/aggregator.py` is what the Hub's log is built
on: it is the same code the chain layer will anchor, and it is tested for
second-preimage resistance via RFC 6962 domain separation.

### What the chain will and will not prove

Worth stating precisely before it is built, because the overclaim is the
tempting one. Anchoring proves that a commit record existed at a given time and
has not been altered or removed since — including by whoever runs the Hub. It
does not prove the weights were really trained from the claimed base, or that
the data was what the record says. A liar can publish an honest-looking record;
the anchor only stops them changing it afterwards. Closing that gap is what
dataset fingerprinting, the reproducibility record, and commit signing are for.

## Documentation

- [`docs/SYSTEM_REFERENCE.md`](docs/SYSTEM_REFERENCE.md) — architecture, every
  command, the storage model, and a runnable demo walkthrough
- [`docs/design/dashboard.md`](docs/design/dashboard.md) — why the dashboard
  looks and moves the way it does
- [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) — roadmap, threat model, and
  design rationale
- [`docs/TEAM_WALKTHROUGH.md`](docs/TEAM_WALKTHROUGH.md) — engineering record
  of the rebuild
