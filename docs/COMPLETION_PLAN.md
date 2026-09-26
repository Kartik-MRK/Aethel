# Aethel: completion plan

**Baseline inspected:** `foundation`, commit `168708d`, plus the existing local review documents  
**Confirmed review window:** Monday 28 September to Thursday 1 October  
**Planning decision:** two remaining phases: a working review build by Monday, then one bounded
completion-and-submission phase. The final submission date is not yet confirmed.

This is the current execution plan. It supersedes the dates, open-task lists, and milestone
sequence in `PROJECT_PLAN.md` and `TEAM_WALKTHROUGH.md`. Those documents remain useful historical
and architectural references. The audit below records the starting state. See [Build status](BUILD_STATUS.md) for
implementation completed since that audit and the checks still outstanding. Sections 4 and 5
give the current schedule; section 2 retains the original audit findings.

**Expanded review scope, now implemented:** IPFS mirroring, EVM checkpoint publishing,
independent historical verification, and the provenance dashboard have been brought forward into
the review build. Two real SST-2 pilot adapters are pinned and their commits are checkpointed on
Sepolia. The full HTTP suite now runs. See [Review evidence](REVIEW_EVIDENCE.md) and
[Provenance runbook](PROVENANCE_RUNBOOK.md). The final local suite passed with 836 tests, no skips,
and one deprecation warning. The browser and live service checks also passed.
Remaining final-phase work centers on clone/pull, merge, reproduction, larger experiments,
deployment validation, and submission materials.

## 1. Recommendation and final scope

Keep the existing core, CLI, FastAPI Hub, templates, object store, and Merkle implementation.
Finish the missing workflow and strengthen its evidence. A frontend rewrite, database migration,
large language model expansion, or multi-user platform would consume the time needed to finish.

The final capstone should demonstrate this complete workflow:

> Train two compatible LoRA adapters with recorded data and configuration; version, compare,
> branch, and merge them; publish the result; retrieve it on another machine; reproduce its
> evaluation; and independently check its inclusion in a publicly anchored history.

Use **sequence classification on one pinned DistilBERT base** as the supported ML scope. Use two
datasets with compatible label meanings for the merge study, such as SST-2 and Rotten Tomatoes,
after checking their label mappings and cross-dataset duplicates. Emotion and AG News can show
separate repositories/tasks, but their different classification heads are not merge examples.

| Required for completion | Bounded implementation |
|---|---|
| Reliable versioning and publishing | Preserve objects, protect staged work, validate complete ancestry, reject conflicting ref updates |
| Credible evaluation | Deterministic train/validation/test protocol, accuracy and macro-F1, comparable evaluation records |
| Usable remote workflow | Verified clone/fetch and fast-forward pull; another machine can restore a published adapter |
| Meaningful adapter comparison | Configuration compatibility, effective weight differences, prediction disagreement |
| One defensible merge | Weighted merging of compatible effective LoRA updates, two-parent history, measured quality |
| Reproduction | Fetch a pinned artifact and rerun the same evaluation with recorded inputs |
| Independent provenance verification | Batched public-testnet checkpoints, historical inclusion proofs, independently configured verifier |
| IPFS mirror | Mirror adapter blobs and verify downloaded bytes; retain Hub fallback |
| Submission evidence | Real results, attack demonstrations, deployment runbook, report, presentation, rehearsed demo |

Blockchain and IPFS are required by Sathwik's expanded review scope and are now demonstrated
through public Sepolia and Pinata records. Their remaining work is release validation and operation.

**Revised schedule after the review date was confirmed:** Phase A runs up to the review, with
code frozen the day before it opens. Phase B starts straight after, with **a proposed
internal completion target roughly two weeks out**, not an agreed submission deadline. This replaces the earlier
six-plus-two-week estimate. It assumes continuous implementation work in this workspace, timely decisions from Sathwik,
one usable ML machine, and access to the required external services.

The compressed scope supports one model family, one task type, one merge method, and one team.
Monday's target now includes reliable publishing, real held-out evaluation, a useful adapter diff,
public checkpoint verification, IPFS retrieval, and the finished provenance dashboard.
Remaining remote, merge, reproduction, and release work is assigned to the final phase.
That phase therefore includes implementation as well as validation; describing it as polish-only
would be inaccurate. Re-estimate its internal target on Monday using completed work and measured
training times. Do not silently drop required scope to preserve a date.

| By the review window | In the last phase, after it |
|---|---|
| Critical publishing and workspace defects fixed | Verified clone/fetch and fast-forward pull |
| Real held-out accuracy and macro-F1 for two compatible adapters | Two-parent history and one evaluated weighted merge |
| Recorded seed/configuration and dataset/split identities | Base verification and evaluation reproduction elsewhere |
| Both adapters compared on the same evaluation examples | Repeated experiments and final untouched test-set results |
| Read-only adapter diff with compatibility checks | Compare, merge-history, and reproduction dashboard views |
| Public Sepolia checkpoint, independent verification, and verified IPFS copies | Remote HTTPS deployment, security review, and recovery drills |
| Push/dashboard/proof demo using real artifacts | Report, presentation, and recorded final demo |
| Tested build, measured results, and a specific remaining-work list | No required feature left as unspecified future work |

