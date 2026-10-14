# Aethel

**Version control and provenance for LoRA adapters.**

Aethel versions small model adapters against a pinned base-model revision. It provides local
history, held-out evaluation, effective-update comparison, verified Hub sharing, IPFS mirrors,
and independent verification of commit inclusion against EVM checkpoints.

The base weights are referenced rather than stored. Adapter sizes depend on the model and LoRA
configuration; the two recorded DistilBERT experiments each contain about 2.54 MiB of adapter weights.

## Install

Python 3.10 or newer is required. From this checkout, create and activate a virtual environment,
then install the parts you need:

```bash
python -m pip install -e .                       # CLI, versioning, push, clone, pull
python -m pip install -e '.[ml]'                  # training, evaluation, adapter comparison
python -m pip install -e '.[hub,provenance]'       # Hub server and checkpoint/IPFS operations
python -m pip install -e '.[dev]'                 # tests and lint
```

The Hub and ML packages are optional for clients that only retrieve or inspect repositories.
`scripts/dev.sh` and `scripts/dev.ps1` set up the base, development, and Hub dependencies.

## Use

Prepare a CSV dataset and YAML config as described in the [user guide](docs/guide.md), then:

```bash
aethel init --model distilbert/distilbert-base-uncased
aethel train --config train_yaml/sst2_config.yaml
aethel eval --json
aethel commit -m "SST-2 baseline" --require-evaluation
aethel log
aethel fsck
```

Run `python -m hub` in another terminal and share the repository:

```bash
aethel push --repo sentiment --remote http://127.0.0.1:8000
aethel clone sentiment teammate-copy --remote http://127.0.0.1:8000
cd teammate-copy
aethel pull
```

Clone requires a new destination and verifies all published history. Pull advances the current
branch by fast-forward and protects uncommitted work. Base weights and source datasets are
separate downloads. Use the shared Hub's reachable address when working on another machine.

## Documentation

Start at the [documentation index](docs/README.md) for scope, validation, and the reading order. The focused guides are:

| Document | Contents |
|---|---|
| [User guide](docs/guide.md) | Setup, commands, evaluation, sharing, demo, troubleshooting |
| [Architecture](docs/architecture.md) | Storage schemas, algorithms, concurrency, trust boundaries |
| [Operations](docs/operations.md) | Hub configuration, IPFS, checkpoints, deployment, recovery |
| [Development](docs/development.md) | Source layout, team workflow, tests, packaging, assets |
| [Team explainer](docs/aethel-explainer.html) | Standalone illustrated reference for the team and project guide |
| [Evidence](docs/evidence/README.md) | Measured results, public receipts, and validation record |

Aethel supports a single-team adapter workflow. Adapter merging, multi-parent history,
automatic reproduction, and multi-user platform administration are outside its supported scope.
Hash checks establish content integrity; checkpoints establish inclusion in a recorded history.
Neither proves honest training or model quality.
