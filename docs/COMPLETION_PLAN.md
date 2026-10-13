# Final-year project completion plan

Updated 13 October 2026. This is the current scope; the older milestones in
[Project plan](PROJECT_PLAN.md) are a historical design and audit record.

## Agreed delivery

Aethel versions LoRA adapters against a pinned base model, records held-out evaluation,
compares effective adapter updates, shares verified history through a Hub, mirrors adapter
blobs on IPFS, and verifies commit inclusion against public-ledger checkpoints.

This is the final-year project scope. Merging is optional future work, to be reconsidered
only when explicitly requested or when a larger live deployment needs it. No two-parent
schema, merge algorithms, accounts platform, or production hosting expansion is required
for the college submission.

## Implemented workflow

| Capability | What can be demonstrated |
|---|---|
| Local versioning | Initialize, train, commit, branch, checkout, log, status, and integrity checks |
| Evaluation | Recorded dataset identity, deterministic held-out splits, accuracy and macro-F1 |
| Comparison | Effective standard-LoRA update differences with compatibility checks |
| Publishing | Hash-verified uploads, complete dependency validation, fast-forward remote refs |
| Retrieval | Clone every published branch and history; pull the current branch by fast-forward |
| Workspace preservation | Dirty or diverged pulls refuse; failed replacement restores prior work |
| Provenance | Merkle inclusion, IPFS blob verification, EVM checkpoints and independent verification |
| Dashboard | Repository/history browsing, proof checks, mirror status, checkpoint and health views |

Clone restores tracked adapter artifacts and metadata. Base weights and source datasets are
separate inputs. Successful retrieval verifies content against the branch snapshot returned
by the selected Hub; use independent provenance verification to check a trusted checkpoint.
It does not establish the truth of training claims or the quality of the adapter.

## Remaining submission work

These are evidence and presentation tasks, not a request to add more product features.

1. **Rehearse the complete workflow.** Publish from one directory or machine, clone into a
   fresh destination, publish a second commit, and pull it. Show matching hashes and workspace
   files. Demonstrate one dirty-workspace refusal and one divergent-history refusal.
2. **Prepare a modest, reproducible results table.** Use the existing supported model/task,
   record the base revision, dataset/split identity, seed, environment, accuracy, macro-F1,
   adapter size, and transfer size. Keep the test split out of tuning. Report the existing
   tiny pilot as a pilot; any broader accuracy claim needs a suitable held-out experiment.
3. **Rehearse on the presentation machine.** Install the declared extras, cache the required
   base model and dataset, run the supported commands, and save the exact commands and results.
   A clean-machine run is stronger evidence than another feature added to the development laptop.
4. **Keep a backup and an offline demonstration.** Copy the repository, Hub data, proof bundle,
   and public receipts. Test restoring the backup. Explain cached evidence clearly if an RPC
   provider or IPFS gateway is unavailable during the presentation.
5. **Finish the report and viva material.** Explain content addressing, the adapter/base split,
   held-out evaluation, effective-update comparison, fast-forward history, and what an anchored
   Merkle root proves. Use [System reference](SYSTEM_REFERENCE.md),
   [Low-level design](LOW_LEVEL_DESIGN.md), and [Review evidence](REVIEW_EVIDENCE.md).

## Acceptance checklist

- [ ] The team can run the push/clone/pull demonstration from the documented commands.
- [ ] A fresh clone passes `aethel fsck` and matches the selected published commit.
- [ ] Deleted/modified workspace files and divergent local commits are preserved on refusal.
- [ ] Reported metrics identify the evaluation split and do not imply a broad study from pilot data.
- [ ] Independent verification is explained separately from trusting the Hub's current branch tip.
- [ ] The presentation machine and an offline backup have both been rehearsed.
- [ ] The report, slides, source archive, and team attribution are ready for submission.

Automated regression coverage is recorded in [Build status](BUILD_STATUS.md). This checklist
tracks the team's final demonstration and submission evidence; tests alone do not complete it.

## Optional future work

Revisit these only for a concrete requirement: compatible adapter merging and multi-parent
history; a dedicated evaluation-reproduction command; base/tokenizer file fingerprints;
additional model families; account management and access roles; production hosting, monitoring,
load testing, and an independent contract security audit. They are not completion blockers.

The supported pull policy remains fast-forward only. When histories diverge, preserve both
branches and choose which experiment to present; automatic merging is not part of this build.