The public checkpoint and both IPFS copies already pass their review checks. Use the saved
artifacts and independent verifier for Monday; no new deployment or pin request is needed.
The review still needs a recording, presentation-machine rehearsal, and a frozen revision.

## 2. Original audit, before implementation

This section records the starting defects and environment limitations. It is historical;
the current verification results are in [Review evidence](REVIEW_EVIDENCE.md).

| Area | Current state | Practical implication |
|---|---|---|
| Local storage | Separate content-addressed blobs, trees, commits, and bases; hashing, atomic object writes, refs, integrity checks | Strong foundation to extend |
| CLI | `init`, `train`, `commit`, `branch`, `checkout`, `log`, `status`, `fsck`, `push` are registered | Fetch, diff, merge, reproduce, anchor, and independent verify commands are missing |
| Hub | Object upload/download, repository views, publishing, token-gated writes, health board | Publishing has correctness gaps described below; token auth is not per-user authorization |
| Dashboard | Repository/commit views, metrics, proof display, operations page | Extend existing pages with real evidence and comparison states |
| Merkle log | Domain-separated hashing, inclusion proofs, Python/JavaScript verification | No external checkpoint yet; current proofs are against the current Hub root |
| Evaluation | Loss, accuracy, adapter size, parent comparison are implemented | Current data selection cannot support reliable held-out claims |
| Base reference | Model ID and immutable revision | Tokenizer/config fingerprints and full reproduction record are missing |
| Delivery | Python CI for core and Hub, local launch scripts | No ML CI job or completed contract/deployment pipeline |

### Verification performed before implementation

- Core, CLI parsing, corruption regression, and evaluation subset: **239 passed, 3 skipped**;
  **96% coverage of `aethel.core`** in that run.
- `ruff check .`: passed.
- `python3 scripts/check_verify_js.py`: **306/306** checks agreed with Python.
- The full suite was attempted but stalled in the Hub `TestClient` startup. A focused rerun
  timed out in Starlette/AnyIO's blocking portal before the first Hub assertion. The historical
  “716 passed, 3 skipped” result was **not re-established** by this audit.
- The system test environment has pytest 9.1.1, outside the project's declared `<9` range.
  Treat the startup issue as unresolved environment/test-runner diagnosis, not a demonstrated
  product failure. Establish a clean, supported development environment first.
- The existing `.venv` successfully imports torch 2.13.0+cu130, transformers 4.57.6, and PEFT
  0.20.0, but has no pytest or Ruff installed. CUDA is unavailable to that process in this
  session. The old statement that no ML stack exists locally is stale; this does not establish
  a completed real training/evaluation run.
- Three temporary-store probes called the actual Hub ref-update handler directly and confirmed
  the first three defects in the next table. They did not modify the project's existing stores.

### Improvements to prioritize

| Priority | Finding and source | Required change |
|---|---|---|
| P0 | `hub/api.py:update_ref` accepts a commit with absent tree/base/blob objects; reproduced | Validate the complete reachable object graph before publishing or logging accepted history |
| P0 | An unrelated root can replace an existing Hub branch; reproduced | Add fast-forward ancestry validation and an expected-old-tip comparison under the ref lock |
| P0 | `HubStorage.commit_history()` defaults to 100 entries and `update_ref` uses that default; a 105-commit history logs only 100; reproduced | Separate bounded display queries from unbounded correctness traversal; test histories over 100 and over UI page limits |
| P0 | `train.py:prepare_workspace` deletes the workspace before data/model validation completes | Train into a temporary run directory; replace staged output only after success and explicit overwrite policy |
| P0 | `train.py` selects CausalLM versus classification by catching arbitrary exceptions | Require and validate `task_type`; support classification explicitly and fail clearly for unsupported tasks |
| P0 | `evaluation/dataset_loader.py` evaluates the recorded training file | Introduce immutable split manifests and explicit held-out evaluation |
| P0 | `evaluate_current_vs_parent` loads each adapter's own recorded dataset | Resolve one evaluation specification for both sides, or report “not comparable” |
| P1 | Training loads the base and creates a new adapter every time; checkout does not make subsequent training continue that adapter | Add explicit initialization modes and record the actual training source separately from history parents |
| P1 | Saved training metadata omits parsed columns, sample limit, max length, gradient accumulation, and other reproduction settings | Save a complete normalized run configuration and the effective settings actually used |
| P1 | No configured seed before model/head/adapter initialization; defaults are not a reproducibility contract | Seed all relevant RNGs before initialization, record determinism settings and runtime versions |
| P1 | `core/commits.py` has one `parent_hash`; traversal is linear | Introduce compatible two-parent history support before merging |
| P1 | `commit.py` catches all evaluation failures, then commits without a structured result state | Distinguish skipped, failed, incompatible, and measured; provide explicit evaluation and strict mode |
| P1 | `/ops` compares the latest full root with the last anchor root | Compare the anchored prefix; normal growth must appear as unanchored additions, not tampering |
| P1 | Chain/IPFS health currently means configuration exists, not the dependency works | Add bounded real probes and separate unconfigured, pending, healthy, and failed states |
| P1 | CI never installs ML dependencies; existing evaluation tests do not execute a real prediction path | Add CPU ML smoke/integration coverage and a repeatable real-data experiment runner |

