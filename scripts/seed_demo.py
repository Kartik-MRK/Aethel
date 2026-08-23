#!/usr/bin/env python3
"""Build a Hub store with a branching history, for local work and rehearsal.

Why this exists
---------------
The dashboard has to render a *shape*: forks, parallel lines of work, a fork
point two branches share. A single straight line of commits renders identically
whether the lane code is right or absent, so a linear store cannot tell you
whether the history rail works. Neither can a screenshot of one.

It is also the answer to a smaller, more annoying problem. The store this was
built against used to live in ``/tmp``, which meant every reboot silently threw
away the data the pages were being designed against, and the pages went blank
mid-session. A store you can rebuild in one command from a file that is checked
in is worth more than a store you happen still to have.

What it is not
--------------
**The adapter bytes are synthetic.** No training runs here -- this script never
imports torch, so it works on a laptop with no ML extra installed and finishes
in under a second. Each "adapter" is deterministic pseudo-random bytes at a
plausible size (~590 KB, about what distilbert at rank 8 over two projections
actually produces). The VCS layer never looks inside the file: it hashes bytes,
stores them, and dedupes them, and every one of those behaviours is exercised
identically by noise. The recorded metrics are likewise made up.

That is fine for building and rehearsing a UI, and it is *not* fine as evidence
of anything. Metrics on a page fed by this script are the shape of real output,
not real output.

How the objects get in
----------------------
Through the Hub's own HTTP API, driven in-process. Every blob is uploaded with
its claimed hash and re-verified server-side, every ref update walks the history
and appends to the transparency log -- the same code a real ``aethel push``
reaches. Writing files into the store directly would have been shorter and would
have produced a store the API cannot actually produce, which is the one thing a
fixture must never do.

Usage
-----
    python scripts/seed_demo.py                  # ./.demo, refuses to clobber
    python scripts/seed_demo.py --force          # rebuild from scratch
    python scripts/seed_demo.py --dir /srv/demo  # somewhere else

Then serve it:

    AETHEL_HUB_DATA=.demo/hub-data python -m hub
"""

import argparse
import random
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aethel.core.commits import build_base_object, create_commit  # noqa: E402
from aethel.core.repo import Repo  # noqa: E402
from aethel.remote.objects import UPLOAD_ORDER, build_push_plan  # noqa: E402

REPO_NAME = "sentiment-lora"

BASE_MODEL_ID = "distilbert-base-uncased"

#: A real Hugging Face revision SHA for distilbert-base-uncased. Pinned rather
#: than invented because the base reference is the reproducibility contract, and
#: a fabricated SHA on the page would be a claim that resolves to nothing --
#: anyone who pastes it into huggingface.co should land on the real revision.
BASE_REVISION_SHA = "12040accade4e8a0f71eabdb258fecc2f7b256e3"

#: Placeholder authors. Three, because a one-author history cannot show that the
#: author column is per-commit rather than per-repository.
AUTHORS = ("johndoe", "janedoe", "samdoe")

#: Roughly a distilbert rank-8 adapter over q_lin/v_lin, in float32.
ADAPTER_BYTES = 590_000


@dataclass
class Step:
    """One commit to create: what it says, where it sits, what it scored."""

    key: str
    branch: str
    parent: str | None
    message: str
    author: str
    timestamp: str
    accuracy: float | None
    f1: float | None
    rank: int = 8
    epochs: int = 3
    dataset: str = "./datasets/sst2"
    #: Reuse another step's weights byte-for-byte. Two commits, one blob.
    weights_of: str | None = None
    notes: dict = field(default_factory=dict)


