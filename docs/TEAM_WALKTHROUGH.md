# Aethel — Team Walkthrough

**What changed, why, and what's next.**
Read alongside `docs/PROJECT_PLAN.md`
(the strategy doc); this one is the engineering record.

---

## 0. Thirty-second summary

We audited the prototype, found a bug that silently returned the **wrong
commit**, and rebuilt the storage layer so that bug cannot exist. Along the
way we found a second, separate security flaw in the Merkle tree that would
have broken the blockchain layer before we ever built it.

| | Before | After |
|---|---|---|
| Tests | **0** | **200** |
| Core coverage | 0% | **96%** |
| Linter | none | ruff, clean |
| CI | none | GitHub Actions, 4 Python versions |
| Known data-corruption bugs | 1 (documented, unfixed) | 0 |
| Dead code | ~340 lines | 0 |
| Dependencies pinned | 0 of 13 | all |
| Can the CLI run without PyTorch? | no | yes |
| Is the repo portable? | claimed, false | true, tested |

Net: **692 insertions, 1792 deletions** across tracked files, plus 16 new
files. We deleted more than we added. That is the point.

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
metadata, and it resolved `commit_hash → adapter_hash → folder → commit.json`
— landing on whichever commit wrote last.

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

**Which failure matters most.** Not the message — `parent_hash`. A wrong
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

Deduplication is preserved — identical weights still share one blob. The
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
inclusion proof for a value that was **never a committed leaf** — forging
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
good. `aethel log` now shows accuracy per commit; the metrics pipeline itself
is Shravan's module and is next.

---

## 2. Architecture: before and after

### Before

```
aethel/
  commands/   init, commit, status, train, branch, checkout   <- all logic here
  core/       model.py, aggregator.py                          <- BOTH dead
```

Every command re-declared its own constants (`AETHEL_DIR`, `HEAD`, hash
regex) — five copies. Business logic, file I/O, SQLite and terminal printing
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
    aggregator.py   Merkle transparency log (142)

  commands/   thin CLI. Parses args, calls core, renders output.
    _common.py      error rendering boundary (68)
    init.py (133)  commit.py (77)  branch.py (115)
    checkout.py (104)  log.py (140)  fsck.py (131)
    train.py (478)  <- the only file that needs torch
