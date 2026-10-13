"""Verified retrieval, preservation on failure, and Hub round trips."""

import json

import pytest
from typer.testing import CliRunner

from aethel.core.errors import AethelError
from aethel.core.hashing import hash_bytes
from aethel.core.objects import OBJECT_KINDS, ObjectStore
from aethel.core.repo import Repo
from aethel.core.workspace import workspace_files
from aethel.main import app
from aethel.remote.http import HubClient, RemoteError
from aethel.remote.sync import HistoryDownload, clone_repository, pull_repository
from tests.conftest import ADAPTER_BYTES_A, ADAPTER_BYTES_B, make_commit


class StoredRemote:
    """A byte transport over another repository, allowing corruption injection."""

    base_url = "http://hub.test"

    def __init__(self, repo):
        self.repo = repo
        self.fetched = []

    def repository(self, name):
        return {branch: self.repo.refs.read_branch(branch) for branch in self.repo.refs.list_branches()}

    def fetch_object(self, kind, digest):
        self.fetched.append((kind, digest))
        yield self.repo.objects.path_for(kind, digest).read_bytes()


@pytest.fixture
def published(core_repo, base_hash, tmp_path):
    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    remote = StoredRemote(core_repo)
    clone = clone_repository(remote, "demo", tmp_path / "copy", branch=None, author="local-author")
    return remote, clone, first


def test_clone_restores_all_branches_and_uses_local_identity(core_repo, base_hash, tmp_path):
    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    core_repo.refs.create_branch("experiment", first)
    second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)
    remote = StoredRemote(core_repo)
    copy = clone_repository(remote, "demo", tmp_path / "copy", branch="experiment", author="teammate")
    assert copy.refs.read_branch("main") == second
    assert copy.refs.read_branch("experiment") == first
    assert copy.refs.require_attached_branch() == "experiment"
    assert (copy.workspace_dir / "adapter_model.safetensors").read_bytes() == ADAPTER_BYTES_A
    assert copy.read_config()["author"] == "teammate"
    assert copy.read_config()["hub_repo"] == "demo"
    assert copy.read_config()["hub_url"] == remote.base_url
    assert len(remote.fetched) == len(set(remote.fetched))
    for kind in OBJECT_KINDS:
        for digest in core_repo.objects.iter_hashes(kind):
            assert copy.objects.path_for(kind, digest).read_bytes() == core_repo.objects.path_for(kind, digest).read_bytes()


@pytest.mark.parametrize("existing", ["dir", "file", "symlink", "dangling"])
def test_clone_refuses_any_existing_destination(core_repo, tmp_path, existing):
    destination = tmp_path / "copy"
    if existing == "dir":
        destination.mkdir()
    elif existing == "file":
        destination.write_text("keep")
    else:
        destination.symlink_to(tmp_path if existing == "symlink" else tmp_path / "missing")
    with pytest.raises(AethelError, match="already exists"):
        clone_repository(StoredRemote(core_repo), "demo", destination, branch=None, author="team")
    assert destination.exists() or destination.is_symlink()


def test_clone_failure_preserves_concurrent_files(core_repo, base_hash, tmp_path, monkeypatch):
    make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    destination = tmp_path / "copy"
    remote = StoredRemote(core_repo)
    original = remote.repository

    def changed(name):
        (destination / "keep.txt").write_text("other work")
        return original(name)

    monkeypatch.setattr(remote, "repository", changed)
    with pytest.raises(AethelError, match="destination changed"):
        clone_repository(remote, "demo", destination, branch=None, author="team")
    assert list(destination.iterdir()) == [destination / "keep.txt"]


def test_pull_fast_forward_then_noop_and_local_ahead(published, base_hash):
    remote, copy, first = published
    second = make_commit(remote.repo, base_hash, "second", ADAPTER_BYTES_B)
    assert "Pulled main" in pull_repository(remote, copy, "demo")
    assert copy.refs.read_branch("main") == second
    assert workspace_files(copy.workspace_dir) == workspace_files(remote.repo.workspace_dir)
    (copy.workspace_dir / "notes.txt").write_text("local uncommitted")
    assert "up to date" in pull_repository(remote, copy, "demo")
    assert (copy.workspace_dir / "notes.txt").read_text() == "local uncommitted"
    remote.repo.refs.update_branch("main", first)
    assert "ahead" in pull_repository(remote, copy, "demo")
    assert copy.refs.read_branch("main") == second


