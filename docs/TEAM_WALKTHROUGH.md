# Aethel: Team Walkthrough

**What changed, why, and what's next.**
Read alongside `docs/PROJECT_PLAN.md`
(the strategy doc); this one is the engineering record.

---

## 0. Thirty-second summary

We audited the prototype, found a bug that silently returned the **wrong
commit**, and rebuilt the storage layer so that bug cannot exist. Along the
way we found a second, separate security flaw in the Merkle tree that would
have broken the blockchain layer before we ever built it. On top of that core we
built the publishing layer: `aethel push`, a Hub that serves patches, and the
append-only Merkle transparency log the chain will anchor.

| | Before | After |
|---|---|---|
| Tests | **0** | **715** |
| Core coverage | 0% | **96%** |
| Hub + remote coverage | n/a | **91%** |
| Linter | none | ruff, clean across the whole tree |
| CI | none | GitHub Actions, 4 Python versions + a Hub job |
| Known data-corruption bugs | 1 (documented, unfixed) | 0 |
| Dead code | ~340 lines | 0 |
| Dependencies pinned | 0 of 13 | all |
| Can the CLI run without PyTorch? | no | yes |
| Is the repo portable? | claimed, false | true, tested |
| Can a commit be published and proven? | no | yes: Hub + inclusion proofs |

The core rebuild was net **692 insertions, 1792 deletions** across tracked
files, plus 16 new files: we deleted more than we added. The publishing layer
on top of it is 7,098 lines under `hub/` and 440 under `aethel/remote/`, and the
suite behind the whole project is 7,151 lines, more test code than product code.
That ratio is deliberate, because the Hub is the part an outsider is asked to
trust.

---

## 1. The three findings

### 1.1 The commit corruption bug (the headline)

**What it was.** `commit.py` stored commit metadata *inside the adapter's
folder*:

```
.aethel/objects/<adapter_hash>/
    adapter_model.safetensors
    commit.json          <-- ONE file
```

Adapter folders are named by the hash of the weights, so **identical weights
mean the same folder**. Two commits with the same weights both wrote
`commit.json` to that one path. The second overwrote the first.

**Why nobody noticed.** SQLite still had two rows with two distinct commit
hashes, so `aethel log` looked perfectly correct. Only `checkout` read the
metadata, and it resolved `commit_hash → adapter_hash → folder → commit.json`,
landing on whichever commit wrote last.

**Proof, captured from the real run before the fix:**

```
Commit:  7ad5fb13...  Adapter: c531c1e5...  Stored: .aethel/objects/c531c1e5...
Adapter folder already exists, deduplicating.
Commit:  9a705bbe...  Adapter: c531c1e5...  Stored: .aethel/objects/c531c1e5...
                                                     ^^^ same path
```

Five tests failed against the old code:

```
FAILED test_first_commit_retains_its_own_message
FAILED test_each_commit_reports_its_own_parent
FAILED test_no_commit_field_leaks_between_deduplicated_commits[message]
FAILED test_no_commit_field_leaks_between_deduplicated_commits[parent_hash]
FAILED test_no_commit_field_leaks_between_deduplicated_commits[timestamp]
```

**Which failure matters most.** Not the message, `parent_hash`. A wrong
parent means a **wrong commit DAG**, and that DAG is exactly what we plan to
anchor on-chain. We would have produced a tamper-proof record of false
lineage: cryptographically guaranteed wrong.

**It was already known.** `PROJECT_STATUS_REPORT.md` listed it verbatim as a
"known limitation" and shipped anyway. That is the real lesson: it was written
down, and nothing executed the code, so it survived.

**The fix.** Commits are keyed by *their own* hash, in a separate directory:

```
objects/commits/<ab>/<rest>   <-- keyed by commit hash: cannot collide
objects/blobs/<ab>/<rest>     <-- keyed by content: deduplicates as intended
```

Deduplication is preserved, identical weights still share one blob. The
collision has **no representable form** now. It isn't guarded against; it
can't be expressed.

### 1.2 The Merkle second-preimage vulnerability (Sathwik's area)

`aggregator.py` hashed leaves and internal nodes identically:

