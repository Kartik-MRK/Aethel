# Aethel: Project Plan

**Status:** M0 delivered. L0 through L4 are built and tested; L2's evaluation half sits on a
branch and L5 exists only in §5.2 of this document.
**Audience:** Sathwik, Karthik, Adyaa, Shravan
**Next review:** 3 September 2026
**Owner of this document:** Sathwik, keep it current; a stale plan is worse than none

> §2, §3 and §4 are the audit of the **pre-rebuild prototype**. Every `file.py:line`
> reference in them points at that code, not at the tree you have checked out; the rebuilt
> `aethel/commands/commit.py` is 77 lines and shares no line numbering with the original.

### Contents

| § | Section | Read if |
|---|---|---|
| 1 | What we are building | everyone, first |
| 2 | Where we stand today (honest audit) | everyone |
| 3 | Verified defects | everyone: **S1.1 is the important one** |
| 4 | What is good and stays | everyone |
| 5 | Design decisions and rationale | everyone: these are the viva answers |
| 6 | Architecture | Sathwik, Karthik |
| 7 | Roles, learning protocol, definition of done | everyone |
| 8 | Milestones + demo script | everyone |
| 9 | Backend hosting | Sathwik |
| 10 | File actions | Karthik |
| 11 | Verification checklist | everyone |
| 12 | Open items | Sathwik |
| 13 | Risks | everyone |

**If you read only one thing:** §3 (S1.1) explains the bug that forces the rebuild, and §5.2
explains why the blockchain layer is defensible rather than decorative. Those two are what the
panel will probe.

---

## 1. What we are building

**Aethel is version control and provenance for LoRA adapters.**

One frozen base model is fine-tuned into small LoRA patches. Each patch is a *version*, managed
with Git-like commands. Patches are published to a Hub with a dashboard, mirrored to IPFS, and the
history is anchored to a public blockchain so that **even the person running the Hub cannot rewrite
a model's recorded history without it being detectable.**

```
base model      NEVER stored. Referenced by (model_id, revision_sha). Anyone re-downloads it from
                Hugging Face themselves.
LoRA patch      the versioned artifact (~0.6 MB). init / train / commit / branch / checkout / push
Hub + dashboard serves patches + metadata; keeps an append-only Merkle log of history
IPFS (Pinata)   mirror of patch blobs. An address, never the source of truth
Sepolia         anchors the Merkle root, so history cannot be silently edited
verify page     recompute the root from an inclusion proof, compare to the chain -> PASS / FAIL
```

**One-line thesis (use this wording):**

> Aethel is a version control and provenance system for parameter-efficient model adapters. It
> gives Git-like history, semantic diff and merge for LoRA patches, and cryptographically
> verifiable lineage, anchored to a public ledger so that even the registry operator cannot
> rewrite a model's recorded history.

---

## 2. Where we stand today (honest audit)

The existing prototype works on the happy path: `init → train → commit → branch → checkout → log`.
It is roughly 1,050 lines of Python and 1,900 lines of markdown, written with AI assistance and
never independently reviewed or tested.

### Scores

| | As a typical college capstone | As a portfolio / resume artifact |
|---|---|---|
| **Overall** | **6.0 / 10** | **4.0 / 10** |

| Dimension | Score | Reason |
|---|---|---|
| Code style / readability | 6.5 | Genuinely tidy: typed helpers, docstrings, custom exceptions |
| Architecture | 4.0 | Corruption bug is structural; SQLite is a second source of truth |
| Correctness / robustness | 4.0 | Documented quickstart crashes; no atomicity anywhere |
| **ML rigor** | **2.5** | **No eval split, no accuracy, no seeds, silent fake-data path** |
| **Testing** | **0.0** | **Zero tests exist** |
| Security | 4.0 | No signing, no verify-on-read, `trust_remote_code=True`, deps unpinned |
| Documentation | 4.0 | High volume, self-contradictory, stale |
| Novelty / thesis | 4.0 | "Local git for LoRA" is real but thin; "decentralized" is unbacked today |
| Git hygiene | 3.0 | 5 commits, messages like "made system changes in all of the features" |

**Summary:** a tidy surface over a hollow core. This is the normal outcome of generated code that
nothing executes, not a criticism of anyone. The plan below fixes it.

---

## 3. Verified defects

Every item below was checked against the actual code, not assumed.

### Severity 1: must fix before anything is built on top

**S1.1: Silent data corruption.** The prototype's `commit.py:197-221` returned early when an
adapter folder already exists ("deduplicating"), then line 306 writes `commit.json` into that
shared folder anyway. Two commits with identical weights both get rows in SQLite, but only the
**second** `commit.json` survives. `checkout.py` resolves `commit_hash → adapter_hash → folder →
commit.json`, so checking out commit A silently returns **B's** message, author and parent.