@pytest.mark.parametrize("change", ["edit", "delete_all", "untracked"])
def test_pull_preserves_dirty_workspace(published, base_hash, change):
    remote, copy, first = published
    make_commit(remote.repo, base_hash, "second", ADAPTER_BYTES_B)
    if change == "delete_all":
        for file in copy.workspace_dir.iterdir():
            file.unlink()
    else:
        file = "adapter_model.safetensors" if change == "edit" else "notes.txt"
        (copy.workspace_dir / file).write_bytes(b"my work")
    original = workspace_files(copy.workspace_dir)
    with pytest.raises(AethelError, match="uncommitted"):
        pull_repository(remote, copy, "demo")
    assert copy.refs.read_branch("main") == first
    assert workspace_files(copy.workspace_dir) == original


def test_pull_preserves_divergent_history(published, base_hash):
    remote, copy, _ = published
    make_commit(remote.repo, base_hash, "remote change", ADAPTER_BYTES_B)
    local = make_commit(copy, base_hash, "local change", b"local adapter")
    original = workspace_files(copy.workspace_dir)
    with pytest.raises(AethelError, match="diverged"):
        pull_repository(remote, copy, "demo")
    assert copy.refs.read_branch("main") == local
    assert workspace_files(copy.workspace_dir) == original


@pytest.mark.parametrize("kind", OBJECT_KINDS)
def test_corrupt_download_never_publishes_a_clone(core_repo, base_hash, tmp_path, kind):
    make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    digest = next(core_repo.objects.iter_hashes(kind))
    core_repo.objects.path_for(kind, digest).write_bytes(b"corruption")
    destination = tmp_path / "copy"
    with pytest.raises(AethelError, match="does not match"):
        clone_repository(StoredRemote(core_repo), "demo", destination, branch=None, author="team")
    assert not destination.exists()


def test_interrupted_pull_reuses_verified_objects_and_repairs_corrupt_cache(published, base_hash, monkeypatch):
    remote, copy, first = published
    second = make_commit(remote.repo, base_hash, "second", ADAPTER_BYTES_B)
    original = remote.fetch_object

    def interrupted(kind, digest):
        if kind == "blobs":
            yield b"partial"
            raise RemoteError("disconnected")
        yield from original(kind, digest)

    monkeypatch.setattr(remote, "fetch_object", interrupted)
    with pytest.raises(RemoteError, match="disconnected"):
        pull_repository(remote, copy, "demo")
    assert copy.refs.read_branch("main") == first
    assert (copy.workspace_dir / "adapter_model.safetensors").read_bytes() == ADAPTER_BYTES_A
    assert not list(copy.objects.root.rglob(".download-*"))
    # Repair a corrupt cached dependency while reusing the new commit/tree.
    copy.objects.path_for("bases", base_hash).write_bytes(b"bad cache")
    monkeypatch.setattr(remote, "fetch_object", original)
    remote.fetched.clear()
    pull_repository(remote, copy, "demo")
    assert copy.refs.read_branch("main") == second
    assert ("commits", second) not in remote.fetched
    assert ("bases", base_hash) in remote.fetched


