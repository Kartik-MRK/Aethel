"""Shared fixtures for the Aethel test suite.

These tests avoid torch/transformers entirely. The VCS layer is pure stdlib,
so the whole core suite runs without the [ml] extra installed.
"""

import json
from pathlib import Path

import pytest

# Byte patterns used as stand-in adapter weights. Content is irrelevant to the
# VCS layer -- what matters is that ADAPTER_BYTES_A is byte-identical between
# commits, which is what triggers content-addressed deduplication.
ADAPTER_BYTES_A = b"\x00\x01aethel-fake-adapter-A" + bytes(range(256)) * 4
ADAPTER_BYTES_B = b"\x00\x01aethel-fake-adapter-B" + bytes(range(255, -1, -1)) * 4

BASE_MODEL_ID = "distilbert-base-uncased"
BASE_REVISION_SHA = "b" * 40

TRAINING_INFO = {
    "dataset": "./datasets/sst2",
    "dataset_file": "./datasets/sst2/train.csv",
    "num_labels": 2,
    "lora_rank": 8,
    "lora_alpha": 16,
    "batch_size": 2,
    "epochs": 1,
    "model_id": BASE_MODEL_ID,
    "revision_sha": BASE_REVISION_SHA,
    "task_type": "SEQ_CLS",
    "target_modules": ["q_lin", "v_lin"],
    "metrics": {"train_loss": 0.42, "eval_accuracy": 0.87},
    "timestamp": "2026-01-01T00:00:00",
}


@pytest.fixture
def core_repo(tmp_path, monkeypatch):
    """An initialized repository, with the CWD moved into it.

    Built directly rather than by running `aethel init`, because init resolves
    a revision SHA from the Hugging Face Hub. Tests must not need the network.

    The chdir is load-bearing: commands call `Repo.discover()`, which starts
    from the current working directory.
    """
    from aethel.core.repo import Repo

    monkeypatch.chdir(tmp_path)

    return Repo.create(
        tmp_path,
        {
            "model_id": BASE_MODEL_ID,
            "revision_sha": BASE_REVISION_SHA,
            "author": "test-author",
        },
    )


@pytest.fixture
def base_hash(core_repo):
    """A stored base-model reference object."""
    from aethel.core.commits import build_base_object

    return core_repo.objects.write_json(
        "bases", build_base_object(BASE_MODEL_ID, BASE_REVISION_SHA)
    )


def stage_adapter(repo_path: Path, adapter_bytes: bytes) -> None:
    """Write adapter artifacts into the workspace, as `aethel train` would."""
    workspace = Path(repo_path) / ".aethel" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    (workspace / "adapter_model.safetensors").write_bytes(adapter_bytes)
    (workspace / "adapter_config.json").write_text(
        json.dumps({"r": 8, "lora_alpha": 16, "target_modules": ["q_lin", "v_lin"]}),
        encoding="utf-8",
    )
    (workspace / "training_info.json").write_text(
        json.dumps(TRAINING_INFO, indent=2), encoding="utf-8"
    )


def make_commit(core_repo, base_hash, message, adapter_bytes, *, timestamp=None):
    """Stage an adapter and commit it via the core API. Returns the hash."""
    from aethel.core.commits import create_commit

    stage_adapter(core_repo.root, adapter_bytes)

    return create_commit(
        core_repo,
        message=message,
        author="test-author",
        base_hash=base_hash,
        training_info=TRAINING_INFO,
        timestamp=timestamp,
    )


def read_commit_metadata(commit_hash: str, repo_path: Path | None = None) -> dict:
    """Retrieve a commit's own metadata, the way the system retrieves it.

    The single point of coupling between the tests and the storage layout, so
    the tests that use it read the same before and after a storage change.
    """
    from aethel.core.commits import read_commit
    from aethel.core.repo import Repo

    root = Path(repo_path) if repo_path is not None else Path.cwd()
    return read_commit(Repo.discover(root), commit_hash)


def commit_hashes_in_order(repo_path: Path, branch: str = "main") -> list[str]:
    """Return a branch's commit hashes, oldest first.

    Walks the object store via refs. There is no index to consult -- the old
    implementation read this ordering out of SQLite.
    """
    from aethel.core.commits import walk_history
    from aethel.core.repo import Repo

    repo = Repo.discover(repo_path)
    tip = repo.refs.read_branch(branch)

    return [h for h, _ in walk_history(repo, tip)][::-1]