```python
def hash_data(d): return sha256(d)         # leaf
def hash_pair(a, b): return sha256(a + b)  # internal node
```

That makes an internal node indistinguishable from a leaf. Demonstrated
against the old code:

```python
real   = MerkleTree([l0, l1, l2, l3])
forged = MerkleTree([H(l0+l1), H(l2+l3)])   # "leaves" are real's internal nodes
real.get_root() == forged.get_root()        # -> True
```

**Why this would have killed the blockchain layer.** Our entire pitch is "the
Hub cannot rewrite history." With this flaw, an operator could produce a valid
inclusion proof for a value that was **never a committed leaf**, forging
exactly what the anchor exists to prevent. We would have anchored a root that
proved nothing.

**The fix** is RFC 6962 domain separation (what Certificate Transparency
uses):

```
leaf     = SHA256(0x00 || data)
internal = SHA256(0x01 || left || right)
```

Different prefixes mean leaf and node hash spaces cannot overlap. 29 tests
now cover this, including a regression test that fails if anyone removes the
prefixes.

We also kept the original's **promotion** of odd nodes (carry up unchanged)
rather than duplication. Duplicating the last node is the Bitcoin behaviour
behind CVE-2012-2459, where two different leaf sets produce one root. The
original got this right.

### 1.3 The project had no evaluation

No eval split, no `compute_metrics`. The only recorded numbers were training
loss and runtime. An ML project that could not say whether its models were any
good. `aethel log` now shows accuracy per commit; the module that computes those
metrics sits on `feat/evaluation-metrics`, green on CI, not yet merged.

---

## 2. Architecture: before and after

### Before

```
aethel/
  commands/   init, commit, status, train, branch, checkout   <- all logic here
  core/       model.py, aggregator.py                          <- BOTH dead
```

Every command re-declared its own constants (`AETHEL_DIR`, `HEAD`, hash
regex), five copies. Business logic, file I/O, SQLite and terminal printing
were interleaved in the same functions. Nothing was testable without a
terminal.

### After

```
aethel/
  core/       pure logic. No torch. No network. Never prints. Never exits.
    errors.py       exception hierarchy (65 lines)
    hashing.py      SHA-256 + canonical JSON (99)
    atomic.py       crash-safe writes + locking (162)
    objects.py      content-addressed store (232)
    refs.py         HEAD + branches (244)
    repo.py         discovery + layout (142)
    commits.py      commit creation + history (228)
    aggregator.py   Merkle tree, now backs the Hub's log (142)

  remote/     talking to a Hub. Hashes checked at both ends.
    objects.py      branch -> push plan (115)
    http.py         upload, negotiate, move a ref (302)

  commands/   thin CLI. Parses args, calls core, renders output.
    _common.py      error rendering boundary (78)
    init.py (133)  commit.py (77)  branch.py (116)
    checkout.py (105)  log.py (140)  fsck.py (131)  push.py (302)
    train.py (477)  <- the only file that needs torch

hub/          the server. Needs the [hub] extra; nothing in aethel/ imports it.
  config.py       env-driven config, no hard-coded hosts (136)
  storage.py      the Hub's own object store + repo index (277)
  log.py          the append-only transparency log (280)
  api.py          REST API (565)
  apidocs.py      /api generated from the routes (511)
  errors.py       typed HTTP errors + error pages (202)
  security.py     CSP nonce, security headers (142)
  views.py        the five rendered pages + ops board (1117)
```

**The rule that drives this:** core raises `AethelError` and never prints;
`commands/_common.py` is the single place where errors become terminal output
and exit codes.

**The second rule, added with the Hub:** the dependency arrow points one way.
`hub/` imports from `aethel.core`; nothing in `aethel/` imports from `hub/`.
That is what lets the Hub reuse the same hashing, the same Merkle tree and the
same commit format as the client, while the client stays installable without a
web framework.

**Why it matters practically:** the whole VCS *and its publishing path* are
testable with no GPU, no model download, and no network. 715 tests run in
**under 10 seconds**, including full pushes, the Hub's tests drive the ASGI app
in-process instead of over a socket. That is why we can afford to run them on
every push.

---

## 3. The object model, in detail

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

**Four object types, each named by the SHA-256 of its own content.**