```

**The rule that drives this:** core raises `AethelError` and never prints;
`commands/_common.py` is the single place where errors become terminal output
and exit codes.

**Why it matters practically:** the whole VCS is testable with no GPU, no
model download, and no network. 200 tests run in **2.2 seconds**. That is why
we can afford to run them on every push.

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

- **blob** — raw bytes of one file. Identical files stored once, ever.
- **tree** — a manifest mapping filename → blob hash. This is why checkout
  restores `adapter_config.json` and `training_info.json` too, not just the
  weights.
- **commit** — metadata: parent, tree, base, message, author, timestamp,
  training info.
- **base** — `(model_id, revision_sha)` plus config/tokenizer hashes. **The
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
could not resolve a commit without `repo.db`, which made the docs' claim —
"copy the folder and you have the version" — **false**.

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
of truth is a stronger claim than managing it — and it made `reindex`
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
   data — you get a correctly-named empty file.
3. **`os.replace`.** Atomic on POSIX *and* Windows, unlike `os.rename`, which
   fails on Windows when the destination exists.
4. **`fsync` the parent directory**, so the rename itself survives power loss.

**Ordering inside a commit also matters:** objects are written first, the ref
is advanced last. Objects are immutable and self-verifying, so an interrupted
commit leaves unreferenced garbage that `fsck` reports and `gc` will collect.
Advancing the ref first would leave a branch pointing at a commit whose
objects don't exist — unrecoverable.

**Locking** uses `O_CREAT | O_EXCL` for ref updates. Without it, two
concurrent commits both read the same branch tip and the second silently
discards the first. A crashed process leaves the lock behind deliberately —
the same trade-off Git makes with `index.lock`.

---

## 5. Every defect fixed

| ID | Defect | Fix |
|---|---|---|
| **S1.1** | Commit metadata overwritten between identical-weight commits | Commits keyed by own hash |
| **S1.2** | No evaluation anywhere | Accuracy surfaced in `log`; metrics module next (Shravan) |
| **S1.3** | Zero tests | 200 tests, 96% core coverage |
| **S2.1** | Documented IMDB quickstart crashed (Arrow vs CSV) | Detects Arrow dirs, explains the conversion |
| **S2.2** | Typo'd dataset path → silently trained on fake text, still committable | Refuses; requires explicit `--allow-stub` |
| **S2.3** | No atomicity in commit | `atomic.py` everywhere |
| **S2.4** | Task type chosen by try/except ordering | *(still open — see §8)* |
| **S2.5** | SQLite as second source of truth | Removed entirely |
| **S3** | `USERNAME` is Windows-only → every Linux commit authored `"User"` | Checks `AETHEL_AUTHOR`, `USER`, `USERNAME`, `LOGNAME` |
| **S3** | `branch.py:86` used bare `startswith` for path containment | Proper parent-directory comparison |
| **S3** | `trust_remote_code=True` on all 5 datasets — arbitrary code execution | Removed |
| **S3** | ~340 lines dead code (`model.py`, `aggregator` unused, `experiment_lora.py`) | Deleted; `aggregator.py` rewritten and now used |
| **S3** | `aethel/core/__init__.py` missing → `find_packages()` never shipped core | Added; explicit package list in `pyproject.toml` |
| **S3** | 13 unpinned deps in a *reproducibility* project | All pinned; `bitsandbytes` → optional `[gpu]` |
| **S3** | `train` wiped the workspace with no confirmation | Commit no longer touches the workspace at all |
| **Docs** | Three docs contradicted the code and each other | Deleted 3 stale status docs; rewrote README |

### New safety behaviours

- **Commits blocked on detached HEAD** (kept from the original — stricter than
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
| LoRA target inference | `train.py:242-264` | Priority list + `nn.Linear` scan makes the trainer architecture-agnostic. |
| Canonical JSON hashing | `commit.py:302` | `sort_keys=True` + compact separators. Deterministic. |
| Merkle odd-node promotion | `aggregator.py` | Avoids CVE-2012-2459. |
| Exception-per-module | all commands | Clean error boundaries; we generalized it. |

The corruption bug is what unreviewed generated code produces, every time, and
six of previous decisions were good enough to keep untouched.

---

## 7. Verify it yourself

```bash
cd /home/Capstone/Aethel

pytest tests/ -q                       # 200 passed in ~2s
pytest tests/ --cov=aethel.core        # 96%
ruff check aethel/ tests/              # All checks passed

