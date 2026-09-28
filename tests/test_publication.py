"""Publication acceptance, ancestry, concurrent updates, and retry behavior."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException

from aethel.core.errors import CorruptObject
from aethel.remote.http import HubClient
from aethel.remote.objects import build_push_plan
from hub.api import update_ref
from hub.storage import RefConflict
from tests.conftest import ADAPTER_BYTES_A, ADAPTER_BYTES_B, make_commit


def upload(repo, storage, tip):
    plan = build_push_plan(repo, "main", tip)
    for kind in plan.kinds():
        for digest in plan.objects[kind]:
            raw = repo.objects.path_for(kind, digest).read_bytes()
            if kind == "blobs":
                storage.put_blob(digest, raw)
            else:
                storage.put_json_object(kind, digest, raw)


def publish(storage, log, tip, **payload):
    return asyncio.run(
        update_ref("demo", {"branch": "main", "commit": tip, **payload}, storage, log)
    )


@pytest.fixture
def history(core_repo, base_hash, hub_storage):
    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)
    upload(core_repo, hub_storage, second)
    return first, second


@pytest.mark.parametrize("kind,field", [("trees", "tree"), ("bases", "base"), ("blobs", "adapter_blob")])
def test_missing_dependency_never_publishes_or_logs(history, hub_storage, hub_log, kind, field):
    tip = history[-1]
    commit = hub_storage.read_commit(tip)
    hub_storage.objects.path_for(kind, commit[field]).unlink()
    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, tip)
    assert exc.value.status_code == 409
    assert hub_storage.repo("demo") is None
    assert hub_log.size() == 0


@pytest.mark.parametrize("kind,field", [("trees", "tree"), ("bases", "base"), ("blobs", "adapter_blob")])
def test_corrupt_dependency_never_publishes(history, hub_storage, hub_log, kind, field):
    commit = hub_storage.read_commit(history[-1])
    hub_storage.objects.path_for(kind, commit[field]).write_bytes(b"corrupt")
    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, history[-1])
    assert exc.value.status_code == 400
    assert hub_storage.repo("demo") is None
    assert hub_log.size() == 0


def test_missing_ancestor_config_is_checked(history, hub_storage, hub_log):
    first = hub_storage.read_commit(history[0])
    files = hub_storage.read_tree(first["tree"])
    hub_storage.objects.path_for("blobs", files["training_info.json"]).unlink()
    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, history[-1])
    assert exc.value.status_code == 409
    assert hub_log.size() == 0


def test_long_history_is_fully_logged(core_repo, base_hash, hub_storage, hub_log):
    hashes = [make_commit(core_repo, base_hash, str(i), ADAPTER_BYTES_A) for i in range(105)]
    upload(core_repo, hub_storage, hashes[-1])
    result = publish(hub_storage, hub_log, hashes[-1])
    assert hub_log.leaves() == hashes
    assert result["logged_indices"] == list(range(105))
    assert len(hub_storage.commit_history(hashes[-1])) == 100
    assert len(hub_storage.commit_history(hashes[-1], limit=None)) == 105


def test_dependency_beyond_display_limit_is_checked(core_repo, base_hash, hub_storage, hub_log):
    hashes = [make_commit(core_repo, base_hash, str(i), ADAPTER_BYTES_A) for i in range(105)]
    upload(core_repo, hub_storage, hashes[-1])
    hub_storage.objects.path_for("commits", hashes[0]).unlink()
    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, hashes[-1])
    assert exc.value.status_code == 409
    assert hub_log.size() == 0


def test_rewind_is_rejected_without_changing_the_log(history, hub_storage, hub_log):
    publish(hub_storage, hub_log, history[-1])
    before = hub_log.leaves()
    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, history[0])
    assert exc.value.status_code == 409
    assert hub_storage.repo("demo").branches["main"] == history[-1]
    assert hub_log.leaves() == before


def test_unrelated_root_cannot_replace_branch(history, hub_storage, hub_log):
    publish(hub_storage, hub_log, history[0])
    other = {**hub_storage.read_commit(history[0]), "message": "unrelated root"}
    digest = hub_storage.objects.write_json("commits", other)
    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, digest)
    assert exc.value.status_code == 409
    assert hub_log.leaves() == [history[0]]


def test_stale_expected_tip_rejects_even_fast_forward(history, hub_storage, hub_log):
    publish(hub_storage, hub_log, history[0])
    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, history[-1], expected_tip=None)
    assert exc.value.status_code == 409
    assert hub_log.leaves() == [history[0]]
    result = publish(hub_storage, hub_log, history[-1], expected_tip=history[0])
    assert result["commit"] == history[-1]


def test_retry_of_same_tip_is_idempotent(history, hub_storage, hub_log):
    publish(hub_storage, hub_log, history[-1], expected_tip=None)
    root = hub_log.root()
    retry = publish(hub_storage, hub_log, history[-1], expected_tip=None)
    assert retry["appended_indices"] == []
    assert retry["root"] == root


def test_two_competing_writers_preserve_one_tip(history, hub_storage, hub_log):
    first, second = history
    publish(hub_storage, hub_log, first)
    sibling = hub_storage.objects.write_json(
        "commits", {**hub_storage.read_commit(second), "message": "competing child"}
    )
    barrier = Barrier(2)

    def writer(tip):
        barrier.wait(timeout=5)
        try:
            hub_storage.publish_branch(
                "demo", "main", tip, log=hub_log, expected_tip=first, check_expected=True
            )
            return tip
        except RefConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(writer, [second, sibling]))
    winners = [tip for tip in outcomes if tip]
    assert len(winners) == 1
    assert hub_storage.repo("demo").branches["main"] == winners[0]
    assert hub_log.leaves() == [first, winners[0]]


def test_ref_write_failure_can_be_retried(history, hub_storage, hub_log, monkeypatch):
    import hub.storage as module

    first, second = history
    publish(hub_storage, hub_log, first)
    original = module.atomic_write_text

    def fail(*args, **kwargs):
        raise OSError("simulated ref write failure")

    monkeypatch.setattr(module, "atomic_write_text", fail)
    with pytest.raises(OSError):
        publish(hub_storage, hub_log, second, expected_tip=first)
    assert hub_storage.repo("demo").branches["main"] == first
    assert hub_log.leaves() == [first, second]
    monkeypatch.setattr(module, "atomic_write_text", original)
    retry = publish(hub_storage, hub_log, second, expected_tip=first)
    assert retry["appended_indices"] == []
    assert hub_storage.repo("demo").branches["main"] == second


@pytest.mark.parametrize("dependency", ["tree", "base", "adapter_config.json", "training_info.json"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_retry_revalidates_accepted_dependencies(history, hub_storage, hub_log, monkeypatch, dependency, damage):
    import hub.storage as module

    first, second = history
    publish(hub_storage, hub_log, first)

    def fail(*args, **kwargs):
        raise OSError("simulated ref write failure")

    with monkeypatch.context() as fault:
        fault.setattr(module, "atomic_write_text", fail)
        with pytest.raises(OSError):
            publish(hub_storage, hub_log, second, expected_tip=first)
    assert hub_storage.repo("demo").branches["main"] == first
    assert hub_log.leaves() == [first, second]
    root = hub_log.root()

    commit = hub_storage.read_commit(second)
    if dependency in ("tree", "base"):
        kind = {"tree": "trees", "base": "bases"}[dependency]
        path = hub_storage.objects.path_for(kind, commit[dependency])
    else:
        files = hub_storage.read_tree(commit["tree"])
        path = hub_storage.objects.path_for("blobs", files[dependency])
    original = path.read_bytes()
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")

    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, second, expected_tip=first)
    assert exc.value.status_code == (409 if damage == "missing" else 400)
    assert hub_storage.repo("demo").branches["main"] == first
    assert hub_log.root() == root
    assert hub_log.leaves() == [first, second]

    path.write_bytes(original)
    retry = publish(hub_storage, hub_log, second, expected_tip=first)
    assert retry["appended_indices"] == []
    assert hub_storage.repo("demo").branches["main"] == second


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_new_child_revalidates_accepted_ancestor(history, hub_storage, hub_log, damage):
    first, second = history
    publish(hub_storage, hub_log, first)
    ancestor = hub_storage.read_commit(first)
    path = hub_storage.objects.path_for("trees", ancestor["tree"])
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")
    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, second, expected_tip=first)
    assert exc.value.status_code == (409 if damage == "missing" else 400)
    assert hub_storage.repo("demo").branches["main"] == first
    assert hub_log.leaves() == [first]


@pytest.mark.parametrize(
    "change",
    [
        {"adapter_blob": "f" * 64},
        {"schema": 99},
        {"parent_hash": 0},
        {"training_info": []},
        {"message": None},
    ],
)
def test_malformed_commit_cannot_be_published(history, hub_storage, hub_log, change):
    payload = {**hub_storage.read_commit(history[0]), **change}
    tip = hub_storage.objects.write_json("commits", payload)
    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, tip)
    assert exc.value.status_code == 400
    assert hub_log.size() == 0


@pytest.mark.parametrize("branch", ["../outside", "feature/x", "main.lock", "CON"])
def test_invalid_branch_never_enters_log(history, hub_storage, hub_log, branch):
    with pytest.raises(HTTPException) as exc:
        publish(hub_storage, hub_log, history[0], branch=branch)
    assert exc.value.status_code == 400
    assert hub_log.size() == 0


def test_embedded_hash_field_cannot_forge_log_leaf(history, hub_storage, hub_log):
    payload = {**hub_storage.read_commit(history[0]), "hash": "f" * 64}
    tip = hub_storage.objects.write_json("commits", payload)
    publish(hub_storage, hub_log, tip)
    assert hub_log.leaves() == [tip]


def test_push_sends_tip_observed_during_negotiation(monkeypatch):
    import httpx

    calls = []
    client = HubClient("http://unused.test")

    def request(method, path, **kwargs):
        calls.append(kwargs.get("json"))
        if path.endswith("negotiate"):
            return httpx.Response(200, json={"missing": {}, "branches": {"main": "a" * 64}})
        return httpx.Response(200, json={"commit": "b" * 64})

    monkeypatch.setattr(client, "_request", request)
    client.negotiate("demo", {})
    client.update_ref("demo", "main", "b" * 64)
    assert calls[-1]["expected_tip"] == "a" * 64


def test_blob_corruption_is_detected_on_read(tmp_path):
    from aethel.core.objects import ObjectStore

    store = ObjectStore(tmp_path)
    digest = store.write_blob(b"original")
    store.path_for("blobs", digest).write_bytes(b"modified")
    with pytest.raises(CorruptObject):
        store.read_blob(digest)
    with pytest.raises(CorruptObject):
        store.blob_path(digest)


@pytest.mark.parametrize("name", ["../outside", "/outside", "a/b", "a\\b", "C:outside", "..", ""])
def test_tree_cannot_write_outside_destination(tmp_path, name):
    from aethel.core.objects import ObjectStore

    store = ObjectStore(tmp_path / "objects")
    blob = store.write_blob(b"payload")
    tree = store.write_json("trees", {"files": {name: blob}})
    with pytest.raises(CorruptObject):
        store.extract_tree(tree, tmp_path / "output")
    assert not (tmp_path / "outside").exists()


def test_extraction_checks_all_blobs_before_replacing_files(tmp_path):
    from aethel.core.objects import ObjectStore

    store = ObjectStore(tmp_path / "objects")
    good = store.write_blob(b"new")
    broken = store.write_blob(b"original")
    tree = store.write_json("trees", {"files": {"a.txt": good, "z.txt": broken}})
    store.path_for("blobs", broken).write_bytes(b"modified")
    output = tmp_path / "output"
    output.mkdir()
    (output / "a.txt").write_bytes(b"old")
    with pytest.raises(CorruptObject):
        store.extract_tree(tree, output)
    assert (output / "a.txt").read_bytes() == b"old"