- **blob**: raw bytes of one file. Identical files stored once, ever.
- **tree**: a manifest mapping filename → blob hash. This is why checkout
  restores `adapter_config.json` and `training_info.json` too, not just the
  weights.
- **commit**: metadata: parent, tree, base, message, author, timestamp,
  training info.
- **base**: `(model_id, revision_sha)` plus config/tokenizer hashes. **The
  base model's weights are never stored.**

**Sharding** (`<ab>/<rest>`, first two hex chars as a directory) is what Git
does. Without it, one directory accumulates tens of thousands of entries and
filesystem lookups degrade.

### A commit object

```json
{
  "schema": 2,
  "parent_hash": "<hash> | null",
  "tree": "<tree hash>",
  "adapter_blob": "<blob hash>",
  "base": "<base object hash>",
  "message": "SST-2 sentiment baseline",
  "author": "john_doe",
  "timestamp": "2026-06-06T15:16:27+00:00",
  "training_info": { "metrics": { "eval_accuracy": 0.871 }, ... }
}
```

### Why there is no database any more

The old design had SQLite as a **second source of truth**. `checkout.py:51-70`
could not resolve a commit without `repo.db`, which made the docs' claim,
"copy the folder and you have the version", **false**.

We removed SQLite entirely. History is walked from refs through the object
store. Proven:

```console
$ cp -r .aethel /tmp/aethel-copy/ && cd /tmp/aethel-copy
$ find . -name '*.db' | wc -l
0
$ aethel log --all      # full history, intact
$ aethel fsck           # Repository integrity OK.
```

The plan said "SQLite is a cache with `reindex`". Deleting the second source
of truth is a stronger claim than managing it, and it made `reindex`
unnecessary. **This is a deliberate deviation from the plan; flag it if you
disagree.**

---

## 4. Crash safety (`atomic.py`)

The old `commit()` did five mutating steps with no transaction, no `fsync`, no
rollback. Interrupt it anywhere → corrupt or orphaned repo.

Every durable write now follows this sequence, and each step is load-bearing:

1. **Write to a temp file in the same directory.** `os.replace` is only atomic
   *within one filesystem*; `/tmp` is often a different mount.
2. **`fsync` the temp file.** Without it the rename can hit disk before the
   data; you get a correctly-named empty file.
3. **`os.replace`.** Atomic on POSIX *and* Windows, unlike `os.rename`, which
   fails on Windows when the destination exists.
4. **`fsync` the parent directory**, so the rename itself survives power loss.

**Ordering inside a commit also matters:** objects are written first, the ref
is advanced last. Objects are immutable and self-verifying, so an interrupted
commit leaves unreferenced garbage that `fsck` reports and `gc` will collect.
Advancing the ref first would leave a branch pointing at a commit whose
objects don't exist, unrecoverable.

**Locking** uses `O_CREAT | O_EXCL` for ref updates. Without it, two
concurrent commits both read the same branch tip and the second silently
discards the first. A crashed process leaves the lock behind deliberately,
the same trade-off Git makes with `index.lock`.

---

## 5. The defect list, and where each one stands

Fourteen of the sixteen are closed. The two that are not say so in their own
row, because a table titled "every defect fixed" with an open row in it is worse
than no table.