This is already written down as a "known limitation" in `PROJECT_STATUS_REPORT.md` and was never
fixed. A version control system that returns a different version than the one requested is not a
version control system. This is the single most important thing in this document.

**S1.2: Zero evaluation.** No eval split, no `compute_metrics`. The only recorded metrics are
Hugging Face `Trainer` training loss and runtime. We cannot currently state whether any model we
version is any good, which is a serious problem for an ML project.

**S1.3: Zero tests.** Confirmed: there is no test file anywhere in the repository.

### Severity 2: breaks real usage or core claims

| ID | Defect | Location |
|---|---|---|
| S2.1 | Documented IMDB quickstart cannot run: `dataset_download.py` writes Arrow, trainer accepts only CSV | `dataset_download.py`, `train.py:275-289` |
| S2.2 | Silent fake data: a typo'd dataset path prints one yellow line, then trains on `f"sample training record {index}"` and still produces a committable adapter with plausible metrics | `train.py:306` |
| S2.3 | No atomicity: `commit()` performs 5 mutating steps with no transaction, no `fsync`, no rollback | `commit.py:276-313` |
| S2.4 | Task type chosen by exception handling: tries CausalLM, falls back to SeqCls on *any* error. A causal model + classification CSV trains on garbage | `train.py:221-239` |
| S2.5 | SQLite is a second source of truth; checkout cannot resolve a commit without `repo.db`, so the documented "copy the folder and you have the version" claim is false | `checkout.py:51-70` |

### Severity 3: correctness, portability, hygiene

- **`os.getenv("USERNAME")`** (`init.py:117,123`), a Windows variable. On Linux every commit is
  authored by literally `"User"`.
- **Destructive `train`**: `prepare_workspace()` (`train.py:135-141`) `rmtree`s the workspace with
  no confirmation, destroying uncommitted adapters.
- **Inconsistent path-escape check**: `branch.py:86` uses `startswith()` without a separator guard;
  `commit.py:97` does it correctly. Two different security postures in one codebase.
- **~340 lines of dead code**: nothing imports `core/model.py` (`ModelManager`),
  `core/aggregator.py` (`MerkleTree`), or `experiment_lora.py`. Verified by grep. Also
  `aethel/core/__init__.py` does not exist, so `find_packages()` never ships `core/` on a real install.
- **13 unpinned dependencies** in a project whose thesis is reproducibility. `bitsandbytes` is
  CUDA-only and breaks CPU installs. `train.py:373` uses `processing_class=` (requires
  transformers ≥ 4.46) with no pin, so it silently breaks on other versions.
- **`trust_remote_code=True`** on all five datasets in `setup_demo_data.py`, executes arbitrary
  code downloaded from the Hub.

### Documentation defects

- `AETHEL_SYSTEM_EXPLAINED.md` describes a folder-based object store, then asserts under "Key Design
  Decisions" that the store is *flat files, not subdirectories*, describing a refactor that was
  reverted. The document contradicts itself.
- It also claims the workspace is "cleared after every commit" while `commit.py:313` explicitly
  restores it.
- `README.md` still says `commit` performs training by perturbing weights, two refactors out of
  date, and documents a PowerShell-only setup on a Linux machine.

---

## 4. What is good and stays

These are real engineering decisions. We keep them, port them verbatim, and give credit.

| What | Where | Why it is good |
|---|---|---|
| **HF revision pinning** | `init.py:37-47` | Resolves and stores the immutable model SHA; `train.py` passes `revision=` on every load. Correct reproducibility practice that a lot of production ML gets wrong. |
| **Detached-HEAD commit guard** | `commit.py:315-329` | Blocks the commit and prints recovery steps. Git itself only warns. Genuinely thoughtful. |
| **LoRA target inference** | `train.py:242-264` | Priority list + `nn.Linear` scan makes the trainer architecture-agnostic. |
| **Canonical JSON hashing** | `commit.py:302` | `sort_keys=True` + compact separators. Correct and deterministic. |
| **Exception-per-module pattern** | all commands | Clean error boundaries. |
| **`DEMO_GUIDE.md` structure** | n/a | Well built; folded into the demo walkthrough in `README.md`. |
| **`core/aggregator.py`** | n/a | Dead today, but it becomes the Hub's transparency log. Keep and test it. |

---

## 5. Design decisions and rationale

### 5.1 We do not store base models, and this is a headline feature

Only `(model_id, revision_sha)` plus config/tokenizer hashes are recorded. Anyone reproducing a
result downloads the base from Hugging Face themselves and applies our patch.