Some older prose claims fast-forward protection and complete remote dependency validation already
exist. The current handler and temporary-store probes contradict those claims. Use the code and
these reproducible checks as the baseline.

## 3. Implementation work packages across both phases

**Outcome:** one working release candidate containing every required feature by the final-phase
feature gate. The packages below describe the full implementation and acceptance requirements;
sections 4 and 5 assign the review scope and remaining work to their dates. Implementation is handled in this workspace under Sathwik's direction. Named leads identify
learning and presentation responsibilities, not code authorship.

### A0. Establish a reproducible development baseline

**Status:** local supported dependencies, CPU integration tests, and CI definitions are ready.
An environment lock, clean-machine installation, and hosted CI execution remain release gates.

**Study/presentation lead:** Shravan; Sathwik reviews CI.

- Document the supported development environment with `.[dev,hub,ml]`; preserve the
  existing user environment. Keep the lightweight base installation as a separately tested path.
- Resolve the Hub test-startup problem, run the existing suite, and record exact versions and results.
- Add a tested dependency constraints/lock strategy for the demo and ML runner. Compatible version
  ranges in `pyproject.toml` alone do not reproduce a known environment.
- Create a small CPU integration test using a locally constructed tiny classification model and
  valid PEFT adapter. It should train, save, reload, and predict without downloading a model.
- Add an ML CI job that installs its dependencies and fails if they cannot import. Keep real
  Hugging Face downloads and long experiments in an explicit integration/release job.

**Done when:** a teammate can create the environment and run the supported suite; base-only CLI
usage remains usable; an ML failure cannot disappear into an all-skipped green CI job.

### A1. Repair publishing and workspace safety

**Status:** review repairs are implemented and tested. Extend the same guarantees to both
parents when the merge schema is introduced.

**Study/presentation leads:** Sathwik for Hub publishing; Karthik for local state transitions.

Work in `hub/api.py`, `hub/storage.py`, `aethel/core/refs.py`,
`aethel/commands/train.py`, and `aethel/commands/checkout.py`.

- Validate commit schemas, all parents, trees, bases, referenced blobs, and adapter/tree agreement.
  Do not let a response pagination limit govern reachability or acceptance.
- Reject non-fast-forward updates by default. Send an expected previous tip and compare it under
  the same lock as the update so two valid concurrent writers cannot silently overwrite each other.
- Apply the same expected-parent protection to local commit creation: locking only the final
  write does not make the earlier parent read part of a transaction.
- Define the transaction/recovery boundary between accepted-log entries and ref publication.
  Fault injection and retries must leave an explainable, recoverable state without invented success.
- Train into staging, retain previous work after invalid configuration, missing data, model load
  failure, training failure, or interruption; validate before any destructive action.
- Exercise checkout with dirty workspaces even when checking out the current hash, moving between
  branches sharing a tip, or starting from an unborn branch. Current guards skip some such cases.
- Validate log record indices and handle a torn final JSONL write explicitly. Quarantine/report
  damage; do not silently discard accepted history to make the next run green.

**Done when:** missing dependencies, conflicting pushes, long ancestry, interrupted training,
and concurrent branch updates have regression tests; failures preserve the previous usable state.

### A2. Make evaluation and training scientifically defensible

**Status:** deterministic splits, shared evaluation, recorded run inputs, and real pilot results
are implemented. Explicit continued-training modes and the larger two-dataset study remain.

**Study/presentation lead:** Shravan; Karthik reviews training initialization; Adyaa consumes the schema.

Work in `aethel/commands/train.py`, `aethel/evaluation/`, `train_yaml/`, and
`setup_demo_data.py`. Add an explicit `aethel eval` command.

- Make `task_type: sequence_classification` explicit. Validate text/label columns, label mapping,
  class counts, numeric limits, and model compatibility before initializing training.
- Prefer published validation/test splits when usable; otherwise generate deterministic stratified
  splits. Keep final test labels out of tuning, merge selection, and divergence calibration.
  SST-2's public test labels are not available for normal local scoring: document the chosen
  validation-derived test protocol instead of pretending those labels are available.
- Detect exact normalized-text overlap across splits and both merge datasets. Record any removal
  rule; disclose that this does not eliminate all semantic duplicates.
- Fingerprint raw inputs and normalized examples; record source revision, preprocessing version,
  row counts, label map, split seed, and split membership/order hashes. Local paths are locations,
  not dataset identities. Do not publish private data by default.