| ID | Defect | Fix |
|---|---|---|
| **S1.1** | Commit metadata overwritten between identical-weight commits | Commits keyed by own hash |
| **S1.2** | No evaluation anywhere | *(partly: `log` and the dashboard display accuracy and prefer it over training metrics; the module that computes it sits on `feat/evaluation-metrics`, green on CI, not yet merged, never run with torch)* |
| **S1.3** | Zero tests | 715 tests, 96% core coverage |
| **S2.1** | Documented IMDB quickstart crashed (Arrow vs CSV) | Detects Arrow dirs, explains the conversion |
| **S2.2** | Typo'd dataset path → silently trained on fake text, still committable | Refuses; requires explicit `--allow-stub` |
| **S2.3** | No atomicity in commit | `atomic.py` everywhere |
| **S2.4** | Task type chosen by try/except ordering | *(still open: see §8)* |
| **S2.5** | SQLite as second source of truth | Removed entirely |
| **S3** | `USERNAME` is Windows-only → every Linux commit authored `"User"` | Checks `AETHEL_AUTHOR`, `USER`, `USERNAME`, `LOGNAME` |
| **S3** | `branch.py:86` used bare `startswith` for path containment | Proper parent-directory comparison |
| **S3** | `trust_remote_code=True` on all 5 datasets: arbitrary code execution | Removed |
| **S3** | ~340 lines dead code (`model.py`, `aggregator` unused, `experiment_lora.py`) | Deleted; `aggregator.py` rewritten and now used |
| **S3** | `aethel/core/__init__.py` missing → `find_packages()` never shipped core | Added; explicit package list in `pyproject.toml` |
| **S3** | 13 unpinned deps in a *reproducibility* project | All pinned; `bitsandbytes` → optional `[gpu]` |
| **S3** | `train` wiped the workspace with no confirmation | Commit no longer touches the workspace at all |
| **Docs** | Three docs contradicted the code and each other | Deleted 3 stale status docs; rewrote README |

### New safety behaviours

- **Commits blocked on detached HEAD** (kept from the original, stricter than
  Git, which only warns). Exits 1 and prints recovery steps.
- **`checkout` refuses to discard uncommitted work** without `--force`, and
  compares by *content hash*, not mtime.
- **Verification on every read**, not just `fsck`. Reading a commit re-hashes
  it; a mismatch raises `CorruptObject` immediately.

---

## 6. What we kept, and why

Not everything was wrong. These were real engineering decisions and we ported
them **verbatim**:

| Kept | Original location | Why it was right |
|---|---|---|
| HF revision pinning | `init.py:37-47` | Pins the immutable SHA, not a tag. Tags move; SHAs don't. Production ML often gets this wrong. |
| Detached-HEAD guard | `commit.py:315-329` | Blocks instead of warning. Stricter than Git, and correct. |
| LoRA target inference | `train.py:248-270` | Priority list + `nn.Linear` scan makes the trainer architecture-agnostic. |
| Canonical JSON hashing | `commit.py:302` | `sort_keys=True` + compact separators. Deterministic. |
| Merkle odd-node promotion | `aggregator.py` | Avoids CVE-2012-2459. |
| Exception-per-module | all commands | Clean error boundaries; we generalized it. |

The corruption bug is what unreviewed generated code produces, every time, and
six of previous decisions were good enough to keep untouched.

---

## 7. The publishing layer

This is the part built on top of the rebuilt core, and the part that gives the
chain something to anchor. Three pieces: `aethel push`, the Hub, and the log.

### 7.1 Why the Hub has to exist before the chain

On a single-user local tool, a blockchain loses the argument to "why not just a
database?"; there is no untrusted party, so there is nothing to prove to
anyone. A hosted Hub has an operator who *can* rewrite published lineage. The
Hub is what creates the adversary the chain defends against. Build it in the
other order and the anchoring looks decorative, because it is.

### 7.2 The push protocol, and why each step is where it is

```
1. plan       walk the branch locally; verify every object exists
2. negotiate  ask the Hub what it lacks
3. upload     blobs -> trees -> bases -> commits (oldest first)
4. move ref   last, and only after the Hub re-walks the history
```

**Planning happens before the first byte leaves the machine.** A push that
cannot be completed fails locally with the missing object named, rather than
half-way through, leaving a remote in a state someone has to reason about.

**The server decides the delta, not the client.** The client offers what it
holds; the Hub answers with what it is missing. The obvious alternative, the
client remembering what it pushed last time, is wrong in a specific way: a Hub
that lost an object would stay quietly incomplete forever, because the client
would never offer it again. `test_an_object_the_hub_lost_is_re_uploaded` deletes
a blob from the Hub's store and pushes again to pin exactly that.

**Dependency order is not cosmetic.** Every object references only objects sent
earlier. So an interrupted push leaves a Hub missing objects (retryable) rather than
a Hub holding a commit that points into nothing. It is the same reasoning as the
local commit path: objects first, refs last.