Why this is strong:
- A version is ~0.6 MB instead of ~250 MB, so an entire model family's history fits in megabytes.
- No licence or redistribution question about base weights.
- Reproduction is exact, because the revision SHA is immutable.

This should be stated as a design property in the report, not buried in a metadata field.

### 5.2 The Hub is what makes the blockchain defensible

The question every panel asks is *"why not just use a database?"* Projects die on it.

A chain is only justified when **you cannot trust whoever runs the database.** On a single-user
local tool there is no such party, so a chain there is decoration. But a hosted Hub **has an
operator**, and that operator can rewrite published lineage, swap a training-data record, forge
authorship, hide that a model descends from a poisoned parent. Anchoring a Merkle root of the log
means the Hub cannot alter history without every user detecting it.

**Answer to give:** *a database can be edited by whoever runs it; an anchored append-only log cannot
be edited without everyone noticing.*

Build order follows from this: **Hub first, then anchoring.** The Hub supplies the adversary the
chain needs.

### 5.3 What the chain proves: say this precisely

**It proves:** this commit record existed at this time and has not been altered or removed since,
even by the Hub operator.

**It does not prove:** that the weights really came from the claimed base, or that the training data
was really what the record says. A liar can publish an honest-looking record; the chain only stops
them changing it *later*.

Overclaiming here is the fastest way to lose marks. We close the gap in software instead:

1. **Dataset fingerprinting**: canonical row order → SHA256, stored with row count. "Trained on X"
   becomes checkable rather than asserted.
2. **Reproducibility record**: base ref hash, seed, library versions, hyperparams. With a fixed
   seed, a commit becomes re-derivable.
3. **Ed25519 commit signing**: authorship becomes cryptographic instead of a string field (which is
   broken today anyway, see S3).
4. **`aethel diff`**: weight-level evidence that a child descends from its claimed parent,
   independent of what the metadata asserts.

### 5.4 IPFS: Pinata, and CID determinism is deliberately irrelevant

**Decision: use Pinata (or an equivalent pinning service). No local IPFS daemon.**

Setup cost is not the reason, `ipfs init && ipfs daemon` is trivial. The reason is **NAT
traversal**: a kubo node behind a campus firewall can be running perfectly and still not be dialable
from outside. Uptime is not the issue; reachability is.

**The important design rule:**

- **`blob_sha256` is the integrity hash.** After fetching a blob from *anywhere*, Hub, IPFS
  gateway, or disk; we hash the bytes and compare. Mismatch = reject.
- **`ipfs_cid` is only an address.** We record whatever the service returns and never derive trust
  from it.
- **Nothing depends on recomputing the CID locally.**

Why that last rule matters: **a CID is not the SHA256 of your file.** IPFS splits files into 256 KB
chunks and builds a DAG; the root CID hashes the *DAG structure*. Our patches are ~0.6 MB for
distilbert at rank 8 (≈149K params × 4 bytes: LoRA 147,456 + classifier head ≈1,538), so they are
above the chunk boundary and always multi-block. Different tools use different chunker settings, so
a local recompute can legitimately disagree with Pinata's. Verifying **content** is robust;
verifying an **address** is not.

**Expected objection:** *"you used a centralized service to build a decentralized system."*
**Answer:** content addressing makes the address independent of the host. Anyone can re-pin the same
bytes and the CID does not change, unlike an HTTP URL which dies with its server. Pinata is one pin,
not the authority.

**Worth 30 seconds of demo:** pin one adapter, then fetch that same CID through two independent
public gateways. Same CID, same bytes, different providers, that *shows* location independence
rather than claiming it.

### 5.5 Chain: Sepolia for the demo, Anvil for tests

**Sepolia is the demo target.** A public chain with an Etherscan link is materially more convincing
than a local node, and faucet access is arranged.

**Anvil is still installed: for tests, not demos.** Local chain setup is not heavy; Foundry is one
command and Anvil starts instantly with ten pre-funded accounts:

```bash
curl -L https://foundry.paradigm.xyz | bash && foundryup   # forge + anvil + cast
anvil                                                       # instant, 10 funded accounts
```

We need it because **contract tests cannot run against a testnet**: ~40 tests against 12-second
blocks, faucet drain, non-deterministic state, and CI has no wallet. `forge test` runs against an
in-process EVM in milliseconds.

| Path | Backend | Why |
|---|---|---|
| `forge test` / CI | in-process EVM + Anvil | fast, free, deterministic, no wallet needed |
| Demo + verification page | **Sepolia** | public, explorer-linkable, real |

Same contract, same bytecode. Anvil doubles as a 30-second recovery if Sepolia's RPC flakes
mid-presentation.