- Seed before the model, classifier head, adapter, sampler, and training are initialized. Record
  Python/library/CUDA versions, device, precision, hyperparameters, and determinism settings.
- Add explicit `--from-base` / `--from-commit` initialization semantics. For continued training,
  load the adapter as trainable; for a fresh run, record that it started at the base. A history
  parent is not proof that its weights initialized the next run.
- Evaluate all compared adapters on the same input examples, preprocessing, and label meanings.
  Support a named evaluation suite for two datasets. Refuse a single “improved” verdict across
  different tasks or evaluation specifications.
- Report accuracy, macro-F1, loss, sample count, per-class support, adapter bytes, and status.
  Use batched inference and explicit device handling. Name MiB versus MB correctly.
- Bind an evaluation record to the adapter/tree identity, base, evaluation specification, and
  evaluator version. Reuse it only when these match. A failed evaluation must not reuse stale metrics.
- Let `commit` attach a matching evaluation or state why none exists. An explicit strict option
  can require successful evaluation; offline VCS operations should not require model downloads.

**Done when:** one real adapter is trained and evaluated on held-out data, parent/child comparison
uses a shared specification, custom columns survive reload, and the dashboard shows measured
accuracy and macro-F1 with the split and sample count. Fixture data stays clearly labeled.

### A3. Extend the provenance schema and base verification

**Status:** pending. This is the first final-phase implementation dependency for merge and
complete remote retrieval.

**Study/presentation leads:** Adyaa for base/run records; Karthik for commit graph; Sathwik for protocol review.

- Add base config and tokenizer manifest hashes, architecture, task, label-map identity, and
  exact model revision. Hash a specified set of files in a stable order.
- Implement `aethel base verify`; a mismatch fails with the specific differing component.
- Introduce a new commit schema with ordered `parents: []`, `[parent]`, or `[left, right]`.
  Read old schema-2 `parent_hash` commits through a compatibility adapter; never rewrite existing
  content-addressed commits or change already published hashes.
- Distinguish version-history parents from training initialization source and merge inputs.
  These relationships answer different questions.
- Update history traversal, object collection, `fsck`, Hub ancestry validation, CLI log, and DAG
  rendering together. Use deterministic traversal, visit shared ancestors once, and upload every
  parent before its child. Keep first-parent display separate from full reachability.
- Define one authoritative run/evaluation record and clear precedence for legacy
  `training_info.json`. The commit metadata currently contains evaluation that its workspace copy
  need not contain; reproduction must read the intended authoritative record.

**Done when:** old histories still read, a two-parent fixture survives push/fetch/checkout/fsck,
both branches of its ancestry are present, and missing second-parent dependencies are rejected.

### A4. Close the remote and reproduction workflow

**Status:** pending. IPFS blob retrieval is implemented; complete repository retrieval and
evaluation reproduction still require this package.

**Study/presentation leads:** Karthik for fetch/clone; Adyaa for reproduction and base verification; Sathwik reviews transport.

- Add clone/fetch and fast-forward pull using `aethel/remote/`. Pin the requested remote tip at
  the start, retrieve its complete object graph, verify every object, then update local refs.
- Add raw structured-object download support or an equally precise byte-preserving protocol.
  Current JSON GET endpoints add presentation fields and cannot be treated as original object
  bytes; the upload endpoint also accepts non-canonical JSON bytes. Hash the actual stored content.
- Preserve dirty workspaces and local branches. An interrupted transfer must be safely retryable;
  a conflicting pull should fetch and explain the divergence without overwriting local history.
- Add `aethel reproduce <commit>` to restore pinned artifacts and rerun the recorded evaluation.
  Accept a local dataset location and verify its fingerprint. A missing private dataset should be
  reported as unavailable, not replaced by another dataset.
- Produce a machine-readable comparison report with requested/observed identities, metrics,
  tolerances, environment, and pass/fail/unavailable status.
- Treat full retraining as a separate experiment. Reproducing evaluation is the command's minimum
  supported contract; reproducing optimization requires more than loading the saved adapter.

**Done when:** a second clean directory or machine fetches a published commit, passes `fsck`,
restores identical adapter bytes, and reproduces its evaluation within a predeclared tolerance.

### A5. Build adapter diff and an advisory divergence check

**Status:** effective-update diff and compatibility checks are implemented and tested.
Prediction disagreement remains for the final comparison workflow. Automatic branch advice
is optional and must not delay merge or reproduction.

**Study/presentation lead:** Karthik; Shravan supplies the common evaluation suite.

- Add `aethel diff <a> <b>` with human-readable and JSON output: base/task/label compatibility,
  configuration changes, per-layer effective-update norms/cosines, and prediction disagreement.
- For standard LoRA use the effective update `Delta W = (alpha / r) B A`, respecting orientation,
  per-layer settings, and supported PEFT configuration. Reject unsupported variants explicitly.
  Comparing flattened A/B factors directly is misleading: different factorizations can represent
  the same update. Keep large calculations per layer or in chunks.
- Report classification-head differences separately from LoRA factors. Zero updates require a
  defined “undefined cosine” state, not a fabricated score or NaN in JSON.
