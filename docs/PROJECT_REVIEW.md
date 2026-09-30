# Project review — 30 September 2026

Reviewed the current working tree on `foundation`, based on `0598c36`, against the latest
review comments on [PR #8](https://github.com/Kartik-MRK/Aethel/pull/8). GitHub reports that PR
as **merged**. The configuration-message and dataset-portability changes were already present
locally at the start of this review and were retained and tested.

The review covered CLI workflows, objects and refs, workspace replacement, training and
evaluation, adapter comparison, remote publication, Hub APIs and pages, IPFS retrieval, EVM
checkpoints, deployment configuration, packaging, CI, documentation, and contributor attribution.

## PR #8 comments

All seven comments are addressed in the current code or PR title. Two comments describe the
same dataset-path issue. All seven GitHub threads still have `isResolved: false`; the first
three are marked outdated. These statuses were recorded during the review; the fixes are
included in the follow-up pull request.

| Comment | Verification and disposition |
|---|---|
| [Refuse an existing work directory](https://github.com/Kartik-MRK/Aethel/pull/8#discussion_r4121799440) | Already merged: exclusive `work.mkdir()` refuses existing directories, files, and symlinks before writing dataset/configuration files. Covered by `test_prepare_review_models.py`. |
| [Validate accepted commits again](https://github.com/Kartik-MRK/Aethel/pull/8#discussion_r4121799394) | Already merged: publication checks every reachable commit, tree, base, and blob, including accepted ancestors and retries. Covered by missing/corrupt dependency tests in `test_publication.py`. |
| [Normalize contract byte values](https://github.com/Kartik-MRK/Aethel/pull/8#discussion_r4121799351) | Already merged: `bytes(value).hex()` consistently handles prefixed hex methods. Local EVM tests exercise publication, retries, verification, historical checkpoints, and reorgs. |
| [Deployment configuration message](https://github.com/Kartik-MRK/Aethel/pull/8#discussion_r4122390473) | Local fix verified: reports only missing variables; deployment does not require an existing contract address. Missing optional dependencies now also produce installation guidance at construction time. |
| [Portable dataset path](https://github.com/Kartik-MRK/Aethel/pull/8#discussion_r4122390535) | Local fix verified: `build_manifest(..., repo_root=...)` records internal datasets relative to that root. Training passes the root directly. Relocation, external datasets, symlinks, and old absolute manifests are covered. |
| [Duplicate portable-path comment](https://github.com/Kartik-MRK/Aethel/pull/8#discussion_r4122390579) | Addressed by the same implementation and tests. |
| [Broaden the PR title](https://github.com/Kartik-MRK/Aethel/pull/8#discussion_r4122390647) | Already updated on GitHub to “Provenance integration plus evaluation protocol, storage safety/concurrency hardening, and CI expansion”. |

## Additional findings fixed

Severity reflects the failure before these changes.

| Severity | Finding and resulting behavior | Regression coverage |
|---|---|---|
| High | Checkout left files absent from the target tree and could partially replace the workspace. It now stages the complete target, replaces the workspace, and restores the prior workspace if the HEAD write fails. A moved target branch is refused. | `test_workspace_safety.py` |
| High | Atomic writes in the same process shared PID-based temporary names. Exclusive random names isolate concurrent writers while preserving umask-based group permissions. | `test_core_atomic.py` |
| High | A source file changing between hashing and copying could be installed under the wrong hash. Streamed copies now verify their bytes before installation, including extraction. | `test_core_objects.py` |
| High | Corrupted stored objects were acknowledged as already present and omitted from push negotiation. Negotiation now verifies bytes; verified uploads and local writes repair corruption. Incorrect replacement bytes remain rejected. | `test_hub_api.py`, `test_core_objects.py`, `test_publication.py` |
| High | Blob size was checked only after buffering the entire request; structured-object uploads had no equivalent check. Both upload routes now stop reading at the configured limit, including requests without Content-Length. | `test_hub_api.py` |
| High | A branch changing during evaluation could silently become the new commit's parent. The CLI captures the expected branch/tip before evaluation, evaluation records its parent, and the final ref update checks HEAD and the tip under the ref lock. | `test_workspace_safety.py`, `test_core_refs.py` |
| High | Surrounding spaces bypassed the current-branch deletion guard. Names are normalized first, and deletion checks HEAD under the shared ref lock. Branch creation is also serialized. | `test_workspace_safety.py`, `test_core_refs.py` |
| High | Commit creation accepted missing bases or bases inconsistent with recorded training metadata. It now reads and verifies the base before snapshotting and checks supplied model/revision fields. | `test_cli_integrity.py` |
| Medium | Training metadata could change during scoring or between scoring and commit creation while metrics retained an earlier evaluation identity. Metadata digests now bind evaluation and commit creation; scoring rejects changes to either adapter or reference metadata. | `test_training_pipeline.py`, `test_workspace_safety.py` |
| Medium | `fsck` ignored detached HEAD and did not implement the documented adapter/tree consistency check. It now checks both, along with branch refs and dependencies. | `test_cli_integrity.py` |
| Medium | Leading-dot branch names disappeared from branch listings; trailing dots were not portable to Windows. Both forms are now rejected. | `test_core_refs.py` |
| Low | Documentation misstated upload response codes, lock paths, corruption repair, and recovery after a partial log append. Relevant design and API documentation now describe actual behavior. | Source/API review and documentation tests |

## Validation

- Full installed-dependency suite: **894 passed, zero failures, zero skips** on Python 3.13.
  This includes CPU training, save/reload and evaluation, effective adapter differences,
  Hub publication, IPFS mocks, and real contract execution on a local EVM.
- Coverage across `aethel` and `hub`: **83.48%**; `aethel.core`: **96.03%**, above CI's 80% floor.
- Optional-dependency absence simulation: **385 passed**; Hub/ML/provenance-dependent tests
  skipped as intended. Imports were blocked explicitly; this was not a separate clean installation.
- `ruff check .` and `git diff --check` passed.
- JavaScript/Python hashing and Merkle proof parity: **306/306** cases agreed.
- Solidity 0.8.30 rebuild produced no change to the committed contract artifact.
- Python wheel built successfully with all packaged Hub and contract assets checked.
- Chrome checks passed against a disposable local fixture: navigation, filtering, status refresh,
  light/dark mode, mobile overflow, browser proof recomputation, and no console errors.

The installed Starlette test client emits one deprecation warning about its use of `httpx`.
The suite still passes; dependency migration is separate from these correctness fixes.

## Attribution and review limits

GitHub lists exactly three contributors: `hs-sathwikhs`, `Kartik-MRK`, and `Shravan-PES`.
Tracked source and available Git history contain no assistant attribution or co-author credit.
GitHub's standard merge committer is present in history; it is not an additional contributor.
Third-party font license notices remain intact.

This review did not publish IPFS content, spend funds,
change deployments, rewrite history, or post GitHub comments. Browser fixtures use synthetic
adapter bytes; they validate UI behavior, not model quality. Live RPC and gateway availability,
other Python/OS combinations, and production load were not revalidated here.

Recovery after a killed process can still require inspecting stale locks, `workspace.backup`,
or a partial trailing transparency-log record. Verification deliberately fails closed in those
cases. Rechecking all reachable objects also incurs I/O proportional to history; the review
prioritizes integrity and does not establish production-scale throughput.