**The ref update doubles as the completeness check.** `POST .../refs` re-walks
the commit's entire ancestry in the Hub's own store and returns 409 if anything
is absent. A published branch therefore cannot point at something a client
cannot fetch. `test_the_ref_moves_only_after_every_object_is_present` posts a ref
with nothing uploaded and asserts the 409, no repo created, log size still zero.

### 7.3 Hashes are verified at both ends

Uploads are addressed by the hash the client computed. The Hub recomputes it
from the bytes it actually received and rejects a mismatch with 422. Then the
client re-checks the hash the Hub echoed back.

That last check sounds redundant and is not. It catches a remote that stores
something other than what it was sent, accidentally or otherwise. The test for
it patches `HubClient._request` so the real upload still happens and only the
*acknowledgement* is forged, then asserts the push fails. A test that merely
called the verifier with a bad value would prove the verifier works; this one
proves it is actually wired into the path.

**Bytes are uploaded verbatim, never re-serialized.** A tree or commit is sent
as the exact bytes on disk. Re-serializing anywhere (client or server) could
reorder keys, change the hash, and break every proof downstream. The rule is
easy to state and easy to violate by accident, which is why it is a rule.

### 7.4 The transparency log

Every commit the Hub accepts becomes a leaf, in acceptance order, in
`log.jsonl`. The Merkle root over those leaves is the Hub's commitment to its
own history.

Four decisions worth defending:

- **Append-only is structural.** `hub/log.py` has exactly one mutating
  operation. There is no update and no delete in the module, so the property
  holds because there is no code path that could break it, not because we chose
  not to call one.
- **The file is the truth; the tree is derived.** The log is JSONL, readable with
  `cat`, and the tree is recomputed from it on every read. A stale cache in a
  provenance system is worse than re-reading a small file.
- **Leaves are commit hashes.** A commit hash already commits to the whole
  commit body, so hashing the hash is enough and leaves stay fixed-width.
- **A re-push must not move the root.** Appending an already-logged commit
  resolves to its existing leaf. The API reports `logged_indices` (where the
  history sits) separately from `appended_indices` (what this call created), so
  an idempotent sync can't be misread as a change.

**One log for the whole Hub, not one per repository.** Leaves are keyed by commit
hash across every repo, so two repositories that push byte-identical commits
share one leaf and `repo` records which one got there first.

This is the decision that shapes the contract, so it is worth being able to
defend:

- **A single tree makes deletion of a whole repository detectable.** Every
  commit's proof binds it into the same tree as every other repo's commits, so
  the operator cannot quietly drop a repository from the anchored history without
  invalidating every proof already published for every other repo. With per-repo
  trees, removing a repository removes its tree, and nothing that survives is
  inconsistent; you would have to already know the repo existed to notice it was
  gone. Omission is the failure this layer exists to catch.
- **A verifier needs only a commit hash.** Per-repo trees mean leaves must be
  `hash(repo || commit)`, so checking inclusion requires knowing the repo name
  and spelling it exactly as the Hub did. The leaf stops being the thing the
  commit hash already commits to.
- **It is cheaper.** One anchor transaction per batch, regardless of how many
  repositories were active in it.

Per-repo *views* are still available, and belong on the dashboard: repo
membership is Hub metadata, and filtering leaves by `repo` is a display concern,
not a cryptographic one. What the chain indexes is ordering (`batchIndex`) and
reverse lookup (`root`), see §9.

### 7.5 What the tests actually cover

163 tests across the three files, and the ones worth knowing about are the
negative ones: a Hub with no token accepting a push, a wrong token being
refused, a detached HEAD refused with the Hub left completely empty, `--dry-run`
uploading nothing *and* not writing the repo name to config, a proof that
verifies against a tampered root failing, and a malformed proof returning False
rather than raising a 500.

The Hub's tests run the ASGI app in-process. `httpx.ASGITransport` cannot back a
synchronous client (it only implements `handle_async_request`), so the seam is
`httpx.Client` monkeypatched to return a `TestClient` over the app. One
`TestClient` is entered as a context manager to run the lifespan, which is what
populates `app.state`.