**Operational care:** RPC URL, chain ID and contract address go in config, testnets do get sunset
(Ropsten, Rinkeby, Kovan and Goerli are all dead). Deployer key in `.env`, never committed, throwaway
wallet only. Use a keyed RPC provider, not a public endpoint, because public ones rate-limit exactly
when you are presenting.

---

## 6. Architecture

```
L0  aethel/core/     objects, refs, hashing, atomic IO, base refs   [pure Python, no ML/network]
L1  aethel/vcs/      init, commit, branch, checkout, log, fsck, gc, reindex
L2  aethel/ml/       train, eval, diff, merge                       [needs torch]
L3  aethel/remote/   push/pull; backends: local | http | ipfs
L4  hub/             FastAPI registry + dashboard + append-only Merkle log
L5  chain/           AethelAnchor contract + independent verifier
```

Two of those names did not survive the build, and the tree is the authority, not this block. L1
landed as `aethel/commands/` (one module per CLI verb, which is how Typer wants it), and L2's
training half sits in `aethel/commands/train.py` with its evaluation half in `aethel/evaluation/`.
`gc` and `reindex` were never written, and the second was dropped on purpose: see 6.1.

Every layer is demoable on its own, and nothing above L2 is required for a working, gradeable
project. That is deliberate: if the chain work slips, the core still stands.

### 6.1 Object model: this is the corruption fix

```
.aethel/
  HEAD, config.json
  refs/heads/<branch>              -> commit hash
  objects/blobs/<ab>/<sha256>      -> patch weight files (immutable, deduped)
  objects/trees/<ab>/<sha256>      -> manifest: filename -> blob hash
  objects/commits/<ab>/<sha256>    -> commit JSON, keyed by COMMIT hash
  objects/bases/<ab>/<sha256>      -> base-model reference objects
```

The plan originally had an `index.db` here, a derived SQLite cache rebuilt by `aethel reindex`. It
was never built, and that turned out to be the better answer: with no cache there is no second
source of truth to fall out of step, no reindex command to remember, and the "copy the folder and
you have the version" claim is true by construction rather than by discipline. A test asserts that
no database file appears under `.aethel/` so one cannot creep back in.

Three rules make S1.1 impossible by construction:

1. **Commits are keyed by commit hash, never by patch hash.** Identical weights share a blob and
   still keep separate commits. The bug cannot recur, not "is guarded against", but has no
   representable form.
2. **No second source of truth.** Every read path resolves from the object store alone. The plan
   allowed a rebuildable cache alongside it; the build did not add one, which is strictly safer.
3. **All writes atomic**: write to tmp → `fsync` → `os.replace()`. Refs updated last, under a lock
   file.

### 6.2 Base-model reference object

Because base weights are never stored, this object *is* the reproducibility contract:

```json
{
  "model_id": "distilbert-base-uncased",
  "revision_sha": "<immutable HF commit sha>",
  "architecture": "DistilBertForSequenceClassification",
  "config_sha256": "<hash of config.json as fetched>",
  "tokenizer_sha256": "<hash of tokenizer files>",
  "num_labels": 2,
  "source": "huggingface"
}
```

Commits reference a base by hash. Two commands fall out of it:

- **`aethel base verify`**: re-fetch the pinned revision, re-hash config/tokenizer, confirm the
  local base matches what the commit claims. Catches a silently-changed base.
- **`aethel reproduce <commit>`**: download base @ revision, apply the patch, re-run eval, compare
  to recorded metrics. **This is our strongest demo:** a third party reproduces a published result
  from a 0.6 MB download plus a pinned reference.

### 6.3 Data flow

```
aethel train      base ref (model_id + revision_sha, NOT weights) + dataset -> patch in workspace
                  records: base ref hash, dataset_sha256, seed, hyperparams, eval metrics

aethel commit     patch bytes -> blobs/<sha256>        (immutable, deduped)
                  manifest    -> trees/<sha256>
                  metadata    -> commits/<sha256>      (keyed by COMMIT hash)
                  refs/heads/<branch> -> commit hash   (atomic: tmp -> fsync -> rename)

aethel push       blobs + commits -> Hub (authoritative remote)
                                  -> Pinata mirror; ipfs_cid recorded next to blob_sha256
                  Hub appends the commit hash as a leaf in its append-only Merkle log

anchor (Hub job)  Merkle root -> anchor(root, logSize) on Sepolia -> event + block number
                  BATCHED (per N commits or per interval), never one tx per commit

verify (page)     commit hash + inclusion proof + on-chain root
                  -> recompute root client-side -> compare -> PASS / FAIL
                  runs against a public RPC; does not trust the Hub

aethel reproduce  download base @ revision_sha from HF + apply patch + re-run eval
                  -> compare against recorded metrics
```

