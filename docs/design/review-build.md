# Review build decisions

## Publication

Uploads and publication are separate operations. Receiving a commit object does not make it a
usable branch. Publication walks the full ancestry and validates all dependencies before moving
the ref or logging accepted history. Display limits apply only to display queries.

The client remembers branch tips returned by negotiation and sends `expected_tip` when publishing.
The Hub checks this expectation and fast-forward ancestry while holding the repository lock. A
retry of the already published tip is allowed. Older clients without `expected_tip` still receive
fast-forward protection.

The log records accepted commits before the ref file is replaced. If ref persistence fails,
accepted entries can remain without that branch pointing to them. The previous ref stays intact;
a retry reuses those entries. The system does not claim an atomic transaction across the log and
ref files. Rejected conflicts are checked before log acceptance.

## Training and data

Training starts a fresh adapter from the pinned base and records that initialization source.
Version-history parents describe snapshots, not proof that one adapter initialized the next.
Continued training from an adapter remains future implementation work.

Outputs are written to a temporary directory inside the repository. Only a successful run can
replace the workspace. Replacement first preserves the old directory as `workspace.backup`;
if installation fails it restores that directory. A backup left by an interruption blocks another
replacement until inspected. Editors changing the workspace during a run also block replacement.

Examples are identified by their text and numeric label. Normalized whitespace and case identify
duplicates; inconsistent labels for the same normalized text are an error. Duplicate detection
does not establish absence of semantic duplicates across datasets.

The split seed is independent of the training seed. Changing adapter initialization must not
silently change the held-out benchmark. Each label needs at least three unique examples so all
three partitions contain it. Small or unrepresentative datasets must be expanded explicitly.

Manifests record hashes of raw input bytes and canonical examples, chosen sample indices, and
the exact partition membership. Evaluation reconstructs the manifest and rejects mismatches.
The validation split guides development; the test split is selected explicitly for final reporting.

## Evaluation

An evaluation records adapter and configuration hashes plus a specification containing model
revision, dataset identity, label meanings, token length, split identity, and sample count.
Parent comparison applies one specification to both sides. A metric without matching context
cannot establish improvement. Explicit label names are required for cross-dataset comparison;
dataset overlap checks beyond identical dataset manifests remain part of the final experiment work.

Batched loss is weighted by the number of examples, including the final partial batch. Macro-F1
includes every configured class; classes with no correct predictions do not disappear from the
average. Non-finite logits or loss are errors. The dataset and model run on the same device.

## Adapter diff

For standard LoRA, the effective update is `(alpha / rank) * B @ A`. Rescaling A and inversely
rescaling B leaves this update unchanged, so factor-vector cosine alone is not a reliable diff.
The implementation computes effective-update norms, distance, relative change, and cosine in
row blocks. Saved classifier/module tensors are compared separately.

Different ranks can represent the same update. A zero update has no defined cosine. Unsupported
PEFT variants and rank/alpha patterns are rejected rather than evaluated with the wrong formula.
Weight similarity is descriptive evidence; it does not prove honest training or ancestry.

## Remaining limits

The later provenance implementation publishes checkpoints to Sepolia and checks every earlier
prefix through RPC. Two real SST-2 pilot adapters have been pinned and retrieved from IPFS.
See [Review evidence](../REVIEW_EVIDENCE.md) for receipts and [Provenance runbook](../PROVENANCE_RUNBOOK.md)
for deployment and verification boundaries. The full HTTP suite now runs. Merge, clone/pull,
broader experiments, and full reproduction remain final-phase work.
