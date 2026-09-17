"""Execute the compiled contract on a real local EVM and verify its receipts."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("web3")
pytest.importorskip("eth_tester")

from eth_tester import EthereumTester, PyEVMBackend
from web3 import EthereumTesterProvider, Web3

from aethel.core.aggregator import MerkleTree
from aethel.provenance.chain import ChainClient, artifact
from aethel.provenance.checkpoint import CheckpointError, verify_bundle, verify_position
from aethel.provenance.state import StateStore
from hub.log import AnchorStore, TransparencyLog


def hashes(count):
    return [hashlib.sha256(str(i).encode()).hexdigest() for i in range(count)]


@pytest.fixture
def chain(tmp_path):
    backend = PyEVMBackend()
    tester = EthereumTester(backend=backend)
    w3 = Web3(EthereumTesterProvider(tester))
    key = backend.account_keys[0].to_hex()
    store = StateStore(tmp_path / "state.sqlite3")
    client = ChainClient(None, w3.eth.chain_id, None, "ab" * 32, confirmations=1, web3=w3)
    deployed = client.deploy(key, store)
    assert deployed["status"] == "confirmed"
    assert "raw_transaction" not in deployed
    client.address = deployed["contract"]
    log = TransparencyLog(tmp_path / "log.jsonl")
    anchors = AnchorStore(tmp_path / "anchors.jsonl")
    return client, key, store, log, anchors, tester


def test_compiled_artifact_matches_source():
    source = Path(__file__).parents[1] / "contracts" / "AethelCheckpoint.sol"
    assert artifact()["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_deploy_publish_retry_and_historical_inclusion(chain):
    client, key, store, log, anchors, _ = chain
    values = hashes(7)
    log.append_many(values[:3], "demo", "now")
    first = client.publish(log, anchors, store, key)
    assert first["size"] == 3 and first["history_checked"] == 1
    transaction_count = client.w3.eth.get_transaction_count(client.w3.eth.accounts[0])
    retried = client.publish(log, anchors, store, key)
    assert retried["root"] == first["root"]
    assert client.w3.eth.get_transaction_count(client.w3.eth.accounts[0]) == transaction_count
    log.append_many(values[3:], "demo", "later")
    proof = log.inclusion_proof(values[2], size=3)
    assert verify_bundle({"checkpoint": first, "proof": proof, "leaves": values[:3]}, values[2], client)["status"] == "confirmed"
    second = client.publish(log, anchors, store, key)
    assert second["history_checked"] == 2
    assert second["previous_size"] == 3
    assert len(anchors.records()) == 2
    assert client.verify(3, first["root"], leaves=values)["status"] == "confirmed"


def test_contract_rejects_rewrites_wrong_owner_and_stale_predecessor(chain):
    client, key, store, log, anchors, _ = chain
    log.append_many(hashes(2), "demo", "now")
    record = client.publish(log, anchors, store, key)
    contract = client.contract()
    calls = [
        (contract.functions.publish(2, bytes.fromhex(record["root"]), 2), client.w3.eth.accounts[0]),
        (contract.functions.publish(3, b"a" * 32, 0), client.w3.eth.accounts[0]),
        (contract.functions.publish(3, b"a" * 32, 2), client.w3.eth.accounts[1]),
    ]
    for function, sender in calls:
        with pytest.raises(Exception, match="revert"):
            function.transact({"from": sender})


def test_independent_verifier_rejects_wrong_trust_and_rewrite(chain):
    client, key, store, log, anchors, _ = chain
    leaves = hashes(4)
    log.append_many(leaves, "demo", "now")
    record = client.publish(log, anchors, store, key)
    for field, value in (("chain_id", 1), ("log_id", "cd" * 32), ("address", client.w3.eth.accounts[1])):
        wrong = copy.copy(client)
        setattr(wrong, field, value)
        with pytest.raises(CheckpointError):
            wrong.verify(4, record["root"], leaves=leaves)
    altered = ["f" * 64, *leaves[1:]]
    with pytest.raises(CheckpointError, match="differs"):
        client.verify(4, record["root"], leaves=altered)


def test_later_root_cannot_hide_an_inconsistent_old_prefix(chain):
    client, key, store, log, anchors, _ = chain
    leaves = hashes(4)
    log.append_many(leaves[:2], "demo", "now")
    client.publish(log, anchors, store, key)
    altered = ["f" * 64, *leaves[1:]]
    malicious_root = MerkleTree(altered).get_root()
    client.contract().functions.publish(4, bytes.fromhex(malicious_root), 2).transact({"from": client.w3.eth.accounts[0]})
    with pytest.raises(CheckpointError, match="earlier checkpoint"):
        client.verify(4, malicious_root, leaves=altered)


def test_confirmation_threshold_and_reorg_are_not_reported_confirmed(chain):
    client, key, store, log, anchors, tester = chain
    log.append_many(hashes(2), "demo", "now")
    snapshot = tester.take_snapshot()
    record = client.publish(log, anchors, store, key)
    client.confirmations = 3
    with pytest.raises(CheckpointError, match="pending"):
        client.verify(2, record["root"])
    tester.mine_blocks(2)
    assert client.verify(2, record["root"])["confirmations"] == 3
    tester.revert_to_snapshot(snapshot)
    with pytest.raises(CheckpointError, match="absent"):
        client.verify(2, record["root"])


def test_deployment_retry_does_not_deploy_twice(chain):
    client, key, store, _, _, _ = chain
    count = client.w3.eth.get_transaction_count(client.w3.eth.accounts[0])
    deployed = client.deploy(key, store)
    assert deployed["contract"] == client.address
    assert client.w3.eth.get_transaction_count(client.w3.eth.accounts[0]) == count


def test_pending_checkpoint_is_resumed_after_confirmations(chain):
    client, key, store, log, anchors, tester = chain
    log.append_many(hashes(2), "demo", "now")
    client.confirmations = 2
    first = client.publish(log, anchors, store, key)
    assert first["status"] == "pending"
    second = client.publish(log, anchors, store, key)
    assert second["status"] == "pending"
    tester.mine_blocks(1)
    confirmed = client.publish(log, anchors, store, key)
    assert confirmed["status"] == "confirmed"
    assert confirmed["tx_hash"] == first["tx_hash"]
    assert len(anchors.records()) == 1


def test_accepted_broadcast_timeout_does_not_send_another_payment(chain, monkeypatch):
    client, key, store, log, anchors, _ = chain
    log.append_many(hashes(2), "demo", "now")
    send = client.w3.eth.send_raw_transaction

    def accepted_then_timeout(raw):
        send(raw)
        raise TimeoutError("acknowledgement lost")

    monkeypatch.setattr(client.w3.eth, "send_raw_transaction", accepted_then_timeout)
    first = client.publish(log, anchors, store, key)
    assert first["status"] == "pending"
    count = client.w3.eth.get_transaction_count(client.w3.eth.accounts[0])
    monkeypatch.setattr(client.w3.eth, "send_raw_transaction", send)
    second = client.publish(log, anchors, store, key)
    assert second["status"] == "confirmed"
    assert second["tx_hash"] == first["tx_hash"]
    assert client.w3.eth.get_transaction_count(client.w3.eth.accounts[0]) == count


def test_owner_rotation_requires_acceptance_and_revokes_old_owner(chain):
    client, key, store, log, anchors, tester = chain
    contract = client.contract()
    old, new = client.w3.eth.accounts[:2]
    contract.functions.proposeOwner(new).transact({"from": old})
    assert contract.functions.owner().call() == old
    with pytest.raises(Exception, match="revert"):
        contract.functions.acceptOwnership().transact({"from": old})
    contract.functions.acceptOwnership().transact({"from": new})
    log.append_many(hashes(2), "demo", "now")
    with pytest.raises(CheckpointError, match="not the checkpoint contract owner"):
        client.publish(log, anchors, store, key)
    assert client.publish(log, anchors, store, tester.backend.account_keys[1].to_hex())["status"] == "confirmed"


@pytest.mark.parametrize("count", [1, 2, 3, 5, 7, 8, 15])
def test_position_verification_binds_tree_size_and_leaf_index(tmp_path, count):
    log = TransparencyLog(tmp_path / "log")
    values = hashes(count)
    log.append_many(values, "demo", "now")
    for value in values:
        proof = log.inclusion_proof(value)
        assert verify_position(proof, value, log.root(), count)
        bad = json.loads(json.dumps(proof))
        bad["leaf_index"] = count
        assert not verify_position(bad, value, log.root(), count)
        assert not verify_position(proof, value, log.root(), count + 1)
