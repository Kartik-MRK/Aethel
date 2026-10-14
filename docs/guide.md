# User guide

For the current scope, see the [documentation index](README.md). These examples use a shell
with the project's virtual environment activated and run from the checkout root unless noted.
On Windows, use `.venv\Scripts\Activate.ps1` instead of `source .venv/bin/activate`.

## Setup and data

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,hub,ml]'
```

The base installation works without torch or FastAPI for local versioning and Hub retrieval.
`[ml]` adds training/evaluation/comparison; `[hub]` runs the server; `[provenance]` adds EVM
verification. CUDA-only quantization dependencies live in the optional `[gpu]` extra.

For the supplied classification examples, download the demo datasets once:

```bash
python scripts/setup_demo_data.py
```

This helper downloads up to 800 rows from each of five public datasets into `datasets/` at
the checkout root. It overwrites those generated CSVs when rerun. The YAML examples in
[train_yaml](../train_yaml/) reference those locations. Downloads require network access and
the `datasets` package from `[ml]`. These small subsets are for workflow demonstrations.

For your own data, provide a CSV with text and numeric label columns and configure their names.
The training YAML accepts a CSV path or a directory containing the expected training CSV.
Use [sst2_config.yaml](../train_yaml/sst2_config.yaml) as the starting configuration. Set
`task_type: sequence_classification`, label meanings, LoRA rank/alpha, batch settings,
`seed`, `split_seed`, and held-out fractions explicitly. Dataset paths resolve from the Aethel
repository root; the `--config` path resolves from the command's working directory. Run the
supplied examples from the checkout root.

## Training configuration reference

Unknown YAML fields are rejected. The accepted task names are `sequence_classification` and
`SEQ_CLS`; the normalized internal task is `SEQ_CLS`. Required keys must be present even when
the parser has numeric defaults for related fields.

| Field | Requirement / default | Meaning |
|---|---|---|
| `dataset` | Required | CSV path or directory; relative to the repository root |
| `task_type` | Required | Supported classification task |
| `lora_rank`, `lora_alpha` | Required, positive integers | Low-rank dimension and update scaling |
| `batch_size`, `epochs` | Required, positive | Training batch size and epoch count |
| `text_column`, `label_column` | `text`, `label` | Input CSV columns |
| `num_labels` | `0` | Infer the class count; an explicit count must match data |
| `label_names` | Optional | Class meanings; when supplied they must match the class count |
| `max_samples` | `0` | No sample cap; a positive value limits selected examples |
| `max_length` | `128` | Positive tokenizer sequence-length limit |
| `gradient_accumulation_steps` | `1` | Positive accumulation count |
| `seed`, `split_seed` | `42`, `42` | Training and dataset-partition randomness, separately |
| `eval_batch_size` | `8` | Positive evaluation batch size |
| `learning_rate` | `0.00005` | Positive finite optimizer learning rate |
| `validation_fraction`, `test_fraction` | `0.2`, `0.2` | Held-out fractions; valid partitions must retain training examples |
| `lora_dropout` | `0.05` | Dropout in the range `[0, 1)` |
| `deterministic` | `false` | Request deterministic training behavior |

Numeric inputs must have the appropriate type and be finite. Seeds and sample/class counts
cannot be negative; the training seed is below `2**32`. Small class populations can still make
an otherwise valid fraction setting unusable. The trained adapter records effective settings,
package versions, device, precision, base revision, initialization, and its dataset manifest.

## Train, evaluate, and commit

```bash
aethel init --model distilbert/distilbert-base-uncased --author "Team member"
aethel train --config train_yaml/sst2_config.yaml
aethel eval --json
aethel commit -m "SST-2 baseline" --require-evaluation
aethel status
aethel log
aethel fsck
```

`init` resolves and records an immutable base revision. It downloads model metadata, not the
base weights; training fetches the pinned weights. A local repository is discovered from the
current directory or its parents. New commits use the configured author; without `--author`,
initialization checks `AETHEL_AUTHOR`, `USER`, `USERNAME`, and `LOGNAME`.

`train` stages a new adapter in `.aethel/workspace/`. It starts from the pinned base rather
than continuing from the previous adapter. Replacement is staged separately, preserving the
old workspace on failure. Uncommitted workspace files require explicit `--force` replacement.
A missing dataset is an error. `--allow-stub` deliberately uses synthetic data and records that
fact; a stub run must not be presented as measured model quality.

`commit` snapshots the workspace and advances the current branch without changing the staged
files. `--require-evaluation` makes failed held-out evaluation block the commit. Without it,
evaluation failure or absence is recorded explicitly. Changing the staged artifacts invalidates
previous evaluation hashes.

Keep `split_seed` fixed when comparing different training seeds. Normalized duplicate text is
removed before splitting; contradictory labels are rejected. Each class needs enough unique
examples for train, validation, and test partitions. Use validation data for development and
select the test split explicitly for final reporting:

```bash
aethel eval main --split test --json
```

Evaluation requires the original dataset. A manifest records input hashes and split membership;
changed data is refused. Datasets inside the repository are recorded relative to its root,
while external dataset paths can remain absolute. Relocating a clone may therefore require
restoring the original dataset at its recorded location.

## Branch, restore, and compare

```bash
aethel branch experiment
aethel checkout experiment
# Train and commit the next experiment here.
aethel diff main experiment --json
aethel checkout main
```

`branch [name] [start-point]` lists or creates branches. `branch --delete <name>` removes an
inactive branch. Checkout accepts a branch, full commit hash, or unique short hash. A hash
checkout detaches HEAD; return to a branch before committing. Checkout refuses dirty work
unless `--force` is supplied and restores the complete target tree, removing obsolete files.

`diff` compares committed standard-LoRA effective updates against compatible base/task settings.
It reports norms, distance, relative change, cosine, and saved-module differences separately.
It reads safetensors and rejects unsupported variants; similarity does not prove ancestry,
training quality, or safe merge compatibility. Adapter merging is outside the supported command set.

## Publish, clone, and pull

Start the Hub in a separate terminal:

```bash
python -m hub
```

The default listener is `0.0.0.0:8000`; browse `http://127.0.0.1:8000`. The default store is
`./hub-data`, relative to the server's working directory. Use `AETHEL_HUB_DATA` with an absolute
path when launching it from different directories. [Operations](operations.md) covers tokens
and server configuration.

