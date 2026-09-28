"""Mirror failure and retry behavior with controlled gateway responses."""

import hashlib

import httpx
import pytest

from aethel.core.objects import ObjectStore
from aethel.provenance.ipfs import (
    MirrorError,
    PinningClient,
    mirror_blob,
    retrieve_verified,
    validate_cid,
)
from aethel.provenance.state import StateStore

CID = "bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku"
DATA = b"adapter bytes"
DIGEST = hashlib.sha256(DATA).hexdigest()


def test_corrupt_gateway_falls_back_and_never_replaces_existing_file(tmp_path):
    target = tmp_path / "blob"
    target.write_bytes(b"old")
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=DATA if request.url.host == "valid.example" else b"corrupt")

    transport = httpx.MockTransport(respond)
    with pytest.raises(MirrorError, match="No gateway"):
        retrieve_verified(CID, DIGEST, ["https://bad.example/ipfs"], target, transport=transport)
    assert target.read_bytes() == b"old"
    result = retrieve_verified(CID, DIGEST, ["https://bad.example/ipfs", "https://valid.example/ipfs"], target, transport=transport)
    assert target.read_bytes() == DATA
    assert result["sha256"] == DIGEST
    assert len(requests) == 3


def test_oversized_response_and_redirect_are_rejected(tmp_path):
    for response in (httpx.Response(200, content=DATA), httpx.Response(302, headers={"Location": "https://other.example"})):
        with pytest.raises(MirrorError):
            retrieve_verified(CID, DIGEST, ["https://bad.example/ipfs"], tmp_path / "blob", max_bytes=2, transport=httpx.MockTransport(lambda _, response=response: response))
        assert not (tmp_path / "blob").exists()


@pytest.mark.parametrize("cid", ["../../etc/passwd", CID + "?x=1", "https://example.org", "", None])
def test_invalid_cids_never_become_gateway_paths(cid):
    with pytest.raises(MirrorError):
        validate_cid(cid)


def test_failed_retrieval_retains_cid_and_retry_does_not_pin_again(tmp_path):
    objects = ObjectStore(tmp_path / "objects")
    digest = objects.write_blob(DATA)
    state = StateStore(tmp_path / "state.sqlite3")
    pins = []

    def pin(request):
        pins.append(request)
        return httpx.Response(200, json={"IpfsHash": CID})

    client = PinningClient("https://api.pinata.cloud", token="private-token", transport=httpx.MockTransport(pin))
    failed = mirror_blob(state, objects, digest, client, ["https://gateway.example/ipfs"], transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    assert failed["status"] == "failed" and failed["cid"] == CID
    reopened = StateStore(state.path)
    passed = mirror_blob(reopened, objects, digest, client, ["https://gateway.example/ipfs"], transport=httpx.MockTransport(lambda _: httpx.Response(200, content=DATA)))
    assert passed["status"] == "verified" and passed["attempts"] == 2
    assert len(pins) == 1
    assert "private-token" not in str(reopened.records("mirrors"))


def test_pinning_errors_do_not_leak_tokens_or_provider_body(tmp_path):
    blob = tmp_path / "blob"
    blob.write_bytes(DATA)
    client = PinningClient("https://api.pinata.cloud", token="secret", transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret")))
    with pytest.raises(MirrorError, match="HTTP 401") as error:
        client.pin(blob, DIGEST)
    assert "secret" not in str(error.value)


def test_kubo_uses_pinned_cid_v1(tmp_path):
    blob = tmp_path / "blob"
    blob.write_bytes(DATA)

    def handler(request):
        assert request.url.path == "/api/v0/add"
        assert request.url.params["pin"] == "true"
        assert request.url.params["cid-version"] == "1"
        return httpx.Response(200, json={"Hash": CID})

    client = PinningClient("http://127.0.0.1:5001", backend="kubo", transport=httpx.MockTransport(handler))
    assert client.pin(blob, DIGEST) == CID