- Reuse these measurements for an optional branch recommendation. Calibrate any threshold on
  a validation study; record the metric and threshold. A low cosine is neither a proof of task
  divergence nor proof of false ancestry.
- Before committing, offer stay/fork when configured. Non-interactive execution needs an explicit
  policy and must not hang or silently create branches.

**Done when:** identical effective updates compare equal even with different factor scaling,
incompatible tasks are refused, real adapters produce interpretable differences, and both branch
choices preserve staged work. The core CLI still imports without torch.

### A6. Deliver one correct merge method

**Status:** pending. Complete A3 and the compatible-head experiment setup before merging.

**Study/presentation lead:** Karthik; Shravan owns evaluation; Adyaa displays the two-parent result.

- First support compatible adapters sharing the exact base revision, tokenizer, target modules,
  task, label mapping, and compatible classifier-head initialization/representation.
- Combine effective updates: `Delta W_merge = w1 * Delta W_1 + w2 * Delta W_2`.
  Do not average A and B separately: multiplying those averages introduces cross terms and does
  not implement the desired weighted sum.
- Prefer an exact factor-concatenation representation for the initial method; it increases rank
  to the sum of input ranks and avoids an SVD approximation. Account explicitly for each adapter's
  alpha/r scale and the output scale. Report the larger output size and validate PEFT reload.
- Define classifier-head behavior explicitly. Start experiments from the same saved head state
  and compatible labels; never silently take an arbitrary parent's head. Refuse inputs outside
  the implemented head policy.
- Stage a candidate on a new branch, record both parent hashes, method, coefficients, compatibility
  checks, and evaluation specification. Evaluate before making a recommendation.
- Compare each parent and the merge on both datasets. A regression is a valid result to report;
  the tool must not assume merging improves quality.

**Done when:** synthetic tensor tests prove the weighted-update identity; a reloadable real merge
has two-parent history; the Hub retains both ancestries; and a parent-versus-merge table is saved.

TIES and DARE are optional extensions after this acceptance gate. If included, compare against
the simple method, tune on validation only, record random seeds, and measure any rank-reduction
error. They are not deferred requirements for a third phase.

### A7. Complete anchoring and independent verification

**Status:** implemented and publicly verified on Sepolia. Operational deployment, independent
security review, and backup/restore validation remain in the release gate.

**Study/presentation lead:** Sathwik; Karthik reviews proof/checkpoint invariants.

The implementation uses `contracts/`, `aethel/provenance/`, `aethel provenance anchor`, and
`aethel provenance verify`, with corresponding Hub proof and checkpoint endpoints.

- Anchor the one Hub-wide log: `(root, log_size)` with an ordered batch identifier and event.
  Allow authorized submitters only; require increasing nonzero sizes; reject invalid/duplicate
  checkpoints; provide no update/delete operation for earlier batches.
- Test the contract locally before deployment. Use a configured supported public testnet;
  Sepolia is the existing target, whose current availability must be checked before deployment.
  Preserve a local Anvil recovery path and label it honestly during demonstrations.
- Snapshot root and size from the same locked log view. Persist submission state, receipt,
  chain ID, contract address, transaction hash, block number/hash, and confirmation status.
  A submitted transaction is not a confirmed checkpoint; handle replacement, retry, and reorg.
- Serve inclusion proofs for the exact anchored prefix, not just today's larger log. Bind the
  proof to its tree size/index and checkpoint, and validate these fields in the verifier.
- Check that the current log still contains the previously anchored prefix. Recomputing that
  prefix is an acceptable bounded capstone implementation; a consistency-proof protocol is an
  optimization. Monotonically increasing sizes alone do not prove append-only history.
- Keep the current ASCII-hex hashing convention stable and publish test vectors for it. The code
  uses RFC 6962-style domain separation, but hashes textual hex in places where other protocols
  use raw digest bytes. Do not silently change it or claim byte-for-byte RFC interoperability.
- The verifier gets chain ID, contract address, and checkpoint trust configuration independently
  of the Hub, then reads the on-chain checkpoint through a configured RPC. Document the RPC and
  client-code trust assumptions; this is not a self-hosted consensus verifier.
- Keep the browser visualization, but provide a separately distributed CLI or static verifier.
  JavaScript served by a malicious Hub can display a fake PASS regardless of correct hash code.
- `/ops` distinguishes a valid anchored prefix plus new unanchored entries from a changed/truncated
  prefix. Freeze further anchoring on detected inconsistency; do not bless a rewrite as a new batch.

**Done when:** a real checkpoint is independently verified; old proofs remain usable after normal
growth; altered bytes, rewritten anchored history, truncation, wrong contract/network, and
unconfirmed/reorged receipts have clear outcomes; the attack demo fails for the intended reason.