@pytest.mark.parametrize("failure", ["ref_write", "head_move", "branch_move"])
def test_pull_rolls_back_workspace_on_ref_failure(published, base_hash, monkeypatch, failure):
    remote, copy, first = published
    make_commit(remote.repo, base_hash, "second", ADAPTER_BYTES_B)
    original_files = workspace_files(copy.workspace_dir)
    original_fetch = remote.fetch_object

    if failure == "ref_write":
        def fail(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(copy.refs, "update_branch", fail)
    else:
        def move(kind, digest):
            if failure == "head_move":
                copy.refs.set_head_detached(first)
            else:
                # A different tip with the same tree keeps the workspace clean.
                payload = copy.objects.read_json("commits", first)
                payload["message"] = "concurrent commit"
                new = copy.objects.write_json("commits", payload)
                copy.refs.update_branch("main", new)
            yield from original_fetch(kind, digest)
        monkeypatch.setattr(remote, "fetch_object", move)
    with pytest.raises((AethelError, OSError)):
        pull_repository(remote, copy, "demo")
    assert workspace_files(copy.workspace_dir) == original_files
    assert not (copy.aethel_dir / "workspace.backup").exists()


def test_pull_uses_pinned_snapshot(published, base_hash, monkeypatch):
    remote, copy, _ = published
    second = make_commit(remote.repo, base_hash, "second", ADAPTER_BYTES_B)
    original = remote.repository

    def grow(name):
        snapshot = original(name)
        make_commit(remote.repo, base_hash, "third", b"newer")
        return snapshot

    monkeypatch.setattr(remote, "repository", grow)
    pull_repository(remote, copy, "demo")
    assert copy.refs.read_branch("main") == second


def test_pull_refuses_wrong_base_and_detached_head(published):
    remote, copy, first = published
    copy.write_config({**copy.read_config(), "model_id": "other/model"})
    with pytest.raises(AethelError, match="different pinned base"):
        pull_repository(remote, copy, "demo")
    copy.refs.set_head_detached(first)
    with pytest.raises(AethelError, match="detached"):
        pull_repository(remote, copy, "demo")


def test_clone_without_main_and_branch_selection(core_repo, base_hash, tmp_path):
    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    core_repo.refs.create_branch("side", first)
    core_repo.refs.set_head_to_branch("side")
    core_repo.refs.delete_branch("main")
    copy = clone_repository(StoredRemote(core_repo), "demo", tmp_path / "single", branch=None, author="team")
    assert copy.refs.list_branches() == ["side"]
    core_repo.refs.create_branch("another", first)
    with pytest.raises(AethelError, match="--branch"):
        clone_repository(StoredRemote(core_repo), "demo", tmp_path / "ambiguous", branch=None, author="team")
    with pytest.raises(AethelError, match="checkout"):
        pull_repository(StoredRemote(core_repo), copy, "demo", branch="another")
    core_repo.refs.set_head_to_branch("another")
    core_repo.refs.delete_branch("side")
    with pytest.raises(AethelError, match="no branch"):
        pull_repository(StoredRemote(core_repo), copy, "demo")


@pytest.mark.parametrize("limit", ["MAX_BLOB_BYTES", "MAX_JSON_BYTES", "MAX_TOTAL_BYTES", "MAX_OBJECTS", "MAX_COMMITS"])
def test_resource_limits_prevent_installation(core_repo, base_hash, tmp_path, monkeypatch, limit):
    from aethel.remote import sync

    make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    monkeypatch.setattr(sync, limit, 1)
    if limit == "MAX_COMMITS":
        make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)
    destination = tmp_path / "copy"
    with pytest.raises(RemoteError, match="limit"):
        clone_repository(StoredRemote(core_repo), "demo", destination, branch=None, author="team")
    assert not destination.exists()


@pytest.mark.parametrize("mutation", ["tree_path", "adapter", "schema", "parent", "base"])
def test_malformed_history_is_rejected(core_repo, base_hash, tmp_path, mutation):
    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    commit = core_repo.objects.read_json("commits", first)
    if mutation == "tree_path":
        commit["tree"] = core_repo.objects.write_json("trees", {"files": {"../outside": commit["adapter_blob"]}})
    elif mutation == "adapter":
        commit["adapter_blob"] = "f" * 64
    elif mutation == "schema":
        commit["schema"] = 99
    elif mutation == "parent":
        del commit["parent_hash"]
    else:
        commit["base"] = core_repo.objects.write_json("bases", {"model_id": ""})
    core_repo.refs.update_branch("main", core_repo.objects.write_json("commits", commit))
    with pytest.raises(AethelError):
        clone_repository(StoredRemote(core_repo), "demo", tmp_path / "copy", branch=None, author="team")
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("command", ["clone", "pull"])
def test_command_help(command):
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.output


def test_real_hub_push_clone_pull_and_saved_remote(core_repo, base_hash, tmp_path, hub_config, monkeypatch):
    from tests.test_push import REMOTE, push, serve

    with serve(hub_config, monkeypatch):
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        push()
        runner = CliRunner()
        monkeypatch.delenv("AETHEL_HUB_URL", raising=False)
        result = runner.invoke(app, ["clone", "demo", str(tmp_path / "download"), "--remote", REMOTE, "--token", "secret"])
        assert result.exit_code == 0, result.output
        copy = Repo(tmp_path / "download")
        assert "secret" not in copy.config_path.read_text()
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)
        push()
        monkeypatch.chdir(copy.root)
        result = runner.invoke(app, ["pull"])
        assert result.exit_code == 0, result.output
        assert copy.refs.read_branch("main") == second
        assert workspace_files(copy.workspace_dir) == workspace_files(core_repo.workspace_dir)
        result = runner.invoke(app, ["push"])
        assert result.exit_code == 0, result.output