# CLI works with no PyTorch installed
python3 -m aethel.main --help
```

Test breakdown:

| File | Tests | Covers |
|---|---|---|
| `test_core_refs.py` | 52 | HEAD, branches, name validation, traversal |
| `test_core_merkle.py` | 29 | Second-preimage, proofs, tamper detection |
| `test_core_objects.py` | 29 | Store, dedup, integrity, trees |
| `test_core_hashing.py` | 24 | Canonical JSON, SHA-256 |
| `test_core_commits.py` | 23 | Creation, lineage, determinism |
| `test_core_atomic.py` | 18 | Crash safety, locking, concurrency |
| `test_core_resolve.py` | 15 | Branch/hash/abbreviation resolution |
| `test_s1_corruption.py` | 10 | The corruption regression, via the real CLI |

**To reproduce the original bug for the demo:** check out a commit from before
the rebuild and run `test_s1_corruption.py` against it.

---

## 8. Still open

Be honest about these — a panel will find them.

1. **S2.4 — task type by exception.** `train.py:221-239` still tries
   `AutoModelForCausalLM` and falls back to `SequenceClassification` on *any*
   error. A causal model fed a classification CSV trains on garbage. Should
   come from config.
2. **No real eval yet.** `log` displays accuracy, but nothing computes it —
   Shravan's `aethel/ml/eval.py` is the missing piece.
3. **`train.py` is untested** (478 lines). It needs torch, so it sits outside
   the fast suite. Needs its own marked test file.
4. **No `diff`, no `merge`, no Hub, no chain.** All planned, none built.
5. **Base object is minimal.** Only `model_id` + `revision_sha`; the plan also
   calls for `config_sha256` and `tokenizer_sha256`.

---

## 9. Next month

### Weeks 1-2 → the review

| Owner | Task |
|---|---|
| Shravan | `aethel/ml/eval.py` — real train/eval split, accuracy + macro-F1 into `training_info` |
| Karthik | `aethel diff <a> <b>` — per-layer ΔW norms, cosine similarity, prediction flips on the eval set |
| Karthik | Fix S2.4: task type from YAML config |
| Aadya | Extend the base object with `config_sha256` + `tokenizer_sha256`; `aethel base verify` |
| Sathwik | Hub skeleton (FastAPI) + dashboard: commit DAG, metrics, patch download |
| Sathwik | Wire `aggregator.py` into the Hub as the append-only log; serve inclusion proofs |
| Sathwik | Pinata account + Sepolia deployer wallet **now**, not demo week |
| Everyone | Rehearse the demo twice on the actual demo PCs |

### Weeks 3-4 → after the review

- `AethelAnchor` Solidity contract, Etherscan-verified on Sepolia
- Verification page, running **client-side** against a public RPC
- The attack demo: tamper the Hub's log → verification fails
- `aethel merge` (task arithmetic, TIES, DARE) + merged-vs-parent accuracy table
- Ed25519 commit signing; dataset fingerprinting
- `/ops` health dashboard

**Order matters:** Hub before chain. On a single-user local tool a blockchain
loses to "why not just a database?" — there's no untrusted party. The Hub
*creates* the adversary (an operator who could rewrite history), which is what
makes anchoring load-bearing instead of decorative.

---

## 10. The review

### Demo script

1. Show the corruption bug on the old code (checked-out old commit)
2. Show the test that catches it
3. Show the rebuilt object store — `objects/commits/` vs `objects/<adapter>/`
4. Corrupt one byte → `fsck` names that exact object, exits 1
5. `train` → **real accuracy numbers**
6. `aethel diff` between two adapters
7. Branch / checkout time travel — three adapters, instant switching
8. Copy `.aethel` elsewhere → full history, no database
9. `push` → Hub dashboard, commit DAG
10. Anchor → Sepolia → Etherscan link
11. Verify page → **PASS**
12. Tamper the Hub's log → verify page → **FAIL**

Steps 11-12 are the thesis. Everything else is supporting evidence.

### Framing

> "We audited the prototype, found a silent data-loss defect and a
> cryptographic flaw in our own Merkle tree, rebuilt the core so neither can
> recur, and built the Hub and anchoring layer on top."

That reads as engineering maturity, not catching up.

### Questions you will be asked

**"Why not just use a database?"**
A database can be edited by whoever runs it. An anchored append-only log
cannot be edited without everyone noticing. The Hub operator is the untrusted
party — that's what the chain is for.

**"What does the chain actually prove?"**
That this commit record existed at this time and hasn't been altered since —
even by us. It does **not** prove the weights really came from the claimed
base or the data was what we say. We close that gap in software: dataset
fingerprinting, seed pinning, Ed25519 signing.

**"You used Pinata — a centralized service — for a decentralized system."**
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
review — that's the rule in `docs/PROJECT_PLAN.md` §7 and it exists precisely
for this question.

---

## 11. Reading order for the team

Start with the smallest file that teaches the most:

1. `aethel/core/hashing.py` (99) — canonical JSON, why key order matters
2. `aethel/core/atomic.py` (162) — the four-step write; the best interview answer in the repo
3. `aethel/core/objects.py` (232) — where the corruption bug died
4. `tests/test_s1_corruption.py` (167) — the bug, as executable proof
5. `aethel/core/aggregator.py` (142) — the log we'll anchor; read the docstring first
6. `aethel/core/refs.py` (244) — HEAD, branches, the detached-HEAD guard

Then write `docs/design/<subsystem>.md` in your own words, without looking. If
you can't, re-read. That check is the whole point.