# The history, oldest first. Read the `parent` column top to bottom and the
# shape falls out:
#
#   baseline ── epochs ── frozen-head ─┬─ dropout ── re-record   (main)
#                                      ├─ rank-16 ── warmup      (wide-rank)
#                                      └─ 4bit                   (probe)
#
# Three properties the dashboard needs and a straight line cannot produce:
#
#   * two branches forking from the *same* commit, so the rail has to hold two
#     open lanes at once rather than one at a time;
#   * a fork point that is not the tip, so a lane passes *through* rows that
#     belong to another branch;
#   * two commits sharing one blob (`re-record`), which is the metadata-
#     corruption defect turned into a visible property: identical weights, two
#     distinct commits, both readable. Eight commits reference seven distinct
#     adapter blobs because of that row.
STEPS = [
    Step(
        key="baseline",
        branch="main",
        parent=None,
        message="SST-2 baseline, rank 8",
        author=AUTHORS[0],
        timestamp="2026-07-28T09:12:04+00:00",
        accuracy=0.871,
        f1=0.869,
    ),
    Step(
        key="epochs",
        branch="main",
        parent="baseline",
        message="three epochs instead of one",
        author=AUTHORS[0],
        timestamp="2026-07-28T11:40:31+00:00",
        accuracy=0.903,
        f1=0.902,
        epochs=3,
    ),
    Step(
        key="frozen-head",
        branch="main",
        parent="epochs",
        message="freeze the classifier head",
        author=AUTHORS[1],
        timestamp="2026-07-29T14:05:58+00:00",
        accuracy=0.889,
        f1=0.887,
        notes={"freeze_classifier": True},
    ),
    Step(
        key="rank-16",
        branch="wide-rank",
        parent="frozen-head",
        message="rank 16, alpha 32",
        author=AUTHORS[1],
        timestamp="2026-07-30T10:22:17+00:00",
        accuracy=0.911,
        f1=0.910,
        rank=16,
    ),
    Step(
        key="4bit",
        branch="probe",
        parent="frozen-head",
        message="probe: 4-bit base, same adapter shape",
        author=AUTHORS[2],
        timestamp="2026-07-30T16:48:02+00:00",
        accuracy=0.864,
        f1=0.861,
        notes={"load_in_4bit": True},
    ),
    Step(
        key="warmup",
        branch="wide-rank",
        parent="rank-16",
        message="rank 16 with 200 warmup steps",
        author=AUTHORS[1],
        timestamp="2026-07-31T09:03:44+00:00",
        accuracy=0.918,
        f1=0.917,
        rank=16,
        notes={"warmup_steps": 200},
    ),
    Step(
        key="dropout",
        branch="main",
        parent="frozen-head",
        message="lora dropout 0.1",
        author=AUTHORS[0],
        timestamp="2026-07-31T15:19:26+00:00",
        accuracy=0.907,
        f1=0.906,
        notes={"lora_dropout": 0.1},
    ),
    Step(
        key="re-record",
        branch="main",
        parent="dropout",
        message="re-record metadata: dataset path was wrong",
        author=AUTHORS[0],
        timestamp="2026-08-01T08:31:09+00:00",
        accuracy=0.907,
        f1=0.906,
        dataset="./datasets/sst2-v2",
        weights_of="dropout",
        notes={"lora_dropout": 0.1},
    ),
]


def synthetic_adapter(key: str) -> bytes:
    """Deterministic stand-in weights, seeded by the step name.

    Seeded so that re-running the script produces byte-identical blobs and
    therefore identical hashes: the URLs in a rehearsed demo survive a rebuild,
    and a diff of two runs of this script is empty.

    High-entropy on purpose. Compressible filler would make the stored size a
    lie about what an adapter costs, and size is one of the project's claims.
    """
    return random.Random(f"aethel-demo-{key}").randbytes(ADAPTER_BYTES)


def training_info(step: Step) -> dict:
    """The provenance record a real `aethel train` writes beside the weights."""
    metrics = {"train_loss": round(0.62 - 0.35 * (step.accuracy or 0.8), 4)}
    if step.accuracy is not None:
        metrics["eval_accuracy"] = step.accuracy
    if step.f1 is not None:
        metrics["eval_f1_macro"] = step.f1

    return {
        "model_id": BASE_MODEL_ID,
        "revision_sha": BASE_REVISION_SHA,
        "task_type": "SEQ_CLS",
        "num_labels": 2,
        "dataset": step.dataset,
        "dataset_file": f"{step.dataset}/train.csv",
        "target_modules": ["q_lin", "v_lin"],
        "lora_rank": step.rank,
        "lora_alpha": step.rank * 2,
        "batch_size": 16,
        "epochs": step.epochs,
        "seed": 42,
        "metrics": metrics,
        "timestamp": step.timestamp,
        **step.notes,
    }