**The skip trap, and why CI has two jobs.** Those tests skip themselves when
FastAPI is absent, so a client-only install stays green. CI was installing only
`[dev]`, which meant a completely broken Hub would still have shown a green
tick. On today's suite that gap is **403 of the 715 tests**: 258 behind the four
module-level `importorskip("fastapi")` gates and 145 behind the two fixture
gates, leaving 312 that a client-only install actually runs. Measured, not guessed: a
meta-path finder that raises `ModuleNotFoundError` for `fastapi` reproduces the
CI environment exactly. (`pytest.importorskip` only skips on
`ModuleNotFoundError`; an `ImportError` raised inside a module body is
re-raised, which is the correct behaviour and why a stub module does not
simulate a missing package.) The `hub` job now installs `[dev,hub]` and asserts
the Hub imports before running anything.

---

## 8. Verify it yourself

```bash
# from the repository root

python3 -m pytest                      # 715 passed in ~8s
python3 -m pytest --cov=aethel.core    # 96%
ruff check .                           # All checks passed

# CLI works with no PyTorch installed
python3 -m aethel.main --help

# and a full push, with no network, in-process
python3 -m pytest tests/test_push.py
```

Test breakdown:

| File | Tests | Covers |
|---|---|---|
| `test_hub_views.py` | 126 | Every dashboard page: rendering, prose, the DAG rail, the accuracy chart |
| `test_hub_api.py` | 94 | Upload, negotiate, refs, reads, log endpoints, auth, health |
| `test_hub_errors.py` | 57 | Status codes, header survival, leak-free pages, the copy affordance |
| `test_core_refs.py` | 52 | HEAD, branches, name validation, traversal |
| `test_push.py` | 41 | Plan, round trips, refusals, client-side hash verification |
| `test_hub_security.py` | 39 | Headers, CSP nonces, body limits, token auth |
| `test_hub_apidocs.py` | 36 | The API reference against the routes actually served |
| `test_core_env.py` | 29 | `.env` loading, precedence, type coercion |
| `test_core_merkle.py` | 29 | Second-preimage, proofs, tamper detection |
| `test_core_objects.py` | 29 | Store, dedup, integrity, trees |
| `test_hub_log.py` | 28 | Append-only behaviour, idempotence, proofs, anchors |
| `test_core_hashing.py` | 24 | Canonical JSON, SHA-256 |
| `test_hub_motion.py` | 24 | Motion tokens, the reduced-motion split, what is allowed to move |
| `test_core_commits.py` | 23 | Creation, lineage, determinism |
| `test_core_atomic.py` | 18 | Crash safety, locking, concurrency |
| `test_hub_verify.py` | 18 | The in-browser verifier's structure and its self-check |
| `test_core_resolve.py` | 15 | Branch/hash/abbreviation resolution |
| `test_hub_fonts.py` | 14 | The served fonts resolve at the URLs the CSS names |
| `test_s1_corruption.py` | 10 | The corruption regression, via the real CLI |
| `test_cli_parsing.py` | 9 | Option order on the commands that take a positional |

**To reproduce the original bug for the demo:** check out a commit from before
the rebuild and run `test_s1_corruption.py` against it.

---

## 9. Still open

Be honest about these, a panel will find them.

1. **S2.4: task type by exception.** `train.py:229-246` still tries
   `AutoModelForCausalLM` and falls back to `SequenceClassification` on *any*
   error. A causal model fed a classification CSV trains on garbage. Should
   come from config.
2. **The evaluator is not merged yet.** `log` and the dashboard both read
   `training_info["evaluation"]["current"]`, and `aethel/evaluation/`, which
   produces it, sits on `feat/evaluation-metrics` with four tests of its own and
   a green CI run. Until it lands, the accuracy numbers on screen come from
   `scripts/seed_demo.py` fixtures, not a real run. See
   `docs/CONTRIBUTING_BRANCHES.md`.
3. **`train.py` is untested** (477 lines). It needs torch, so it sits outside
   the fast suite. Needs its own marked test file.
4. **Nothing anchors the log yet.** The log is honest about this: `/ops` reports
   "never anchored" rather than implying a guarantee. Until the root is pinned
   outside the Hub, the log proves inclusion, not that the operator never
   rewrote the whole thing.
