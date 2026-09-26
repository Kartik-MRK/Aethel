# Build status

Updated after the chain-status display correction and the final checks.

## Current implementation

- IPFS pinning supports Pinata and Kubo. Adapter downloads must match the expected SHA-256;
  failed retrievals retain their CID for retry. Provider and gateway failures are explicit.
- A compiled non-upgradeable Solidity contract records increasing checkpoints and supports
  two-step owner rotation. Transaction jobs persist signed bytes before broadcast.
- Independent verification checks the chosen chain, exact deployed bytecode, log identity,
  confirmation depth, positional inclusion, and every earlier checkpoint prefix.
- The dashboard adds a provenance page, live background checks, proof bundles, mirror records,
  external receipt links, repository search, and compact expandable run metadata.
  Unavailable or stale chain checks are distinguished from roots awaiting anchoring.
- Two real DistilBERT/SST-2 pilot adapters have been trained, pinned, downloaded, and verified.
  Their two accepted commits are checkpointed on Sepolia. Independent verification through
  Tenderly succeeded separately from the Hub's PublicNode RPC.
- Separate service units, a worker timer, HTTPS proxy configuration, packaged static assets,
  and recovery instructions are included. Remote host installation is still pending.

## Evidence and operation

See [Review evidence](REVIEW_EVIDENCE.md) for the public contract, receipt, CIDs, and measured
pilot results. Start the local demonstration with `.venv/bin/python scripts/serve_review.py`
and open `http://127.0.0.1:8787`.

The [low-level design](LOW_LEVEL_DESIGN.md) specifies the modules, schemas, algorithms, threat
model, and design-to-test traceability for everything listed above, and section 13 there specifies
the remaining work named at the bottom of this file.

The prior environment blockers are resolved: declared development dependencies are installed
in `.venv`, the full HTTP suite runs with approved socket access, and public model downloads
succeeded. Shared IPFS gateways returned HTTP 429 during the live run; both copies were then
verified through the account's existing dedicated gateway. No test was changed to fake network
success, model quality, or public-chain confirmation.

The suite result depends on which extras are installed, so the counts below are reported per
environment rather than as one headline number. The four CI jobs were each simulated locally by
hiding the packages that job does not install.

| Environment | Result |
|---|---|
| CI `core` job, `[dev]` only, Python 3.10 to 3.13 | 347 passed, 158 skipped; core coverage 95.58% against an 80% gate |
| CI `hub` job, `[dev,hub]` | 790 passed, 13 skipped |
| CI `provenance` job, `[dev,hub,provenance-test,fonts]` | 814 passed, 5 skipped |
| Full development venv | 836 passed, 1 deprecation warning |
| Base install, system `python3` | 797 passed, 6 skipped |

A module-level skip marks the whole module as one skip item, so the skipped counts do not sum to
the collected total; they are not missing tests. Ruff and the 306/306 Python/JavaScript proof
parity checks passed. Chrome verified navigation, filtering, refresh, both themes, mobile
overflow, browser proof recomputation, and no console errors. Live chain, IPFS, and all eight Hub
health checks passed. A built wheel contains the contract and dashboard assets.

The chain evidence is durable rather than point-in-time: the checkpoint block number, the
on-chain root, and the adapter SHA-256 digests stay valid, while a confirmation count only ever
grows. See the [review evidence](REVIEW_EVIDENCE.md) for those receipts.

## Remaining completion work

The next product work is the two-parent schema, verified clone/fetch/pull, compatible weighted
merge, base fingerprints, and evaluation reproduction. Each is specified in section 13 of the
[low-level design](LOW_LEVEL_DESIGN.md) rather than left as an intention. These changes need
matching comparison, merge-history, and reproduction views. Final experiments must use compatible
classifier heads, larger datasets, repeated seeds, and untouched test sets.

For release, finish the environment lock, hosted CI, installation on the presentation machine,
remote HTTPS hosting, independent contract security review, backup/restore drills, and submission
materials. The current delivery is a working public-testnet review build. The
[completion plan](COMPLETION_PLAN.md) gives the order and acceptance gates for the last phase.
