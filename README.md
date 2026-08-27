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

Two ways in. The **seeded path** needs nothing but the base install and puts a
populated dashboard on screen in two commands. The **full path** trains for
real and needs the `[ml]` extra. They end in the same place, which is why the
seeded one is worth having rehearsed: if a training run misbehaves in front of
an audience there is a way to keep going.

The transcripts below are captured output, not illustrations. Two caveats so
they are read correctly. They were recorded with stand-in adapter weights so
that the whole cycle runs with no GPU, so the **accuracy column is fixture
data** until you run the `[ml]` path — object counts, deduplication and log
behaviour are real either way. And every hash is a hash of the actual bytes, so
**your hashes will differ from these**; that is the point of a
content-addressed store, not a discrepancy.

### Where the Hub keeps its data — and the one trap in it

There is no database, and none is needed. A Hub's whole state is three things
on disk under one directory:

```
hub-data/
  objects/       blobs, trees, commits and base refs, same layout as a repo
  log.jsonl      the append-only transparency log, one JSON leaf per line
  repos.json     which branch of which repository points at which commit
```

So published history persists because it is *files*. Stop the Hub, reboot the
machine, start it again on the same directory and everything is there —
demonstrated below. Nothing is held in memory that matters, and there is no
migration to run.

The trap is that `AETHEL_HUB_DATA` defaults to `./hub-data`, **relative to
whatever directory you start the Hub from**:

```
$ python3 -c "from hub.config import HubConfig; print(HubConfig().data_dir)"
/home/you/Aethel/hub-data          # started from the repo root
/tmp/hub-data                      # started from /tmp
```

Start the Hub from a different directory than last time and you get a *second,
empty* store. Nothing was lost, but the dashboard is blank and it looks exactly
like data loss, which is a bad thing to discover on a projector. Always export
an absolute path:

```bash
export AETHEL_HUB_DATA="$HOME/aethel-hub-data"
export AETHEL_HUB_TOKEN=pick-a-token     # required on every write once set
python -m hub
```

If a page ever looks emptier than it should, the Hub tells you which store it
opened — `GET /api/v1/version` reports `data_dir`, and `/ops` shows the object
count beside it. Check that before believing anything is gone.

### Path 1 — the dashboard alone, in two commands

`scripts/seed_demo.py` builds a Hub store with eight commits across three
branches, using synthetic adapter weights, so every page has content without a
training run:

```bash
python scripts/seed_demo.py --force
AETHEL_HUB_DATA="$PWD/.demo/hub-data" python -m hub
```

The store lands in `.demo/` and is git-ignored — the script is committed, the
~5 MB of synthetic bytes it produces are not, because one command regenerates
them byte for byte. The weights are seeded from the step name, so a rebuild
gives identical hashes and a rehearsed demo's URLs keep working.

### Path 2 — training, committing, publishing

Needs the `[ml]` extra and one dataset download. From the repository root:

```bash
scripts/dev.sh                     # venv + install + checks
pip install -e ".[ml]"             # torch/transformers/peft — large
python setup_demo_data.py          # demo datasets, ~800 samples each
```

Serve a Hub in a second terminal — the three lines from the section above — and
give this terminal the same token, since the Hub requires it on every write:

```bash
export AETHEL_HUB_TOKEN=pick-a-token   # or pass --token on each push
mkdir demo && cd demo
aethel init --model distilbert-base-uncased --author johndoe
```

```
Initialized Aethel repository in /home/you/demo/.aethel
Base model: distilbert-base-uncased
Revision:   12040accade4e8a0f71eabdb258fecc2e7e948be
Base ref:   c0c3831886b3
Author:     johndoe

Base weights are not stored — only the pinned reference.
```

That revision SHA is the reproducibility contract: not the tag `main`, which
moves, but the immutable commit the weights were fetched at. `init` is
idempotent — running it twice prints the same thing and is not an error.

Training writes into `.aethel/workspace`, and `commit` reads whatever is there.
The two are deliberately separate, so a commit never retrains and a failed
training run never half-writes history:

