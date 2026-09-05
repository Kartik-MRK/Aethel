"""Render measured split identity and distinguish legacy scores."""

import asyncio

import pytest

pytest.importorskip("fastapi")

from starlette.requests import Request

from aethel.core.commits import create_commit
from hub.log import AnchorStore
from hub.views import commit_detail
from tests.conftest import ADAPTER_BYTES_A, stage_adapter
from tests.test_publication import upload


def render(core_repo, base_hash, hub_storage, hub_log, info):
    stage_adapter(core_repo.root, ADAPTER_BYTES_A)
    tip = create_commit(core_repo, message="measured", author="test", base_hash=base_hash, training_info=info)
    upload(core_repo, hub_storage, tip)
    request = Request({"type": "http", "method": "GET", "path": f"/c/{tip}", "headers": [], "state": {"csp_nonce": "test-nonce"}})
    response = asyncio.run(commit_detail(
        tip, request, hub_storage, hub_log, AnchorStore(hub_storage.data_dir / "anchors.jsonl")
    ))
    return response.body.decode()


def test_measured_record_shows_held_out_identity(core_repo, base_hash, hub_storage, hub_log):
    spec = {"hash": "a" * 64, "examples_sha256": "b" * 64, "split": "validation", "sample_count": 80}
    info = {"seed": 42, "split_seed": 11, "evaluation": {"status": "measured", "current": {
        "accuracy": 0.75, "macro_f1": 0.7, "evaluation_spec": spec,
    }}}
    html = render(core_repo, base_hash, hub_storage, hub_log, info)
    assert "Validation split, 80 examples" in html
    assert "Evaluation record" in html
    assert spec["hash"] in html
    assert "75.0%" in html


def test_legacy_metrics_do_not_claim_held_out_evaluation(core_repo, base_hash, hub_storage, hub_log):
    html = render(core_repo, base_hash, hub_storage, hub_log, {"metrics": {"accuracy": 0.99}})
    assert "Dataset split not recorded" in html
    assert "not verified held-out results" in html