def stored_blob_count(repo_path: Path) -> int:
    """Count distinct stored blobs (deduplicated payloads)."""
    from aethel.core.repo import Repo

    return len(list(Repo.discover(repo_path).objects.iter_hashes("blobs")))


# ---------------------------------------------------------------------------
# Hub fixtures
#
# The Hub is tested in-process: an ASGI app over a temporary data directory,
# with no socket and no uvicorn. Everything above the transport -- hash
# verification, auth, the log, the templates -- is the same code a real server
# runs, and the parts a TCP stack would add are not what these tests are about.
# ---------------------------------------------------------------------------


@pytest.fixture
def hub_config(tmp_path):
    """Hub configuration pointed at a temporary directory.

    Every environment-backed field is set explicitly. A developer with
    AETHEL_CHAIN_RPC or AETHEL_HUB_TOKEN exported must not change what these
    tests assert -- a suite whose results depend on the shell it was launched
    from is not a suite.
    """
    from hub.config import HubConfig

    return HubConfig(
        data_dir=tmp_path / "hub-data",
        host="127.0.0.1",
        port=0,
        max_blob_bytes=1024 * 1024,
        push_token=None,
        chain_rpc_url=None,
        chain_id=None,
        anchor_contract=None,
        pinning_endpoint=None,
    )


def build_hub_client(config):
    """A TestClient over a Hub built on `config`, as a context manager.

    Entering it runs the lifespan, which is what populates `app.state` with the
    storage, log and anchor store. Requests made without that would fail on a
    missing attribute rather than on anything meaningful.
    """
    pytest.importorskip("fastapi", reason="the Hub needs the [hub] extra")

    from fastapi.testclient import TestClient

    from hub.app import create_app

    return TestClient(create_app(config))


@pytest.fixture
def hub_client(hub_config):
    """An empty Hub, ready to receive uploads."""
    with build_hub_client(hub_config) as client:
        yield client


@pytest.fixture
def hub_storage(hub_config):
    """Direct access to the Hub's store, for assertions and for tampering."""
    from hub.storage import HubStorage

    return HubStorage(hub_config.data_dir)


@pytest.fixture
def hub_log(hub_config):
    """Direct access to the Hub's transparency log."""
    from hub.log import TransparencyLog

    return TransparencyLog(hub_config.log_path)


def upload_objects(client, repo, plan, *, headers=None):
    """Upload every object in a push plan, in dependency order.

    Sends the stored bytes verbatim, exactly as `aethel push` does: the hash in
    the URL is the hash of what is on disk, so re-serializing here would make
    the tests upload something the Hub is right to reject.
    """
    from aethel.remote.objects import UPLOAD_ORDER

    for kind in UPLOAD_ORDER:
        hashes = (
            plan.commit_order if kind == "commits" else sorted(plan.objects.get(kind, set()))
        )
        for object_hash in hashes:
            data = repo.objects.path_for(kind, object_hash).read_bytes()
            response = client.put(
                f"/api/v1/{kind}/{object_hash}", content=data, headers=headers or {}
            )
            assert response.status_code == 200, response.text


@pytest.fixture
def pushed(hub_client, core_repo, base_hash):
    """A Hub holding one repository, `demo`, with two commits on `main`.

    Built by running the real plan-and-upload path rather than by writing files
    into the Hub's store directly: a fixture that bypassed the API would test
    the assertions against a state the API cannot actually produce.

    Lives here rather than in one test module because both the API tests and the
    security tests need a Hub with pages worth rendering -- an empty Hub has no
    repository page and no commit page, so half the response surface would go
    unexercised.
    """
    from aethel.remote.objects import build_push_plan

    first = make_commit(core_repo, base_hash, "first version", ADAPTER_BYTES_A)
    second = make_commit(core_repo, base_hash, "second version", ADAPTER_BYTES_B)

    plan = build_push_plan(core_repo, "main", second)
    upload_objects(hub_client, core_repo, plan)

    response = hub_client.post(
        "/api/v1/repos/demo/refs", json={"branch": "main", "commit": second}
    )
    assert response.status_code == 200, response.text

    return {
        "client": hub_client,
        "repo": core_repo,
        "plan": plan,
        "first": first,
        "second": second,
        "ref": response.json(),
    }