**Claim boundary:** a checkpoint proves commitment to a record by its inclusion in that chain
checkpoint under the stated trust assumptions. It does not prove honest training, permanent
availability, a person's identity, or exact wall-clock creation time. Inclusion alone also does
not prove the repository name or Hub acceptance timestamp: those log fields are not currently
hashed into leaves. Retained checkpoints and prefix checks are what expose later rewrites.

### A8. Add the IPFS mirror and finish the dashboard workflow

**Status:** Pinata/Kubo mirroring, verified retrieval, retry records, and the provenance
dashboard are implemented. Compare, merge-history, and reproduction views depend on A3-A6.

**Study/presentation leads:** Sathwik for mirroring; Adyaa for dashboard; Shravan for measured states.

- Mirror adapter blobs through a replaceable pinning backend. Store CID mappings outside immutable
  commit bodies, keyed by blob SHA-256. Delayed mirroring must not change a published commit hash.
- Record queued/pinned/failed state; make retries idempotent. Keep pinning credentials out of the
  public Hub web process, consistent with the existing configuration design.
- Fetch through two independently operated gateways where accessible; verify bytes against the
  expected SHA-256 every time. A CID is an address and does not promise persistence or availability.
  Do not assume identical bytes always produce the same CID across different import settings.
- Define the recovery boundary honestly: a blob mirror alone cannot rebuild all repository refs
  and commit metadata. Hub backups remain necessary unless a complete metadata bundle is mirrored.
- Add a compare view, two-parent merge view, real evaluation provenance, reproduction result,
  checkpoint receipt/explorer link, and mirror status to the existing dashboard.
- Use a small health vocabulary with distinct unconfigured, pending, healthy, degraded, and failed
  states as appropriate. Probe dependencies with timeouts and caching; a bad gateway/RPC must not
  prevent the dashboard from rendering.

**Done when:** mirror failure leaves publishing usable, corrupt downloads are rejected, valid
downloads match the original blob, and every dashboard verdict states what was actually checked.

## 4. Phase A: the review sprint

Sathwik directs implementation and release decisions. Remaining review preparation is separate
from the larger final-phase feature work.

| Date | Work | Acceptance gate |
|---|---|---|
| 23-25 Sep, completed | Publishing/workspace repairs, held-out evaluation, effective-update diff, public provenance, dashboard | Two real adapters published and mirrored; Sepolia independently verified; 836 tests and browser checks pass |
| 25 Sep | Prepare the presentation walkthrough and verify installation on the presentation machine | Saved adapters, commands, receipts, and proof bundle are accessible without retraining |
| 26 Sep | Rehearse the full workflow and capture a recording; use isolated tests for failure demonstrations | Successful proof and byte checks shown alongside corrupt-content and unavailable-service outcomes |
| 27 Sep | Freeze the reviewed revision and environment; fix only review blockers | Two rehearsals complete, recording available, release notes and final backlog accurate |
| 28 Sep | Present the working build, measured pilot, and final completion plan | Record review feedback and confirm the actual submission date |

| Person | Learning and presentation responsibility |
|---|---|
| Sathwik | Overall flow, publishing guarantees, blockchain/IPFS trust boundaries, release decisions |
| Karthik | Versioning, workspace protection, effective-update diff, planned two-parent merge |
| Shravan | Training/split seeds, held-out metrics, the observed regression, final experiment protocol |
| Adyaa | Dashboard walkthrough, receipts and proof bundles, health states, planned reproduction flow |

The critical dependency chains are:

```text
safe training -> recorded inputs/splits -> shared evaluation -> diff -> merge comparison
schema + full ancestry -> verified fetch -> independent reproduction
correct accepted log -> checkpoint snapshot -> historical proof -> independent verification
all three integrated -> real results -> final report/demo
```

The first chain has reached diff, and the checkpoint chain is implemented and publicly verified.
The schema, fetch, reproduction, and merge dependencies determine the next implementation order.

### Monday review acceptance gate

The review slice includes the environment and review repairs in A0-A2, read-only diff in A5,
anchoring in A7, and the mirror/provenance dashboard in A8. Full merge, repository retrieval,
and reproduction remain final-phase requirements. For Monday, the team must demonstrate:

1. Two actual compatible adapters, trained from recorded configurations. Start with two runs on
   one fixed dataset to keep the review experiment small; the two-dataset merge study comes later.
2. Held-out accuracy, macro-F1, sample count, seed, and split identity for each; both use the same
   evaluation examples. Report regression honestly if the second run performs worse.
3. Commit, branch, diff, checkout, `fsck`, push, and real metrics on the existing dashboard.
4. Tests that reject missing remote objects and conflicting refs, and cover histories over 100.
5. A corrupted byte rejected by integrity checking; valid versus altered inclusion proof behavior.
6. The existing Sepolia transaction and independent CLI verification with separately trusted
   RPC/contract settings, plus an IPFS download that matches the adapter's recorded SHA-256.
7. A recorded demonstration and precise remaining-work list for the last phase.

Use already trained, hash-verified artifacts during the presentation; replaying the short evaluation
is sufficient if live training is too slow. Preserve the training logs and recipe as evidence. A
recording is a fallback for presentation reliability, not a substitute for an implemented feature.

