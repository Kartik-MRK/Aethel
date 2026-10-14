# Documentation

Reference for the implemented system, current as of 14 October 2026. Documentation is maintained
on `main`. The supported workflows, design, configuration, and limitations are described here.

## Read by task

| Need | Read |
|---|---|
| Install, train, evaluate, compare, push, clone, or pull | [User guide](guide.md) |
| Understand the data model, algorithms, and failure behavior | [Architecture](architecture.md) |
| Run a Hub, verify provenance, deploy, or recover data | [Operations](operations.md) |
| Change code, run tests, build packages, or update assets | [Development](development.md) |
| Cite measured results, public receipts, and validation | [Evidence](evidence/README.md) |

Each Markdown guide owns one subject and links to the others. Read the user guide first,
architecture second, operations third, then development and evidence. The
[standalone team explainer](aethel-explainer.html) presents the same system as one illustrated,
detailed reference for the team and project guide; its overlap is intentional so that the
single HTML file can be published independently.

Together these documents explain the supported project at workflow and engineering level.
Exact code paths, library internals, and environment-specific behavior still require source
inspection and hands-on execution. Reading documentation does not replace running recovery
procedures or verifying the runtime environment.

## Supported scope

| Area | Implemented behavior |
|---|---|
| Versioning | Content-addressed adapters, commits, branches, checkout, history, status, and integrity checks |
| Training and evaluation | Pinned base revision, recorded dataset identity, deterministic held-out splits, accuracy and macro-F1 |
| Comparison | Effective standard-LoRA updates and saved-module differences with compatibility checks |
| Publishing | Verified uploads, complete history/dependency validation, fast-forward remote refs |
| Retrieval | All-branch clone and fast-forward pull, verified bytes, retry reuse, dirty-workspace protection |
| Provenance | Merkle inclusion, verified IPFS adapter copies, EVM checkpoints, independent prefix verification |
| Dashboard | Repositories, commit history, proof checks, mirror/checkpoint status, health and API reference |

The full feature-build validation passed **954 tests with zero failures and zero skips** on
Python 3.13, with **84.40%** combined coverage and **96.14%** core coverage. Lint and wheel
checks passed. Exact scope and historical checks are recorded under [Evidence](evidence/README.md#software-validation).
These are recorded local results, not a claim about every machine or the latest hosted CI run.

## Supported limits

The system versions single-parent adapter history and uses fast-forward sharing. Adapter
merging, multi-parent history, continued training from an adapter, automatic reproduction,
base/tokenizer file fingerprints, and multi-user account management are not supported.
These are scope boundaries, not unfinished steps in the documented workflow.

## End-to-end understanding

A complete operational walkthrough covers training and held-out evaluation, committing and
comparing versions, publishing, cloning into a new directory, pulling a later commit,
inspecting the dashboard, and independently checking a checkpoint. It also includes dirty
workspace and divergence refusals, a failed transfer/retry, and backup restoration. Those
exercises connect the written design to the system's observable behavior.

## Boundaries to explain

A clone contains tracked adapter artifacts and metadata, not base weights or source datasets.
Its hash checks validate the history returned by the selected Hub. A separately trusted checkpoint
is required for independent provenance verification. Neither stored metrics nor an anchored
commit proves that training was honest or the model is useful. Public receipts describe checks
at a particular time; gateway availability and canonical chain state can change.