5. **The contract's signature is settled, the contract is not.** §7.4 decided
   one hub-wide log, so `AethelAnchor` anchors a single stream rather than
   `mapping(repoId => Batch[])`. The three indexed event parameters (Solidity
   allows exactly three) go to `batchIndex` (ordering), `root` (reverse lookup
   from a root to its transaction) and `logSize` (which prefix of the log the
   root covers). `repoId` is deliberately not one of them: a leaf is not
   per-repo, so indexing by repo on-chain would advertise a filter the tree
   cannot honour.
6. **No `pull`/`clone`.** Publishing works; fetching a published patch back into
   a fresh repository is not built. The API serves everything needed for it.
7. **No `diff`, no `merge`, no chain, no IPFS mirror.** Planned, none built.
8. **Base object is minimal.** Only `model_id` + `revision_sha`; the plan also
   calls for `config_sha256` and `tokenizer_sha256`.

---

## 10. Next month

### Weeks 1-2 → the review

| Owner | Task |
|---|---|
| Shravan | Merge `aethel/evaluation/` onto `foundation` with its own marked test file, and record one real run |
| Karthik | Merge divergence detection (cosine similarity between consecutive patches + the branch prompt) with tests; then `aethel diff <a> <b>` over the same code path |
| Karthik | Fix S2.4: task type from YAML config |
| Adyaa | Extend the base object with `config_sha256` + `tokenizer_sha256`; `aethel base verify` |
| Sathwik | `AethelAnchor` on Sepolia: one hub-wide log, per §7.4 |
| Sathwik | `aethel anchor`: batch the root, record block number and confirmations |
| Sathwik | Verification page: recompute the root client-side against the on-chain value |
| Sathwik | Pinata mirror: record `ipfs_cid` alongside `blob_sha256` |
| Everyone | Rehearse the demo twice on the actual demo PCs |

Done already: the Hub, the dashboard, `/ops`, `aethel push`, and the
transparency log with inclusion proofs.

### Weeks 3-4 → after the review

- `aethel pull` / `clone` over the same API
- The attack demo, end to end: tamper the Hub's log → `/ops` goes critical →
  verification page fails
- `aethel merge` (task arithmetic, TIES, DARE) + merged-vs-parent accuracy table
- Ed25519 commit signing; dataset fingerprinting

**Order matters:** Hub before chain. On a single-user local tool a blockchain
loses to "why not just a database?", there's no untrusted party. The Hub
*creates* the adversary (an operator who could rewrite history), which is what
makes anchoring load-bearing instead of decorative.

---

## 11. The review

### Demo script

Split honestly, because half of this is runnable on a laptop today and half is
not, and finding that out during the demo is the worst possible time.

**Runnable today, in order, this is the demo we give on 3 September:**

1. Show the corruption bug on the old code (checked-out old commit)
2. Show the test that catches it
3. Show the rebuilt object store, `objects/commits/` vs `objects/<adapter>/`
4. Corrupt one byte → `fsck` names that exact object, exits 1
5. `train` → `commit` → the recorded loss and runtime, and the base pinned by
   revision SHA
6. Branch / checkout time travel, three adapters, instant switching
7. Detached-HEAD commit refused, with the Hub left completely empty
8. Copy `.aethel` elsewhere → full history, no database
9. `push` → Hub dashboard, commit DAG, accuracy per commit
10. `push` again → nothing to upload, root unchanged (a sync, not an event)
11. Delete an object from the Hub → push → it comes back
12. Fetch an inclusion proof; **Recompute in this browser** folds it with its own
    SHA-256 and marks each rung
13. Copy the current root down. Tamper a leaf in `log.jsonl`, reload: the root has
    moved, and the proof issued before the edit no longer verifies against it

Step 13 is the closest thing we have to the thesis today, and its honest caveat is
the argument for the next layer: it only works because *you* wrote the old root
down. Anyone who did not cannot tell the tampered log from the real one, since the
Hub regenerates proofs that are perfectly consistent with whatever it now holds.
Publishing that root where the operator cannot reach it is what anchoring is for.
Say this out loud; it is the clearest way to show the chain is load-bearing
rather than decorative.

**Needs work that is not on `foundation` yet, do not put these in the script
until they land:**