```bash
aethel train --config ../train_yaml/sst2_config.yaml
aethel commit -m "SST-2 sentiment baseline"
aethel push --remote http://localhost:8000 --repo sentiment-lora
```

```
Hub needs 6 of 6 objects.
  sent blob    6b9be589d3fb 321 B
  sent blob    d193d1b6823d 576.0 KB
  sent blob    fd3d777ad3e6 213 B
  sent tree    87be15c8b71d 283 B
  sent base    c0c3831886b3 130 B
  sent commit  95c419882714 629 B

Pushed 6 object(s) · 577.5 KB · 0 already present
sentiment-lora/main → 95c419882714
Transparency log: 1 leaves (1 new)
Log root: b7718fce42b1a14655add6507f2bf94f9026f3f30af9e61ef7986dbf547e755e
```

Six objects for a whole first version: the weights, the adapter config, the
training record, a tree naming them, the base reference, and the commit. The
base model itself is not among them and never will be.

### Commit after commit, push after push

The cycle repeats without ceremony. Train again, commit again, push again — and
the second push shows what content addressing buys:

```bash
aethel train --config ../train_yaml/sst2_config.yaml   # longer schedule
aethel commit -m "SST-2, longer schedule"
aethel push --remote http://localhost:8000 --repo sentiment-lora
```

```
Hub needs 4 of 10 objects.
  sent blob    1bbf0de683c1 321 B
  sent blob    fc0adce1f14d 576.0 KB
  sent tree    0140c2a1c8ac 283 B
  sent commit  0283a7c7652e 689 B

Pushed 4 object(s) · 577.3 KB · 6 already present
sentiment-lora/main → 0283a7c7652e
Transparency log: 2 leaves (1 new)
Log root: 905c77ef85bcc5e5601cea0f0994248ec4ca1d64049c3bf45b814f2a48a06e36
```

Ten objects are now reachable but only four crossed the wire. The first
commit's six were already there, and one of the *new* commit's three files —
`adapter_config.json`, byte-identical because the rank and targets did not
change — was recognised as an object the Hub already holds and was not sent
again. Nothing tracks that; it falls out of naming objects by their hash.

The log grew by exactly one leaf and the root changed. That pairing is the
thing to watch during the demo: one published commit, one leaf, one new root,
and the old root is now provably inside the new one.

### Pushing the same thing twice

A re-push with nothing new is not an error and does not append a leaf:

```
Hub already holds every object — nothing to upload.

Pushed 0 object(s) · 6 already present
sentiment-lora/main → 95c419882714
Transparency log: 1 leaves (0 new)
Log root: b7718fce42b1a14655add6507f2bf94f9026f3f30af9e61ef7986dbf547e755e
```

Same leaf count, same root, `(0 new)`. Worth doing on purpose in front of a
panel: it shows the log is keyed by content rather than by how many times
someone pressed the button, and it means a flaky network mid-push can be
retried without corrupting anything.

To see what a push *would* do without doing it:

```bash
aethel push --remote http://localhost:8000 --repo sentiment-lora --dry-run
```

```
Hub already holds every object — nothing to upload.
--dry-run: nothing was uploaded.
```

### Branching, and going back

A branch is one file holding one commit hash. Creating one copies no weights:

```bash
aethel branch wide-rank
aethel checkout wide-rank

cp ../train_yaml/sst2_config.yaml sst2_r16.yaml    # then set lora_rank: 16
aethel train --config sst2_r16.yaml
aethel commit -m "Rank 16 on its own branch"
aethel push --remote http://localhost:8000 --repo sentiment-lora
```

```
Pushed 5 object(s) · 577.5 KB · 10 already present
sentiment-lora/wide-rank → 91594f6d0245
Transparency log: 3 leaves (1 new)
```

`push` publishes whichever branch HEAD is on; `--branch <name>` overrides that.
All three of this commit's files were sent this time, config included — the rank
changed, so the config's bytes changed, so its hash changed. Deduplication is
not a heuristic that sometimes helps; it is the identity of the object.