Every fetch is verified against `blob_sha256` regardless of source. That single rule is what lets
IPFS, the Hub, and local disk all be untrusted transports.

### 6.4 Contract design: sorted, filterable, append-only

Separation of transactions belongs in the contract, via **indexed event parameters**. Indexed args
become log topics, which makes them filterable through `eth_getLogs` and searchable on explorers.

The log this anchors is **one tree for the whole Hub**, not one per repository, the reasoning is in
`TEAM_WALKTHROUGH.md` §7.4, and the short version is that a single tree makes deleting an entire
repository detectable, while separate trees make it invisible. So the contract anchors a single
append-only stream of batches:

```solidity
contract AethelAnchor {
    struct Batch { bytes32 root; uint64 logSize; uint64 timestamp; address submitter; }

    Batch[] private _batches;                       // append-only, one hub-wide stream
    mapping(bytes32 => bool) public rootSeen;       // duplicate-root guard
    mapping(address => bool) public canAnchor;      // allowlist

    event Anchored(
        uint256 indexed batchIndex,  // topic1 - ordering
        bytes32 indexed root,        // topic2 - reverse lookup: root -> transaction
        uint64  indexed logSize,     // topic3 - which prefix of the log this covers
        address submitter,
        uint64  timestamp
    );

    function anchor(bytes32 root, uint64 logSize) external;
    function batchCount() external view returns (uint256);
    function batchAt(uint256 i) external view returns (Batch memory);
    function latest() external view returns (Batch memory);
}
```

Details that matter:

- **Only 3 indexed params are allowed** (4 topics including the event signature). They go to
  `batchIndex`, `root` and `logSize`, ordering, reverse lookup, and coverage. `repoId` is
  deliberately absent: a leaf is not per-repo, so indexing by repo on-chain would advertise a filter
  the tree cannot honour. Per-repo *views* live on the dashboard, where repo membership is Hub
  metadata rather than a cryptographic claim.
- **`logSize` must be monotonic.** It is the count of leaves the root covers, so a new anchor whose
  `logSize` is not greater than the last one is either a replay or an attempt to anchor a shorter
  history, the on-chain shape of "the operator truncated the log". Rejecting it in the contract
  means the check cannot be skipped by the client that submits.
- **Access control is not optional.** Without `canAnchor`, anyone can spam anchors into our contract
  and the dashboard renders attacker rows. This is the most commonly forgotten piece.
- **Append-only by construction**: no update or delete function exists, and `anchor` rejects a
  duplicate root or a non-monotonic `logSize`. The immutability claim holds because there is no code
  path to break it, not because we chose not to call one.
- **Verify the source on Etherscan.** Unverified contracts render events as raw hex, which looks
  broken on a projector. One-time step.
- **Respect reorgs.** Sepolia reorgs happen. Store the block number and treat an anchor as final only
  after a few confirmations; show `pending → confirmed` rather than claiming instant finality.
- Cost is two storage writes plus an event, trivial.

### 6.5 Ops dashboard (`/ops`)

Four systems (Hub, object store, Pinata, Sepolia) fail independently. Without this we end up
debugging live in front of the panel.

`GET /health` returns per-subsystem status; `/ops` renders it as a green/amber/red board:

| Check | Green when | Catches |
|---|---|---|
| Database | connects, migration version current | stale schema |
| Object store | path writable, `fsck` sample passes | disk / permission issues |
| Pinata | API auth valid, a known CID resolves | expired key, quota exhausted |
| IPFS gateway | known CID fetches, bytes hash to `blob_sha256` | gateway timeout, wrong content |
| Chain RPC | `eth_chainId` matches configured chain | wrong network, rate limit |
| Anchor state | last root, batch index, block, confirmations | anchors silently not landing |
| **Merkle log** | **recomputed root == last anchored root** | **log tampering or drift** |

That last row is the most valuable thing on the page, a live self-audit of the exact property the
whole project claims. Put it front and centre.

Supporting pieces: structured JSON logs with a request ID per call; every subsystem call wrapped in a
timeout so one dead dependency cannot hang the page; `/version` exposing git SHA and contract address
so we always know what is deployed. **Degraded dependencies render as amber rows, never as a 500**,
the ops page must survive the failures it reports.

**One tier still missing, and it lands with the Pinata and chain checks.** The board is
green/amber/red today, and amber currently carries two unrelated meanings: *not built yet* (Chain and
IPFS mirror are unconfigured during the foundation phase) and *configured and failing* (a probe threw).
Those must not share a colour. Once the Pinata key and the Sepolia contract are real, an expired key is
a fault, not a note, and with four benign amber rows already on the board, a fifth amber row that
actually matters disappears into them. So the chain phase adds a fourth status between them, and
`/api/v1/health` starts distinguishing "unconfigured" from "unhealthy" rather than collapsing both to
`warning`.