- Real accuracy on screen instead of seeded numbers → the evaluation branch, and
  then a train/validation split on top of it. The evaluator scores the dataset
  file recorded at training time, so its first number would be accuracy on data
  the adapter trained on. Merge it, run it, split the data, quote it, in that
  order
- `aethel diff` between two adapters → unwritten
- Anchor → Sepolia → Etherscan link → the chain layer
- Verify page **PASS** against an on-chain root, then tamper → **FAIL** → the
  chain layer. `/ops`' log-versus-anchored-root row cannot go critical while it
  reads `never anchored`; it has nothing to compare against.

Those last two are the thesis in full, and they are the reason the chain layer is
the next piece of work rather than a later one.

### Framing

> "We audited the prototype, found a silent data-loss defect and a
> cryptographic flaw in our own Merkle tree, rebuilt the core so neither can
> recur, and built the Hub and anchoring layer on top."

That reads as engineering maturity, not catching up.

### Questions you will be asked

**"Why not just use a database?"**
A database can be edited by whoever runs it. An anchored append-only log
cannot be edited without everyone noticing. The Hub operator is the untrusted
party, that's what the chain is for.

**"What does the chain actually prove?"**
That this commit record existed at this time and hasn't been altered since,
even by us. It does **not** prove the weights really came from the claimed
base or the data was what we say. We close that gap in software: dataset
fingerprinting, seed pinning, Ed25519 signing.

**"What stops the Hub from lying about a push?"**
Nothing has to trust it. The hash is in the URL, the Hub recomputes it from the
bytes it received, and the client re-checks the hash the Hub echoes back. The
ref only moves after the Hub re-walks the history itself. And inclusion proofs
are self-contained, verifying one never calls back to the Hub.

**"Why does your log skip duplicates?"**
Because a push is a sync, not an event. Pushing the same branch twice must not
change the root, or the root would depend on how often someone ran a command
rather than on what history exists.

**"You used Pinata (a centralized service) for a decentralized system."**
Content addressing makes the address host-independent. Anyone can re-pin the
same bytes and the CID doesn't change, unlike an HTTP URL that dies with its
server. Pinata is one pin, not the authority. Integrity comes from
`blob_sha256`, verified on every fetch regardless of source.

**"Why is the CID not the same as your SHA-256?"**
IPFS chunks files at 256 KB and builds a DAG; the root CID hashes the DAG
structure, not the bytes. Our adapters are ~0.6 MB, so always multi-block. We
store both: `blob_sha256` for integrity, `ipfs_cid` for location.

**"Isn't this just Git for models?"**
Git has no notion of a frozen base model, no semantic diff or merge for
weights, and no verifiable provenance. We store a ~0.6 MB patch plus a pinned
reference instead of a ~250 MB model.

**"How big is a version?"**
~0.6 MB. distilbert + LoRA r=8 is ~149K parameters (147,456 LoRA + ~1,538
classifier head) at fp32. The base model is never stored.

**"What did you personally write?"**
Answer honestly and specifically. Whoever owns a subsystem should be able to
whiteboard it. If you can't yet, read it and write the design note before the
review, that's the rule in `docs/PROJECT_PLAN.md` §7 and it exists precisely
for this question.

---

## 12. Reading order for the team

Start with the smallest file that teaches the most:

1. `aethel/core/hashing.py` (99), canonical JSON, why key order matters
2. `aethel/core/atomic.py` (162), the four-step write; the best interview answer in the repo
3. `aethel/core/objects.py` (232), where the corruption bug died
4. `tests/test_s1_corruption.py` (167), the bug, as executable proof
5. `aethel/core/aggregator.py` (142), the tree we'll anchor; read the docstring first
6. `aethel/core/refs.py` (244), HEAD, branches, the detached-HEAD guard

Then the publishing layer, in the order the data moves:

7. `aethel/remote/objects.py` (115), a branch becomes a push plan
8. `aethel/commands/push.py` (302), plan, negotiate, upload, move the ref
9. `hub/log.py` (280), the append-only log; the one mutating method
10. `hub/api.py` (557), every endpoint; read `update_ref` closely

Then write `docs/design/<subsystem>.md` in your own words, without looking. If
you can't, re-read. That check is the whole point.