def stage(workspace: Path, step: Step, weights: bytes) -> None:
    """Write the workspace exactly as a training run leaves it."""
    import json

    workspace.mkdir(parents=True, exist_ok=True)

    (workspace / "adapter_model.safetensors").write_bytes(weights)
    (workspace / "adapter_config.json").write_text(
        json.dumps(
            {
                "peft_type": "LORA",
                "task_type": "SEQ_CLS",
                "r": step.rank,
                "lora_alpha": step.rank * 2,
                "lora_dropout": step.notes.get("lora_dropout", 0.05),
                "target_modules": ["q_lin", "v_lin"],
                "base_model_name_or_path": BASE_MODEL_ID,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "training_info.json").write_text(
        json.dumps(training_info(step), indent=2), encoding="utf-8"
    )


def build_local_repo(root: Path) -> tuple[Repo, dict[str, str], dict[str, str]]:
    """Create the working repository and commit the whole history into it.

    Returns the repo, ``step key -> commit hash``, and ``branch -> tip hash``.

    Branches are created by pointing a ref at the parent and checking it out
    before committing, which is what `aethel branch` plus `aethel checkout` do.
    Nothing here reaches around the ref layer: a fork in the seed data has to be
    a fork the tool itself can make.
    """
    repo = Repo.create(
        root,
        {
            "model_id": BASE_MODEL_ID,
            "revision_sha": BASE_REVISION_SHA,
            "author": AUTHORS[0],
        },
    )

    base_hash = repo.objects.write_json(
        "bases", build_base_object(BASE_MODEL_ID, BASE_REVISION_SHA)
    )

    commits: dict[str, str] = {}
    weights: dict[str, bytes] = {}

    for step in STEPS:
        if step.weights_of:
            weights[step.key] = weights[step.weights_of]
        else:
            weights[step.key] = synthetic_adapter(step.key)

        # Put HEAD where this commit's parent is. A new branch is a ref at the
        # parent; an existing one is already there.
        if step.parent is None:
            branch = "main"
        else:
            parent_hash = commits[step.parent]
            if not repo.refs.branch_exists(step.branch):
                repo.refs.create_branch(step.branch, parent_hash)
            branch = step.branch

        repo.refs.set_head_to_branch(branch)

        stage(repo.workspace_dir, step, weights[step.key])
        commits[step.key] = create_commit(
            repo,
            message=step.message,
            author=step.author,
            base_hash=base_hash,
            training_info=training_info(step),
            timestamp=step.timestamp,
        )

    tips = {name: repo.refs.read_branch(name) for name in repo.refs.list_branches()}

    return repo, commits, tips


def publish(repo: Repo, tips: dict[str, str], data_dir: Path) -> dict:
    """Push every branch to a Hub over its real API, in-process.

    A TestClient rather than a socket: it runs the actual ASGI app, so requests
    go through hash verification, the size ceiling, and the log append. What a
    TCP connection would add is not what this needs to be right about.

    Branch order is sorted for reproducibility. It changes the log's leaf order,
    and therefore the Merkle root, so an unordered dict would give two runs of
    this script two different roots for the same history.
    """
    from fastapi.testclient import TestClient

    from hub.app import create_app
    from hub.config import HubConfig

    config = HubConfig(
        data_dir=data_dir,
        host="127.0.0.1",
        port=0,
        push_token=None,
        chain_rpc_url=None,
        chain_id=None,
        anchor_contract=None,
        pinning_endpoint=None,
        ipfs_gateway=None,
    )

    result = {}
    with TestClient(create_app(config)) as client:
        for branch in sorted(tips):
            plan = build_push_plan(repo, branch, tips[branch])

            for kind in UPLOAD_ORDER:
                hashes = (
                    plan.commit_order
                    if kind == "commits"
                    else sorted(plan.objects.get(kind, set()))
                )
                for object_hash in hashes:
                    data = repo.objects.path_for(kind, object_hash).read_bytes()
                    response = client.put(f"/api/v1/{kind}/{object_hash}", content=data)
                    if response.status_code != 200:
                        raise SystemExit(
                            f"upload of {kind}/{object_hash[:12]} failed: "
                            f"{response.status_code} {response.text}"
                        )

            response = client.post(
                f"/api/v1/repos/{REPO_NAME}/refs",
                json={"branch": branch, "commit": tips[branch]},
            )
            if response.status_code != 200:
                raise SystemExit(
                    f"ref update for {branch} failed: {response.status_code} {response.text}"
                )
            result = response.json()

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed a Hub store with a branching demo history."
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=REPO_ROOT / ".demo",
        help="Directory to build in. Holds hub-data/ and work/. Default: ./.demo",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete an existing directory first instead of refusing.",
    )
    args = parser.parse_args()

    target = args.dir.expanduser().resolve()

    if target.exists():
        if not args.force:
            print(f"{target} already exists. Pass --force to rebuild it.", file=sys.stderr)
            return 1
        # Guarded because --force is a recursive delete of a path from argv.
        # Refusing anything that does not look like a store this script built
        # is cheap; recovering someone's home directory is not.
        if not (target / "hub-data").exists() and any(target.iterdir()):
            print(
                f"{target} is not empty and holds no hub-data/. Refusing to delete it.",
                file=sys.stderr,
            )
            return 1
        shutil.rmtree(target)

    work = target / "work"
    data_dir = target / "hub-data"
    work.mkdir(parents=True)

    repo, commits, tips = build_local_repo(work)
    published = publish(repo, tips, data_dir)

    print(f"Seeded {len(commits)} commits across {len(tips)} branches into {data_dir}")
    for branch in sorted(tips):
        print(f"  {branch:<10} {tips[branch][:12]}")
    print(f"  log size   {published.get('log_size')}")
    print(f"  log root   {published.get('root')}")
    print()
    print("Serve it with:")
    print(f"  AETHEL_HUB_DATA={data_dir} python -m hub")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