---

## 7. Roles

Sathwik and Karthik carry the project. Adyaa and Shravan own **real but decoupled** modules, not
paper tasks, because a panel *will* ask them a direct question and a small honest answer beats
silence. Both modules were missing from the codebase when this was written. The table records
the assignment as agreed; § "Where the work actually landed" below records what happened to it.

| Person | Owns | Why this assignment |
|---|---|---|
| **Sathwik** | L5 chain + L4 Merkle log, **co-owns L0 object model** | The log is the bridge between core and chain. You cannot defend an anchoring scheme without explaining exactly what is hashed into the leaves. |
| **Karthik** | L0/L1 core rebuild + L2 train / diff / merge | Knows the existing code; the rebuild is the highest-value thing to own. |
| **Shravan** | evaluation: eval split, metrics, `compute_metrics` | Fully isolated, genuinely needed, absent entirely when this was written (defect S1.2). Landed as `aethel/evaluation/` on `feat/evaluation-metrics`. |
| **Adyaa** | Base-model registry + encoder-model support (config-driven) | Clean seam; directly serves "more base models later". |

### Where the work actually landed

The table above is the assignment as agreed. This is the split as built, measured by the tests each
person owns (715 collected cases in total):

| Person | Owns in the tree | Tests |
|---|---|---:|
| **Sathwik** | The Hub's server contract and the transparency machinery: `hub/{api,storage,log,security,app,config}.py`, `hub/static/verify.js`, the commit and ops templates, plus `aethel push`, the Merkle log and the L5 chain design | 249 |
| **Adyaa G B** | The rendered dashboard and the design system: `hub/views.py`, `hub/static/hub.css`, the base/index/repo/macro templates, `scripts/build_fonts.py`, plus `aethel init` and the commit-record schema | 187 |
| **Karthik** | L0 and L1: `aethel/core/` apart from the aggregator and commit builder, and `aethel/commands/{branch,checkout,log,fsck,_common}.py`, plus divergence detection and merge | 186 |
| **Shravan** | The error contract and the generated API reference: `hub/{errors,apidocs,prose}.py`, `api.html`, `error.html`, plus `aethel commit`, `aethel train` and the evaluation package | 93 |

Two items in that table are not on `foundation` yet. Evaluation is written and green on
`feat/evaluation-metrics` and waiting to merge; divergence detection is designed and not yet
written. `docs/CONTRIBUTING_BRANCHES.md` §8 has the detail for both.

### Learning protocol

AI writes the code. That is fine, but per subsystem, the owner must:

1. **Read it line by line.**
2. **Write the tests that prove it**: this is the forcing function. You cannot write a real test for
   code you do not understand.
3. **Write `docs/design/<name>.md` in your own words**, without looking. If you cannot, re-read.

### Definition of done: the real guard against hallucinated code

Better prompting helps, but it is not the safeguard. **The safeguard is that wrong code fails a
test.** The corruption bug survived being written into a status document as a known limitation
precisely because nothing executed the code. No module is done until:

1. Tests exist and **fail if the behaviour is removed**, including one test per known failure mode.
2. CI is green.
3. The owner wrote `docs/design/<name>.md` in their own words, without looking.
4. **Docs match behaviour**: no claim in any `.md` that the code does not actually do.

Rule 4 exists because our current docs assert a flat object store that was reverted, and a workspace
that is cleared when `commit.py:313` restores it. Doc drift is how a project starts lying about
itself.

---

## 8. Milestones

### M0: two weeks, to the review

**Hard gate: week 1 must be green before week 2 starts.** No Hub work on a broken core.

**Week 1: foundation** (Karthik lead; Sathwik on objects + log)

| # | Task | Owner |
|---|---|---|
| 1 | Rebuild object layer: blob / tree / commit / base split, atomic writes, ref locking | Karthik + Sathwik |
| 2 | Migrate commands onto it; remove 5× duplicated constants; add `core/__init__.py` | Karthik |
| 3 | `pytest` suite 60+, including the **corruption regression test** | Karthik + Sathwik |
| 4 | `aethel fsck` and `aethel reindex` (`reindex` dropped, see 6.1) | Sathwik |
| 5 | **Real evaluation**: eval split, accuracy + macro-F1 into commit metadata | Shravan |
| 6 | S3 fixes: `USER` fallback, CSV/Arrow mismatch, pin all deps, `--allow-stub` flag | Karthik |
| 7 | Base-model reference object + `aethel base verify` | Adyaa |
| 8 | CI: pytest + ruff on push | Sathwik |
| 9 | Create Pinata account + throwaway Sepolia deployer wallet | Sathwik |