Then history across both lines, and time travel between them:

```bash
aethel log --all
```

```
                                 Commit History
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ Commit       ┃ When                ┃ Author  ┃ Accuracy ┃ Message            ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ 91594f6d0245 │ 2026-08-27 09:53:22 │ johndoe │    0.921 │ Rank 16 on its own │
│              │                     │         │          │ branch             │
│ 0283a7c7652e │ 2026-08-27 09:49:15 │ johndoe │    0.912 │ SST-2, longer      │
│              │                     │         │          │ schedule           │
│ 95c419882714 │ 2026-08-27 09:47:55 │ johndoe │    0.885 │ SST-2 sentiment    │
│              │                     │         │          │ baseline           │
└──────────────┴─────────────────────┴─────────┴──────────┴────────────────────┘
```

```bash
aethel branch                # * marks the current branch
aethel checkout main         # the earlier adapter is restored, byte for byte
aethel checkout wide-rank    # and back
aethel fsck                  # every stored object re-hashed
```

```
Checked 15 objects (3 commits, 3 trees, 8 blobs, 1 bases)
Repository integrity OK.
```

A checkout restores from the commit's tree, not from whatever happens to be on
disk, so what comes back is exactly what was committed. Both argument orders
work — `aethel checkout main --force` and `aethel checkout --force main` — which
is a fix worth knowing about, because the first one used to fail.

### Does published history survive a restart?

Yes, and it is worth proving rather than asserting, because "we have no
database" invites the question. Before stopping the Hub:

```
leaves 3
root   4b911f4bcb27d0669fb70f3e11902b8cdb980afe6bcccac5b2a56a2b014a4e87
repos  ['sentiment-lora']  branches ['main', 'wide-rank']
```

Kill the process, start it again on the same `AETHEL_HUB_DATA`:

```
leaves 3
root   4b911f4bcb27d0669fb70f3e11902b8cdb980afe6bcccac5b2a56a2b014a4e87
repos  ['sentiment-lora']  branches ['main', 'wide-rank']
```

Identical root, identical refs. It holds across a reboot too, for the same
reason: the seeded store used for rehearsals has objects written on 25 August
and was serving them unchanged on the 27th, with the machine powered down in
between. So the demo can be paused, the laptop closed, and picked up at the same
commit — and a `train → commit → push` cycle started weeks later appends to the
same tree rather than starting a new one.

What the dashboard then shows, at `http://localhost:8000`: the repository and
its branches at `/r/sentiment-lora`, one commit at `/c/<full hash>` with its
parent, its base reference, its metrics and its inclusion proof, the API
reference at `/api`, and the health board at `/ops`. The commit page's verifier
recomputes the root from the proof **in the browser**, so a PASS does not depend
on trusting the page it is printed on.

The same check runs from a shell, which is the version worth showing when a
projector is involved — fetch a proof, post it back, then flip one character of
one sibling and post it again:

```bash
curl -s http://localhost:8000/api/v1/log/proof/<full-commit-hash> > proof.json
curl -s -X POST http://localhost:8000/api/v1/log/verify \
     -H 'Content-Type: application/json' --data @proof.json
```

```
{"valid": true,  "commit_hash": "0283a7c7652e…", "root": "4b911f4bcb27…"}
{"valid": false, "commit_hash": "0283a7c7652e…", "root": "4b911f4bcb27…"}   # after one hex digit of a sibling was flipped
```

A proof is two sibling hashes here, 64 bytes, for a log of three leaves. At a
million leaves it would be twenty siblings — 640 bytes. That is the property
that makes anchoring one root worth doing: the cost of proving one commit
belongs to a history grows with the logarithm of the history, not its size.

### What is deliberately not there yet

