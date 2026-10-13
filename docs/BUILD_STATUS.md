# Build status

Updated 13 October 2026. The supported college-project scope is recorded in
[Completion plan](COMPLETION_PLAN.md).

## Current implementation

- Content-addressed adapter versioning, branches, checkout, history, and integrity checks.
- Training with recorded dataset identity and held-out evaluation; effective standard-LoRA
  comparison with explicit compatibility checks.
- Verified publication to the Hub, including all ancestors and dependencies before moving refs.
- `aethel clone` downloads every published branch and restores the selected workspace in a
  new directory. Exact stored bytes are verified before installation.
- `aethel pull` downloads the current branch and advances by fast-forward only. Dirty work,
  divergence, base mismatch, and concurrent ref changes prevent replacement. Interrupted pulls
  retain verified objects for retry. Clone remembers the Hub URL/name without saving tokens.
- IPFS pinning through Pinata/Kubo and SHA-256 verification of retrieved adapter blobs.
- EVM checkpoints, recoverable transaction jobs, and independent verification of contract,
  chain, log identity, confirmation depth, inclusion, and historical prefixes.
- Dashboard history, proof verification, mirror/checkpoint status, and operational checks.

Base weights and source datasets are not part of clone. Retrieval does not itself verify
anchored provenance; that remains the separate `aethel provenance verify` workflow.

## Verification record

The full installed-dependency suite passed **954 tests, zero failures and zero skips** on
Python 3.13. Combined `aethel`/`hub` coverage is **84.40%** and core coverage is **96.14%**.
New regression coverage includes a full push/clone/pull round trip, noncanonical JSON byte
identity, histories beyond the Hub's display page, corruption/missing dependencies, limits,
interrupted transfers, local changes, divergence, and concurrent HEAD/ref updates.

Ruff and `git diff --check` passed. The wheel built successfully and includes the new CLI and
retrieval modules. Prior browser and Solidity parity checks are in
[Project review](PROJECT_REVIEW.md); they were not rerun for this retrieval change.

A targeted optional-dependency absence simulation passed **63 tests**, with **5 Hub-dependent
tests skipped**. It blocked ML, FastAPI, and provenance imports while exercising retrieval and
CLI parsing; it was not a separate clean installation.

The installed Starlette TestClient emits a dependency deprecation warning. Full HTTP tests
need an environment that permits their in-process transport. Optional extras control which
ML, Hub, and provenance tests can run.

## Public evidence

Two real DistilBERT/SST-2 pilot adapters were trained, published, pinned, retrieved, and
verified; their accepted commits were checkpointed on Sepolia. Public identifiers and measured
results are in [Review evidence](REVIEW_EVIDENCE.md). Start the saved local demonstration with
`.venv/bin/python scripts/serve_review.py` and open `http://127.0.0.1:8787`.

Public receipts record checks at a particular time. RPC/gateway availability and the canonical
chain can change; confirmation depth does not necessarily grow monotonically across a reorg.
Recheck the chain and retrieved bytes when demonstrating live verification. The current
clone/pull work does not republish or revalidate those external receipts.

## Work remaining for submission

Rehearse push/clone/pull on the presentation machine, prepare the held-out results table,
practice backup restoration, and finish the report/slides. Keep an offline fallback for
external-service outages. Merging, multi-parent history, automatic reproduction, broader
model support, and production platform expansion are optional future work, not final-year
completion requirements. See [Completion plan](COMPLETION_PLAN.md) for the acceptance checklist.