**Week 2: visible layer** (Sathwik lead)

| # | Task | Owner |
|---|---|---|
| 10 | `aethel diff`: per-layer ΔW norms, cosine similarity, prediction flips | Karthik |
| 11 | **FastAPI Hub + dashboard**: commit DAG, metrics per commit, patch download, base ref | Sathwik + Adyaa |
| 12 | `aethel push` → Hub; Pinata mirror; record `ipfs_cid` + `blob_sha256` | Sathwik |
| 13 | **Append-only Merkle log** in the Hub (`aggregator.py` finally earns its place) | Sathwik |
| 14 | **`AethelAnchor` on Sepolia**, Etherscan-verified, `aethel anchor`, verify page PASS → tamper → FAIL | Sathwik |
| 15 | **Ops dashboard** `/ops` | Sathwik |
| 16 | Rewrite README + `docs/`; delete stale docs | Karthik |
| 17 | **Rehearse the demo end-to-end twice, on the actual demo PCs** | everyone |

### Demo script for the review

1. Reproduce the corruption bug on the old code
2. Show the test that catches it
3. Show the rebuilt object store
4. Corrupt a byte → `fsck` names exactly that object
5. Train → **real accuracy and F1 numbers**
6. `aethel diff` between two adapters
7. Branch / checkout time travel
8. `aethel push` → Hub dashboard, commit DAG
9. Same CID fetched through two independent IPFS gateways
10. `aethel anchor` → Sepolia → Etherscan link
11. Verify page → **PASS**
12. Tamper a leaf in the Hub's log → verify page → **FAIL**

Steps 11 and 12 are the entire thesis in two screens. Everything else is supporting evidence.

**Where this actually stands, as of the review.** Steps 1-4, 7 and 8 run today. Step 5 trains and
records loss and runtime; the accuracy and F1 half sits on `feat/evaluation-metrics`, green on CI
and not yet merged, so the number on screen is fixture data. Steps 6, 9, 10 and 11 are not built: `aethel diff`
is designed and unwritten, there is no Pinata mirror, and nothing is anchored. Step 12 has a
runnable form (tamper a leaf and the root moves, so a proof issued earlier stops verifying), but
only for someone who wrote the old root down first, because the Hub reissues proofs consistent with
whatever it now holds. Removing that "wrote it down first" caveat is precisely what step 10 buys.

`docs/TEAM_WALKTHROUGH.md` §11 carries the script we actually give on 3 September, in the order we
give it. This list is the plan; that one is the demo.

**Framing for the review:** *"We audited the prototype, found a silent data-loss defect, rebuilt the
core so it cannot recur, and built the Hub and anchoring layer on top."* That reads as engineering
maturity rather than catching up, which is exactly what it needs to do.

### M1: provenance depth (weeks 3-6)

`aethel merge` (task arithmetic, TIES, DARE) with a merged-vs-parent accuracy table. Dataset
fingerprinting. Ed25519 signing + `aethel verify`. `aethel reproduce`. Reproducibility study: same
seed + revision → identical patch SHA256 across both RTX 3050 machines. `aethel gc`.

### M2: Hub hardening (weeks 7-11)

Auth, multi-user, multiple base models, inclusion proofs served per commit, pagination, real
deployment.

### M3: chain & Hub hardening (weeks 12-15)

Batched anchoring on a schedule, per-repo dashboard filtering over the hub-wide log, reorg-aware
confirmation states, gas/latency measurements for the report.

### M4: write-up (weeks 16-18)

Results tables, ablations, threat model, limitations, polished demo.

**18 weeks ≈ 4.5 months against ~6 available.** The slack is intentional.

---

## 9. Backend hosting

Host-agnostic by design, no hard-coded hosts. Config via `AETHEL_HUB_URL`; the server binds
`0.0.0.0` with a configurable port. All deployment options then work and the decision can wait.

One command per platform, identical behaviour, both idempotent and safe to re-run:

```
scripts/dev.sh      # Linux / Parrot: venv, install, migrate, uvicorn --host 0.0.0.0
scripts/dev.ps1     # Windows: identical steps
```

**Test `dev.ps1` on the actual Windows demo PCs during week 2, not on demo day.** That script is the
single most likely thing to fail in front of the panel.

Demo-day fallbacks, rehearsed in order:

1. Same LAN by IP
2. Phone hotspot, campus wifi often uses AP/client isolation, which silently blocks PC-to-PC traffic
3. `cloudflared` tunnel
4. Run the Hub on the demo PC itself

---

## 10. File actions

Status: **done**, all of the following has been carried out. Every deleted
file remains recoverable from git history at `4437e8d`.