If a live RPC or gateway is unavailable during review, show its current unavailable state and
the dated saved evidence. Use the dedicated gateway configured in the runbook; shared public
gateways have rate-limited valid pins. Do not relabel a saved result as a new live check.

## 5. Phase B: last implementation, validation, and submission phase

**Starts:** after the review. **Proposed internal target:** roughly two weeks out.
This is an aggressive two-week target contingent on the Monday review build passing and the required
model/data access and integration checks being available. It is not a confirmed academic submission date. Continue report and
evidence collection throughout implementation.

### First week: close the finite implementation backlog

All implementation remains under Sathwik's direction. The order below follows the dependencies;
learning and presentation assignments remain those in section 4.

| Dates | Build or improve | Acceptance gate |
|---|---|---|
| 29-30 Sep | Ordered two-parent commit schema; legacy read compatibility; full DAG traversal, publication, and integrity checks | Existing hashes stay valid; both parents and their dependencies survive push and integrity checking |
| 1-2 Oct | Byte-preserving object retrieval; verified clone/fetch and fast-forward pull | A clean directory restores exact objects; interrupted transfer retries; dirty or diverged work is preserved |
| 3-4 Oct | Base/tokenizer fingerprints, explicit training initialization, and compatible-head weighted merge | Merged effective update matches its declared coefficients, reloads in PEFT, and retains both parents |
| 5-6 Oct | Evaluation reproduction, prediction disagreement, compare/merge/reproduction dashboard views | Another environment retrieves the published adapter and reproduces measured evaluation within declared tolerances |
| 7-8 Oct | Failure-path integration, environment lock, hosted CI, deployment candidate, frozen experiment setup | All required feature gates pass; no unresolved data-loss or false-verification defect; feature freeze |

Remaining portions of A1/A2, such as broader concurrency/recovery coverage and explicit continued
training, must close here alongside the new features. Automatic branching stays optional; it must
not delay the supported diff/merge workflow. The existing provenance CLI and worker timer are
retained. Revalidate their integration with the new commit graph and remote workflows.

### Second week: results, release validation, and submission package

| Work | Study lead | Evidence |
|---|---|---|
| Run frozen ML experiment matrix | Shravan + Karthik | Raw per-run records, hashes, held-out tables, variability |
| Integrity/provenance attack matrix | Sathwik + Karthik | Expected versus observed outcomes and saved proof/checkpoint artifacts |
| Reproduction on second machine | Adyaa + Shravan | Identical retrieved artifacts, metric tolerances, environment differences |
| Persistent deployment and recovery | Sathwik | Versioned install, HTTPS/token configuration, restart and backup/restore test, independent contract security review |
| UI and command walkthrough | Adyaa | Complete paths, honest missing/error states, Linux/Windows demo checks |

Run CI on supported environments and test the actual presentation machines. Verify that a wheel
or documented release installation includes Hub templates/fonts/static files; an editable checkout
alone is not packaging evidence. Add basic bounded queries, request limits, and operational
timeouts where the demonstrated deployment needs them. Keep a single-team authorization scope.

### Final report, presentation, and acceptance

- Finish the architecture/data-flow diagrams, algorithms, related-work comparison, implementation,
  experiment protocol, results, threat model, limitations, and references.
- Compare against Git plus artifact storage/Git LFS, DVC, and an experiment/model registry such as
  MLflow on documented capabilities. Position Aethel's contribution as integrated adapter-aware
  comparison and externally checkable provenance, not inventing version control or Merkle trees.
- Record the final demo, export tables/figures, prepare a poster if required, and write a quickstart
  that a reviewer can follow from a clean checkout.
- Each team member writes or revises the design note for their subsystem in their own words and
  rehearses its likely viva questions.
- Rehearse twice, including network/RPC failure and local recovery. Prepare a read-only copy of
  the successful experiment/proof artifacts; label recordings and local-chain fallbacks clearly.
- Freeze the tested release commit and environment; retain result manifests and the exact scripts
  that generated them. Fix release blockers, then submit the agreed deliverables.

### What remains pending after Monday, and what must be finished at the end?

The remaining feature work is verified repository fetching and pull,
two-parent merge, base verification, explicit continued training, evaluation reproduction,
prediction disagreement, and their dashboard views. Public anchoring, independent verification,
and IPFS are implemented and demonstrated. They still need release operation and recovery checks.
Use Monday's feedback to confirm this backlog and the academic deadline.

At the end of Phase B, no required feature remains pending: the system must train/evaluate,
version/diff/merge, publish/fetch/reproduce, mirror, and independently verify a checkpoint, with
real results and a complete submission package. If that gate is missed, report the exact blocker
and revise the completion date; do not invent a third feature phase or call an incomplete build
finished. Optional work in section 7 remains excluded.

## 6. Experiment plan and report tables

### ML study