Say this plainly rather than letting a panel find it. `aethel merge` is not
implemented — branch and checkout work, merging adapters (task arithmetic,
TIES, DARE) is in progress and lands after this review. There is no `pull` or
`clone`: a Hub serves patches over its REST API and the dashboard, and the
client half of that is later work. And nothing is anchored to a chain yet —
the transparency log is built, serves inclusion proofs and is verified in the
browser, but the on-chain root that would make the Hub operator accountable is
the next layer. `/ops` says so on its own: the chain row reads `not configured`
and the log-versus-anchored-root row reads `never anchored`, both amber, rather
than quietly rendering green.

### When something goes wrong

Every failure path below is a real message from the tool, and each exists
because the alternative was worse — a silent overwrite, an orphaned commit, or
a publishable model that learned nothing.

| What you did | What Aethel says |
|---|---|
| Ran a command outside a repository | `Not an Aethel repository (or any parent directory). Run 'aethel init --model <owner/model>' first.` |
| `aethel log` before any commit | `No commits yet. Run 'aethel train' then 'aethel commit'.` |
| `aethel commit` with an empty workspace | `ObjectNotFound: No adapter weights in <path>. Expected one of: adapter_model.safetensors, adapter_model.bin. Run 'aethel train --config <yaml>' first.` |
| `aethel train` with a dataset path that does not exist | Fails. Training on synthetic text needs an explicit `--allow-stub`, so a typo cannot quietly produce a committable model that learned nothing |
| `aethel checkout` with uncommitted work | `Workspace has uncommitted changes:` then the files, then `Commit them, or re-run with --force to discard.` Compared by hash against the current commit's tree, not by mtime |
| `aethel checkout <hash>` | `HEAD is now detached at 95c419882714` · `Commits are blocked while detached.` |
| `aethel commit` while detached | `Detached HEAD — commit blocked.` and then the three commands that recover it. Stricter than Git, which only warns — a commit made there is referenced by nothing and is lost at the next checkout |
| `aethel push` while detached | `InvalidRef: HEAD is detached at 95c419882714. Check out a branch first, or name one with --branch.` |
| `aethel checkout nope` | `InvalidRef: 'nope' did not match any branch or commit.` |
| `aethel branch main` when it exists | `BranchExists: A branch named 'main' already exists.` |
| Deleted the branch you are standing on | `Cannot delete 'main' — it is the current branch.` |
| Deleted any other branch | Succeeds, and says `Its commits are still in the object store at <hash> — nothing was destroyed.` |
| `aethel push` with a wrong or missing token | `RemoteError: The Hub rejected the push token (negotiation).` · `Set it with: export AETHEL_HUB_TOKEN=<token>  (or --token)` |
| `aethel push` at a Hub that is not running | `RemoteError: Cannot reach the Hub at http://localhost:9911 — is it running?` · `Start it with: python -m hub  (or scripts/dev.sh)` |
| `aethel push --branch <name>` with no commits on it | `InvalidRef: Branch '<name>' has no commits yet. Run 'aethel train' then 'aethel commit' first.` |
| Opened `/c/<short-hash>` in the dashboard | 404. Commit pages take the full 64-character hash; the CLI's 12-character prefixes are for reading, not for URLs |
| A byte of a stored object was corrupted | `aethel fsck` prints `1 CORRUPT object(s):`, the hash and the exact path, then `Repository integrity check FAILED.` and exits 1 |
| Deleted a branch, then ran `fsck` | `5 unreachable object(s) — not referenced by any branch; harmless (interrupted commits)`. Reported, never deleted — unreachable is not the same as corrupt, and nothing here removes data behind your back |
| Dashboard looks empty after a restart | Almost certainly the `AETHEL_HUB_DATA` trap above. Check `data_dir` at `/api/v1/version` |

Two more worth rehearsing because they are environmental rather than
behavioural. Campus wi-fi frequently isolates clients from each other, so a
second machine may fail to reach the Hub even with the right IP — a phone
hotspot is the quickest way around it. And the port is configurable
(`AETHEL_HUB_PORT`), which matters when something else already owns 8000.

## Development

```bash
python -m pytest                    # 715 tests, no GPU or network required
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