```bash
aethel push --repo sentiment --remote http://127.0.0.1:8000 --dry-run
aethel push --repo sentiment --remote http://127.0.0.1:8000
aethel clone sentiment teammate-copy --remote http://127.0.0.1:8000
cd teammate-copy
aethel fsck
aethel pull
```

On another machine, replace loopback with the shared Hub's reachable URL. Push validates local
objects, uploads only what the Hub lacks, and moves the remote ref after complete validation.
A repeated push reuses objects and accepted log entries. `--branch` can publish a named branch.
The published repository name is saved in local configuration after success.

Clone downloads **all published branches and their entire history** into a new destination.
It refuses existing directories, files, and symlinks, including empty directories. The default
destination is the repository name. It restores `main` or the only branch; use `--branch <name>`
if there is no unambiguous default or to select another branch. All history must use the same
pinned base. `--author` chooses the identity for future local commits, not the authorship of
received commits. The Hub must serve the raw-object endpoint introduced with clone/pull.

Clone saves `hub_url` and `hub_repo`, never a token. Later push/pull URL selection is:
`--remote`, then `AETHEL_HUB_URL`, then the saved URL, then `http://127.0.0.1:8000`.
Repository selection is `--repo`, then the saved name, then the directory name.
`--token` or `AETHEL_HUB_TOKEN` supplies a shared Hub token when needed.

Pull updates **only the current attached branch**. It pins one remote snapshot, validates its
history, and advances if the local tip is an ancestor. An already current or locally ahead
branch leaves the workspace alone. Divergence, a missing remote branch, a base mismatch, or
modified/deleted/untracked workspace files blocks replacement. `--branch` must match the
current branch; checkout another branch before pulling it. There is no force-pull option.
Interrupted transfers reuse verified objects on retry; a failed final ref update restores
the old workspace.

Base weights and source datasets are not cloned. IPFS mirrors contain adapter blobs rather
than complete repositories. Hash-verified retrieval and independent checkpoint verification
are distinct operations; see [Operations](operations.md#independent-verification).

## Command map

| Command | Purpose |
|---|---|
| `init`, `train`, `commit` | Pin a base, stage an adapter, record a snapshot |
| `branch`, `checkout`, `log`, `status` | Navigate history and inspect local state |
| `fsck` | Verify stored bytes, dependencies, refs, detached HEAD, and reachability |
| `eval`, `diff` | Evaluate a recorded split and compare committed adapters |
| `push`, `clone`, `pull` | Share history with a Hub |
| `provenance` | Mirror, anchor, inspect, and independently verify; see Operations |

Use `aethel <command> --help` for the complete flags. The running Hub's `/api` page is the
maintained REST reference; `/openapi.json` is the machine-readable schema.

## Demonstration without training

For a disposable dashboard and retrieval rehearsal:

```bash
python scripts/seed_demo.py --dir .demo/rehearsal
AETHEL_HUB_DATA=.demo/rehearsal/hub-data python -m hub
```

In PowerShell, set `$env:AETHEL_HUB_DATA` before starting the Hub. The seed script uses the
real Hub API but generates synthetic adapter bytes and invented metrics. Those files can
exercise storage, history, proofs, clone, and pull; they are not trained models or evaluation
evidence. It refuses an existing destination unless explicitly forced. Choose a fresh path
instead of overwriting a useful demo store.

For the recorded real-data experiment, use the preparation/serving instructions in
[Operations](operations.md#run-the-prepared-demonstration) and the receipts in [Evidence](evidence/README.md).
To exercise the complete sharing workflow, publish a new commit after cloning and observe the
clone fast-forwarding on pull. Rehearse dirty-workspace and divergent-history refusals in disposable copies. Do not
corrupt the saved public-evidence store for a live demonstration.

## Troubleshooting

| Symptom | Action |
|---|---|
| No repository found | Run in a repository or initialize a new one with a pinned model. |
| Dataset missing or manifest mismatch | Restore the original input at its recorded path; do not rewrite stored results to hide a mismatch. |
| Workspace has uncommitted changes | Commit or deliberately restore the work. Pull has no discard override. |
| Detached HEAD | Checkout an existing branch or create one at the desired commit. |
| Diverged histories | Preserve both experiments on branches; choose which one to present. Automatic merging is outside scope. |
| Hub unreachable or token rejected | Check the listener, URL, network access, and `AETHEL_HUB_TOKEN`. |
| Clone object route returns 404 | Confirm the repository/object exists and the Hub includes the clone/pull raw-object API. |
| Missing or corrupt objects | Run `fsck`, recover a verified original or retry transfer. Do not change the digest to match corrupted bytes. |
| Lock or `workspace.backup` remains after interruption | Stop the relevant writer, inspect preserved work, and follow [recovery](operations.md#retry-and-recovery-behavior). |
| Hub appears empty after restart | Confirm `AETHEL_HUB_DATA` resolves to the original store. |