Start with a pilot before selecting run counts or promising training time. A reasonable final
matrix is one pinned base, two compatible sentiment datasets, three seeds, and four conditions:
adapter A, adapter B, a weighted merge, and a pooled-data LoRA baseline. This requires nine trained
adapters across the three seeds; merge candidates are derived artifacts. Evaluate every condition
on both frozen test sets. Use an identical initial classification-head state where comparison or
merging assumes it, and document that protocol.

Choose merge coefficients and any divergence threshold on validation data. Report all chosen
conditions on the untouched test sets, even when merging loses. If compute is insufficient,
reduce data/model size based on the pilot and describe the limitation; do not replace repetitions
with invented numbers. An untrained random classification head is a sanity check, not a strong
pretrained sentiment baseline.

Save a table with: condition, training datasets, initialization source, seed, adapter hash,
evaluation-spec hash, per-dataset accuracy/macro-F1/loss, sample count, adapter MiB, training time,
inference time, and measured peak memory where available. Report mean and standard deviation over
seeds, with the individual results retained.

### Reproducibility study

Distinguish three measurements:

1. **Artifact reproduction:** fetching a commit must yield exactly matching object/adapter hashes.
2. **Evaluation reproduction:** the same model/data/preprocessing should match within a declared
   tolerance; record environment and precision differences.
3. **Training repeatability:** rerun a fixed recipe and compare weights and predictions. Exact
   cross-GPU byte equality is an experimental question, not a guarantee. Random initialization,
   library versions, kernels, precision, serialization, and hardware all matter.

Do not compare entire commit hashes as a determinism target: timestamps and other metadata can
legitimately differ even when adapter bytes are identical.

### Integrity and operational study

| Test | Expected result |
|---|---|
| One adapter byte changes | Hash verification rejects it |
| Referenced dependency missing | Publication/ref advance rejected |
| Two competing pushes from the same old tip | One advances; the other receives a conflict |
| More than 100 commits | Full reachable history logged and fetchable |
| Interrupted fetch or mirror operation | Retry succeeds without duplicate accepted history |
| Hub appends after a checkpoint | Old prefix still verifies; new commits show unanchored status |
| Hub rewrites or truncates anchored prefix | Independent comparison detects inconsistency |
| Wrong proof, tree size/index, chain, or contract | Verification fails or reports the precise configuration error |
| RPC timeout/unconfirmed transaction/reorg | Unknown/pending state, never a fabricated confirmed PASS |
| Gateway unavailable or returns wrong bytes | Fallback or explicit failure; corrupt bytes never installed |
| Hub restart or backup restore | Committed objects, refs, log, and checkpoint records remain consistent |

Measure adapter-only storage against the actual downloaded base size, deduplication savings,
initial/repeated push bytes, clone latency, proof generation/verification time at several log sizes,
and observed anchoring gas/latency at different batch sizes. Report hardware, conditions, and
sample counts. Testnet gas is useful evidence; it is not a production monetary-cost estimate.

## 7. Scope cuts and optional work

These are excluded from the completion gate unless the approved synopsis explicitly requires them.
They can remain in the report's future-work section after submission without creating another phase.

| Optional item | Why it can wait |
|---|---|
| TIES/DARE and rank-compressed merge | One correct measured merge establishes the workflow; advanced methods add research and validation cost |
| Ed25519 authorship attestations | Useful, but independent log-integrity verification does not require a personal identity system |
| Multi-user accounts, RBAC, OAuth | Current delivery can explicitly target one team and token-gated publishing |
| More base architectures, generation, quantization, DoRA/other PEFT variants | Each expands compatibility, merge, evaluation, and test requirements |
| Sophisticated semantic embedding/automatic branch system | An explainable advisory diff-based check is a bounded first version |
| Garbage collection | Low value for the demo and unsafe to rush; append-only provenance needs a retention policy first |
| Optimized consistency proofs, distributed witnesses, high-throughput log service | Full anchored-prefix checks suffice for the declared small deployment |
| Kubernetes, mobile app, a new frontend stack | No required capstone workflow depends on them |

If cryptographic authorship is an assessed claim, include Ed25519 in the final-phase implementation
backlog and re-estimate that scope on Monday. Use a domain-separated detached signature over a commit hash, key fingerprints, an
explicit trust model, and tamper/wrong-key tests. A key proves possession, not a person's civil
identity. Detached attestations avoid self-referential hashes and changes to anchored commits.

## 8. Final definition of done

The project is complete when all required Phase A gates and Phase B deliverables pass, the final
results are traceable to scripts and immutable artifacts, and the team can explain both success
and failure paths. No unresolved data-loss or false-verification defect is acceptable in the
demonstrated workflow.

The final presentation can then claim:

> Aethel versions and compares supported LoRA adapters, records their reproducibility inputs,
> supports a measured merge workflow, and lets an independent client verify published commit
> inclusion against a public checkpoint and detect changes to the recorded prefix.

It should not claim that a checkpoint proves honest training, that every merge improves accuracy,
that public IPFS retrieval guarantees permanence, or that identical seeds guarantee identical
weights on different hardware. Document supported scope and measured limitations alongside results.