**Deleted:** `experiment_lora.py`, the prototype's `core/model.py`, `demo_flow.ps1`,
`setup_env.ps1`, `PROJECT_STATUS_REPORT.md`, both `PROJECT_STATUS_UPDATE_*.md`,
`dataset_download.py` (wrote Arrow the trainer cannot read), `imdb_train.yaml`
(pointed at that Arrow directory).

**Merged into `docs/SYSTEM_REFERENCE.md`, then deleted:** `BRANCHING_README.md`,
`CHECKOUT_README.md`, `AETHEL_SYSTEM_EXPLAINED.md`, `DEMO_GUIDE.md`. Folded
into one reference rather than four files, since all four described the
pre-rebuild storage model and two contradicted each other.

**Rewritten:** `README.md`. `setup.py` and `requirements.txt` → `pyproject.toml`,
with every version pinned and `bitsandbytes` moved to the `[ml]` extra.

**New:** `aethel/core/{__init__,objects,refs,repo,hashing,atomic,base}.py`,
`aethel/ml/{eval,diff,merge}.py`, `aethel/remote/`, `hub/`, `chain/`, `tests/`,
`scripts/dev.{sh,ps1}`, `.github/workflows/ci.yml`, `docs/design/`. Of that list, `chain/`,
`diff` and `merge` are still unwritten, and evaluation landed as `aethel/evaluation/` rather than
under `aethel/ml/`.

**Port verbatim, do not rewrite:** `init.py:37-47` (revision pinning), `train.py:242-264` (target
inference), `commit.py:302` (canonical hashing), `commit.py:315-329` (detached-HEAD guard).

**Preserve:** `aethel/core/aggregator.py`, dead today, becomes the week-2 transparency log. Add
tests now.

---

## 11. Verification checklist

- `pytest`: green including the corruption regression test; `--cov` ≥ 80% on `core/`. The floor
  was 60 tests when this was written; the suite currently stands at 715 with 96% coverage on
  `aethel.core`, and CI enforces the 80% floor rather than the count
- **Corruption regression:** commit identical weights twice with different messages; assert both
  commit objects survive and each checkout returns *its own* metadata
- **Atomicity:** fault-inject a kill at each commit step; repo stays valid and `fsck` passes
- **Portability:** no database file exists under `.aethel/` and history still walks from the object
  store alone (`tests/test_core_commits.py`)
- **Integrity:** flip one byte in a blob; `fsck` names exactly that object
- **Base pinning:** `aethel base verify` fails when the pinned revision is changed
- **Reproduce:** `aethel reproduce <commit>` on a machine with no cached base matches recorded metrics
  within tolerance
- **IPFS:** the same CID resolves via two independent gateways; bytes hash to `blob_sha256`
- **Chain:** `forge test` green against in-process EVM; contract deployed and Etherscan-verified on
  Sepolia; verify page PASS against the live contract; tamper a leaf → FAIL
- **Contract safety:** a non-allowlisted address cannot anchor; a duplicate root reverts; no code path
  mutates or deletes a stored batch
- **Ops:** `/ops` all green; revoke Pinata's key → that row goes amber and the page still renders; the
  Merkle-log row flips red when a leaf is tampered
- **Cross-platform:** `scripts/dev.ps1` on Windows and `dev.sh` on Parrot each bring the Hub up in one
  command
- **End-to-end on CPU:** `init → train → commit → branch → train → commit → diff → checkout → push →
  dashboard`, small encoder model, no GPU required
- **CI:** green on push

---

## 12. Open items

- Confirm RTX 3050 VRAM (4 GB laptop vs 8 GB desktop) before choosing the M1 demo model
- Base model for headline experiments: `distilbert-base-uncased`, CPU-friendly and matches the
  existing YAML configs
- Pick a keyed Sepolia RPC provider rather than a public endpoint
- Decide the Pinata free-tier plan and note its storage cap

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| Week 1 slips and Hub work starts on a broken core | Hard gate. Cut `aethel diff` before cutting tests. |
| Sepolia RPC or reorg trouble mid-demo | Keyed provider; Anvil as 30-second fallback; confirmations shown honestly |
| `dev.ps1` fails on the Windows demo PCs | Test it on those exact machines in week 2 |
| Campus network blocks PC-to-PC traffic | Four rehearsed fallbacks (§9) |
| Pinata key expires or quota hit | `/ops` surfaces it before the demo; Hub stays authoritative so the demo survives |
| Panel asks "why not just a database?" | §5.2: the Hub operator is the untrusted party |
| Panel asks a question of Adyaa or Shravan | They own real modules (§7), not paper tasks |
| Generated code looks right and is wrong | Definition of done (§7): tests that fail if behaviour is removed |