@pytest.mark.parametrize("kind", OBJECT_KINDS)
def test_raw_endpoint_preserves_noncanonical_bytes_and_refuses_corruption(hub_client, hub_storage, tmp_path, kind):
    data = b'{  "files": {}, "message": "exact bytes" }\n' if kind != "blobs" else b"raw weights"
    digest = hash_bytes(data)
    path = hub_storage.objects.path_for(kind, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    response = hub_client.get(f"/api/v1/objects/{kind}/{digest}")
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["X-Aethel-Object-Sha256"] == digest
    client = HubClient("http://testserver")
    client._client = hub_client
    store = ObjectStore(tmp_path / "received")
    HistoryDownload(client, store).object(kind, digest)
    assert store.path_for(kind, digest).read_bytes() == data
    path.write_bytes(b"wrong")
    assert hub_client.get(f"/api/v1/objects/{kind}/{digest}").status_code == 409
    path.unlink()
    assert hub_client.get(f"/api/v1/objects/{kind}/{digest}").status_code == 404


@pytest.mark.parametrize("payload", [[], {}, {"name": "wrong", "branches": {}}, {"name": "demo", "branches": {"../bad": "a" * 64}}, {"name": "demo", "branches": {"main": None}}])
def test_invalid_repository_metadata(payload, monkeypatch):
    client = HubClient("http://hub.test")
    monkeypatch.setattr(client, "_stream", lambda *a, **kw: iter([json.dumps(payload).encode()]))
    with pytest.raises(RemoteError, match="Invalid repository"):
        client.repository("demo")


def test_bounded_repository_metadata(monkeypatch):
    client = HubClient("http://hub.test")
    monkeypatch.setattr(client, "_stream", lambda *a, **kw: iter([b" " * (1024 * 1024 + 1)]))
    with pytest.raises(RemoteError, match="1 MiB"):
        client.repository("demo")


def test_history_is_not_limited_to_hub_display_page(core_repo, base_hash, tmp_path):
    for index in range(105):
        tip = make_commit(core_repo, base_hash, f"run {index}", ADAPTER_BYTES_A)
    copy = clone_repository(StoredRemote(core_repo), "demo", tmp_path / "copy", branch=None, author="team")
    assert copy.refs.read_branch("main") == tip
    assert len(list(copy.objects.iter_hashes("commits"))) == 105


def test_noncanonical_commit_is_cloned_verbatim(core_repo, base_hash, tmp_path):
    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    payload = core_repo.objects.read_json("commits", first)
    data = json.dumps(payload, indent=4).encode() + b"\n"
    digest = hash_bytes(data)
    path = core_repo.objects.path_for("commits", digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    core_repo.refs.update_branch("main", digest)
    copy = clone_repository(StoredRemote(core_repo), "demo", tmp_path / "copy", branch=None, author="team")
    assert copy.refs.read_branch("main") == digest
    assert copy.objects.path_for("commits", digest).read_bytes() == data


def test_pull_into_unborn_branch_and_refuse_unrelated_history(core_repo, base_hash, tmp_path):
    remote_tip = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    copy = Repo.create(tmp_path / "copy", core_repo.read_config())
    remote = StoredRemote(core_repo)
    pull_repository(remote, copy, "demo")
    assert copy.refs.read_branch("main") == remote_tip
    payload = copy.objects.read_json("commits", remote_tip)
    payload["message"] = "unrelated root"
    local_tip = copy.objects.write_json("commits", payload)
    copy.refs.update_branch("main", local_tip)
    with pytest.raises(AethelError, match="diverged"):
        pull_repository(remote, copy, "demo")
    assert copy.refs.read_branch("main") == local_tip


def test_repeated_blob_cannot_expand_workspace_without_bound(core_repo, base_hash, tmp_path, monkeypatch):
    from aethel.remote import sync

    first = make_commit(core_repo, base_hash, "first", b"x" * 100_000)
    commit = core_repo.objects.read_json("commits", first)
    files = {f"file-{index}": commit["adapter_blob"] for index in range(20)}
    files["adapter_model.safetensors"] = commit["adapter_blob"]
    commit["tree"] = core_repo.objects.write_json("trees", {"files": files})
    core_repo.refs.update_branch("main", core_repo.objects.write_json("commits", commit))
    monkeypatch.setattr(sync, "MAX_TOTAL_BYTES", 1_000_000)
    with pytest.raises(RemoteError, match="Restored workspace"):
        clone_repository(StoredRemote(core_repo), "demo", tmp_path / "copy", branch=None, author="team")


@pytest.mark.parametrize("kind", OBJECT_KINDS)
def test_missing_remote_dependency_does_not_install_clone(core_repo, base_hash, tmp_path, kind):
    make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    digest = next(core_repo.objects.iter_hashes(kind))
    core_repo.objects.path_for(kind, digest).unlink()
    with pytest.raises(FileNotFoundError):
        clone_repository(StoredRemote(core_repo), "demo", tmp_path / "copy", branch=None, author="team")
    assert not (tmp_path / "copy").exists()


@pytest.mark.parametrize("url", ["file:///tmp/hub", "http://user:secret@host", "https://host?token=secret", "http://host#fragment", "http://host:0", "http://host:bad"])
def test_invalid_or_secret_bearing_urls_are_rejected(url):
    with pytest.raises(RemoteError):
        HubClient(url)
