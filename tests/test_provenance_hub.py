"""Historical proof delivery and truthful dashboard failure states."""

import asyncio
import hashlib
import time

import pytest

pytest.importorskip("fastapi")

from hub.log import AnchorStore
from hub.provenance import ProvenanceMonitor


@pytest.fixture(autouse=True)
def no_external_probes(monkeypatch):
    async def wait(_self):
        await asyncio.Event().wait()

    monkeypatch.setattr(ProvenanceMonitor, "run", wait)


def test_log_growth_preserves_old_bundle_and_prefix_health(pushed, hub_log, hub_config):
    root, size = hub_log.root(), hub_log.size()
    AnchorStore(hub_config.anchors_path).append({"root": root, "size": size, "status": "confirmed"})
    hub_log.append(hashlib.sha256(b"newer").hexdigest(), "demo", "later")
    response = pushed["client"].get(f"/api/v1/checkpoints/{size}/bundle/{pushed['first']}")
    assert response.status_code == 200
    assert response.json()["proof"]["log_size"] == size
    assert response.json()["proof"]["root"] == root
    assert len(response.json()["leaves"]) == size
    checks = pushed["client"].get("/api/v1/health").json()["checks"]
    row = next(check for check in checks if check["name"] == "Log vs anchored root")
    assert row["status"] == "good"
    assert "1 entries added" in row["detail"]
    assert pushed["client"].get("/api/v1/log").json()["current_root_anchored"] is False


def test_wrong_recorded_root_blocks_bundle(pushed, hub_log, hub_config):
    size = hub_log.size()
    AnchorStore(hub_config.anchors_path).append({"root": "a" * 64, "size": size, "status": "confirmed"})
    response = pushed["client"].get(f"/api/v1/checkpoints/{size}/bundle/{pushed['first']}")
    assert response.status_code == 409


def test_historical_proof_query_and_invalid_size(pushed, hub_log):
    client = pushed["client"]
    old = client.get(f"/api/v1/log/proof/{pushed['first']}?size=1")
    assert old.status_code == 200
    assert old.json()["log_size"] == 1
    assert client.get(f"/api/v1/log/proof/{pushed['second']}?size=1").status_code == 404
    assert client.get(f"/api/v1/log/proof/{pushed['first']}?size=1000").status_code == 400


def test_dashboard_shows_unconfigured_and_unavailable_without_false_success(hub_client):
    page = hub_client.get("/provenance")
    assert page.status_code == 200
    assert "No checkpoint published" in page.text
    assert "No adapter copies pinned" in page.text
    monitor = hub_client.app.state.provenance
    monitor.chain = {"status": "unavailable", "detail": "RPC timeout"}
    page = hub_client.get("/provenance")
    assert 'data-state="unavailable"' in page.text
    assert "RPC timeout" in page.text
    assert "raw_transaction" not in page.text


def test_stale_verification_loses_confirmed_label(hub_client):
    monitor = hub_client.app.state.provenance
    monitor.chain = {"status": "confirmed", "detail": "checked", "size": 3}
    monitor.gateway = {"status": "verified", "detail": "checked"}
    monitor.checked = time.monotonic() - 1000
    data = hub_client.get("/api/v1/provenance").json()
    assert data["chain"]["status"] == "stale"
    assert data["gateway"]["status"] == "stale"


def test_unknown_mirror_is_404_and_bad_hash_is_400(hub_client):
    assert hub_client.get("/api/v1/mirrors/" + "a" * 64).status_code == 404
    assert hub_client.get("/api/v1/mirrors/not-a-hash").status_code == 400


def test_log_api_requires_fresh_chain_evidence(pushed, hub_log):
    client = pushed["client"]
    monitor = client.app.state.provenance
    monitor.chain = {"status": "confirmed", "size": hub_log.size(), "root": hub_log.root(), "detail": "checked"}
    assert client.get("/api/v1/log").json()["current_root_anchored"] is True
    assert "On chain" in client.get(f"/c/{pushed['first']}").text
    for status in ("unavailable", "stale", "failed", "pending"):
        monitor.chain = {"status": status, "detail": "No fresh chain confirmation"}
        assert client.get("/api/v1/log").json()["current_root_anchored"] is False
        page = client.get(f"/c/{pushed['first']}")
        assert page.status_code == 200
        assert f'data-state="{status}"' in page.text
        assert f"Chain check {status}" in page.text
        assert "Not yet anchored" not in page.text
        assert "On chain" not in page.text

    monitor.chain = {"status": "confirmed", "size": hub_log.size(), "root": hub_log.root(), "detail": "checked"}
    hub_log.append(hashlib.sha256(b"after checkpoint").hexdigest(), "demo", "later")
    page = client.get(f"/c/{pushed['first']}")
    assert "Confirmed in 2-entry checkpoint" in page.text
    assert "Not yet anchored" in page.text
    assert "On chain" not in page.text
