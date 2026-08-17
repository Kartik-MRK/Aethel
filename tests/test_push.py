"""Tests for `aethel push` -- the plan, and the round trip to a Hub.

The command has two halves and they are tested differently.

`build_push_plan` is pure logic over the object store: given a branch tip, name
every object a remote needs, and order them so nothing is ever sent before what
it references. That is tested directly, because a wrong plan is a wrong push and
this is where it can be caught cheaply.

`run_push` is tested against a real Hub running in-process. `httpx.Client` is
replaced with one whose transport is the ASGI app, so the actual client code --
request building, hash echo verification, error mapping -- runs unchanged against
the actual API, and the assertions are made against the Hub's own store and log.
Nothing is stubbed out; the only missing piece is the socket.
"""

from contextlib import contextmanager
from dataclasses import replace

import pytest
import typer

from aethel.commands.push import DEFAULT_REMOTE, resolve_remote, resolve_repo_name, run_push
from aethel.core.commits import walk_history
from aethel.core.errors import ObjectNotFound
from aethel.remote.objects import UPLOAD_ORDER, build_push_plan
from tests.conftest import ADAPTER_BYTES_A, ADAPTER_BYTES_B, make_commit

TOKEN = "s3cret-push-token"

#: Any URL works: the transport is swapped for the in-process app, so this is
#: only ever echoed back in the command's output.
REMOTE = "http://hub.test"


@contextmanager
def serve(config, monkeypatch):
    """Point `httpx.Client` at an in-process Hub built on `config`.

    The client under test builds its own `httpx.Client`, so the seam is that
    constructor. Swapping it for a `TestClient` over the ASGI app keeps every
    line of `HubClient` and `run_push` in play -- they issue the same requests
    and parse the same responses -- while removing uvicorn, a port, and the
    flakiness of both from the suite.
    """
    pytest.importorskip("fastapi", reason="the Hub needs the [hub] extra")

    import httpx
    from fastapi.testclient import TestClient

    from hub.app import create_app

    app = create_app(config)

    def in_process_client(*_args, headers=None, follow_redirects=True, **_kwargs):
        # base_url and timeout are deliberately dropped: requests are issued as
        # root-relative paths, and there is no socket that could time out.
        return TestClient(app, headers=dict(headers or {}), follow_redirects=follow_redirects)

    # Entering the outer client runs the lifespan, which is what populates
    # app.state with the storage, log and anchor store the endpoints depend on.
    with TestClient(app) as client:
        monkeypatch.setattr(httpx, "Client", in_process_client)
        yield client


@pytest.fixture
def clean_env(monkeypatch):
    """No AETHEL_* remote settings leaking in from the developer's shell."""
    monkeypatch.delenv("AETHEL_HUB_URL", raising=False)
    monkeypatch.delenv("AETHEL_HUB_TOKEN", raising=False)


@pytest.fixture
def hub(hub_config, monkeypatch, clean_env):
    """An open Hub, in-process, that `aethel push` will reach."""
    with serve(hub_config, monkeypatch) as client:
        yield client


@pytest.fixture
def secured_hub(hub_config, monkeypatch, clean_env):
    """The same Hub, with a push token configured."""
    with serve(replace(hub_config, push_token=TOKEN), monkeypatch) as client:
        yield client


def push(**overrides) -> None:
    """Run the push command with the flags a test cares about."""
    options = {
        "remote": REMOTE,
        "repo_name": "demo",
        "branch": None,
        "token": None,
        "dry_run": False,
    }
    options.update(overrides)
    run_push(**options)


def hub_log_state(client) -> dict:
    """The Hub's own view of its log, read through the API."""
    response = client.get("/api/v1/log")
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


class TestPushPlan:
    def test_the_plan_names_every_reachable_object(self, core_repo, base_hash):
        """One commit reaches its tree, that tree's blobs, and its base.

        The workspace holds three files -- weights, adapter config, training
        info -- and the tree covers the whole workspace, so a single commit is
        three blobs rather than one.
        """
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        plan = build_push_plan(core_repo, "main", commit)

        assert plan.branch == "main"
        assert plan.tip == commit
        assert plan.counts() == {"commits": 1, "trees": 1, "blobs": 3, "bases": 1}
        assert plan.total() == 6

    def test_commits_are_ordered_oldest_first(self, core_repo, base_hash):
        """Ancestors before descendants, which is what the log indices rely on.

        `walk_history` yields newest-first; the plan reverses it. If that ever
        stopped happening, the Hub would log a child ahead of its parent and log
        order would no longer match history.
        """
        first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        plan = build_push_plan(core_repo, "main", second)

        assert plan.commit_order == [first, second]
        assert [h for h, _ in walk_history(core_repo, second)] == [second, first]

    def test_identical_weights_share_one_blob(self, core_repo, base_hash):
        """Content addressing, visible in the plan.

        Two commits of byte-identical artifacts reach one tree and three blobs,
        not two trees and six blobs. This is why a history of a hundred
        near-identical adapters is not a hundred full copies.
        """
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "again, same weights", ADAPTER_BYTES_A)

        plan = build_push_plan(core_repo, "main", second)

        assert plan.counts() == {"commits": 2, "trees": 1, "blobs": 3, "bases": 1}

    def test_differing_weights_share_the_unchanged_files(self, core_repo, base_hash):
        """Only what actually changed becomes a new blob."""
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        plan = build_push_plan(core_repo, "main", second)

        # Two adapter files, one shared adapter_config.json, one shared
        # training_info.json.
        assert plan.counts() == {"commits": 2, "trees": 2, "blobs": 4, "bases": 1}

    def test_a_missing_blob_fails_the_plan(self, core_repo, base_hash):
        """Planning is also a local integrity check.

        A push that skipped an unreadable blob would publish a branch the remote
        cannot serve. Failing here means the error can name the local object.
        """
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        tree = core_repo.objects.read_json("commits", commit)["tree"]
        blob = core_repo.objects.read_tree(tree)["adapter_model.safetensors"]

        core_repo.objects.path_for("blobs", blob).unlink()

        with pytest.raises(ObjectNotFound) as exc:
            build_push_plan(core_repo, "main", commit)

        assert blob[:12] in str(exc.value)

    def test_a_missing_base_fails_the_plan(self, core_repo, base_hash):
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        core_repo.objects.path_for("bases", base_hash).unlink()

        with pytest.raises(ObjectNotFound) as exc:
            build_push_plan(core_repo, "main", commit)

        assert base_hash[:12] in str(exc.value)

    def test_a_missing_tree_fails_the_plan(self, core_repo, base_hash):
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        tree = core_repo.objects.read_json("commits", commit)["tree"]

        core_repo.objects.path_for("trees", tree).unlink()

        with pytest.raises(ObjectNotFound):
            build_push_plan(core_repo, "main", commit)

    def test_the_have_map_is_sorted_and_covers_every_kind(self, core_repo, base_hash):
        """Negotiation payloads must be reproducible, so the sets are sorted."""
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        have = build_push_plan(core_repo, "main", commit).have_map()

        assert list(have) == list(UPLOAD_ORDER)
        for hashes in have.values():
            assert hashes == sorted(hashes)
        assert have["commits"] == [commit]

    def test_counts_and_total_agree(self, core_repo, base_hash):
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        plan = build_push_plan(core_repo, "main", second)

        assert plan.total() == sum(plan.counts().values())
        assert plan.total() == sum(len(hashes) for hashes in plan.have_map().values())

    def test_the_plan_covers_only_the_branch_it_was_given(self, core_repo, base_hash):
        """A branch's plan is its own ancestry, not everything in the store."""
        first = make_commit(core_repo, base_hash, "shared", ADAPTER_BYTES_A)

        core_repo.refs.create_branch("side", first)
        core_repo.refs.set_head_to_branch("side")
        side = make_commit(core_repo, base_hash, "side work", ADAPTER_BYTES_B)

        core_repo.refs.set_head_to_branch("main")
        plan = build_push_plan(core_repo, "main", first)

        assert plan.commit_order == [first]
        assert side not in plan.objects["commits"]


# ---------------------------------------------------------------------------
# Option resolution
# ---------------------------------------------------------------------------


class TestOptionResolution:
    def test_the_flag_wins_over_the_environment(self, monkeypatch):
        monkeypatch.setenv("AETHEL_HUB_URL", "http://from-env")

        assert resolve_remote("http://from-flag") == "http://from-flag"

    def test_the_environment_wins_over_the_default(self, monkeypatch):
        monkeypatch.setenv("AETHEL_HUB_URL", "http://from-env")

        assert resolve_remote(None) == "http://from-env"

    def test_localhost_is_the_last_resort(self, monkeypatch, clean_env):
        assert resolve_remote(None) == DEFAULT_REMOTE
        assert resolve_remote("   ") == DEFAULT_REMOTE

    def test_the_repo_name_falls_back_to_the_directory(self, core_repo):
        assert resolve_repo_name(core_repo, None) == core_repo.root.name

    def test_a_configured_name_beats_the_directory(self, core_repo):
        core_repo.write_config({**core_repo.read_config(), "hub_repo": "published-name"})

        assert resolve_repo_name(core_repo, None) == "published-name"
        assert resolve_repo_name(core_repo, "explicit") == "explicit"


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_a_first_push_uploads_everything_and_publishes_the_branch(
        self, core_repo, base_hash, hub, hub_storage
    ):
        first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        push()

        assert hub_storage.object_counts() == {"commits": 2, "trees": 2, "blobs": 4, "bases": 1}

        record = hub_storage.repo("demo")
        assert record is not None
        assert record.branches == {"main": second}
        assert hub_storage.has_object("commits", first)

    def test_the_pushed_blob_is_byte_identical(self, core_repo, base_hash, hub, hub_storage):
        """The Hub holds the same bytes, not a re-encoding of them."""
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        push()

        tree = core_repo.objects.read_json("commits", commit)["tree"]
        blob = core_repo.objects.read_tree(tree)["adapter_model.safetensors"]

        assert hub_storage.blob_path(blob).read_bytes() == ADAPTER_BYTES_A

    def test_the_whole_history_is_logged_oldest_first(
        self, core_repo, base_hash, hub, hub_log
    ):
        first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        push()

        assert hub_log.leaves() == [first, second]
        assert [entry.repo for entry in hub_log.entries()] == ["demo", "demo"]

    def test_every_pushed_commit_has_a_verifiable_inclusion_proof(
        self, core_repo, base_hash, hub, hub_log
    ):
        """The point of pushing, from the transparency log's side."""
        from hub.log import TransparencyLog

        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        push()

        root = hub_log.root()
        for commit in hub_log.leaves():
            proof = hub.get(f"/api/v1/log/proof/{commit}").json()
            assert proof["root"] == root
            assert TransparencyLog.verify(commit, proof["proof"], root)

        assert hub_log.contains(second)

    def test_the_api_reports_the_same_log_the_disk_holds(
        self, core_repo, base_hash, hub, hub_log
    ):
        """What the Hub publishes is what is on its disk, and it is honest about
        not being anchored yet."""
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        push()
        state = hub_log_state(hub)

        assert state["size"] == hub_log.size() == 2
        assert state["root"] == hub_log.root()
        assert [entry["commit_hash"] for entry in state["entries"]] == hub_log.leaves()
        assert state["last_anchor"] is None
        assert state["current_root_anchored"] is False

    def test_a_second_push_of_the_same_branch_changes_nothing(
        self, core_repo, base_hash, hub, hub_storage, hub_log
    ):
        """Idempotence, end to end.

        A re-push must not add objects, add leaves, or move the root. If it did,
        every routine sync would look indistinguishable from tampering to
        anyone watching the anchored root.
        """
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        push()
        counts = hub_storage.object_counts()
        root = hub_log.root()
        size = hub_log.size()

        push()

        assert hub_storage.object_counts() == counts
        assert hub_log.size() == size
        assert hub_log.root() == root

    def test_a_second_push_uploads_only_the_delta(
        self, core_repo, base_hash, hub, hub_storage, hub_log
    ):
        """Negotiation at work: the shared files are not sent twice."""
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        push()

        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)
        push()

        # One new commit, one new tree, one new adapter blob. The base, the
        # adapter config and the training info were already there.
        assert hub_storage.object_counts() == {"commits": 2, "trees": 2, "blobs": 4, "bases": 1}
        assert hub_storage.repo("demo").branches == {"main": second}
        assert hub_log.size() == 2

    def test_an_object_the_hub_lost_is_re_uploaded(
        self, core_repo, base_hash, hub, hub_storage
    ):
        """Why the delta is decided by the server, not the client.

        A client that remembered what it had already sent would leave this Hub
        permanently unable to serve the branch it publishes. Asking `/negotiate`
        every time makes the next push a repair.
        """
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        push()

        tree = core_repo.objects.read_json("commits", commit)["tree"]
        blob = core_repo.objects.read_tree(tree)["adapter_model.safetensors"]
        hub_storage.objects.path_for("blobs", blob).unlink()
        assert not hub_storage.has_object("blobs", blob)

        push()

        assert hub_storage.has_object("blobs", blob)
        assert hub_storage.blob_path(blob).read_bytes() == ADAPTER_BYTES_A

    def test_the_published_name_is_remembered(self, core_repo, base_hash, hub):
        """So later pushes need no flags, and the name cannot drift."""
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        push(repo_name="chosen-name")

        assert core_repo.read_config()["hub_repo"] == "chosen-name"

    def test_a_remembered_name_is_used_by_the_next_push(
        self, core_repo, base_hash, hub, hub_storage
    ):
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        push(repo_name="chosen-name")

        make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)
        push(repo_name=None)

        assert set(hub_storage.repos()) == {"chosen-name"}

    def test_a_named_branch_can_be_pushed_while_head_is_elsewhere(
        self, core_repo, base_hash, hub, hub_storage
    ):
        first = make_commit(core_repo, base_hash, "shared", ADAPTER_BYTES_A)
        core_repo.refs.create_branch("side", first)
        core_repo.refs.set_head_to_branch("side")
        side = make_commit(core_repo, base_hash, "side work", ADAPTER_BYTES_B)
        core_repo.refs.set_head_to_branch("main")

        push(branch="side")

        assert hub_storage.repo("demo").branches == {"side": side}

    def test_two_branches_publish_under_one_repository(
        self, core_repo, base_hash, hub, hub_storage, hub_log
    ):
        first = make_commit(core_repo, base_hash, "shared", ADAPTER_BYTES_A)
        push()

        core_repo.refs.create_branch("side", first)
        core_repo.refs.set_head_to_branch("side")
        side = make_commit(core_repo, base_hash, "side work", ADAPTER_BYTES_B)
        push()

        assert hub_storage.repo("demo").branches == {"main": first, "side": side}
        # The shared ancestor is one leaf, not two.
        assert hub_log.leaves() == [first, side]


class TestRefusals:
    def test_a_detached_head_is_refused(self, core_repo, base_hash, hub, hub_storage):
        """Publishing a commit no branch points at would create remote-only
        history, so it is refused rather than pushed under a guessed name."""
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        core_repo.refs.set_head_detached(commit)

        with pytest.raises(typer.Exit):
            push()

        assert hub_storage.repo("demo") is None
        assert hub_storage.object_counts() == {"commits": 0, "trees": 0, "blobs": 0, "bases": 0}

    def test_a_branch_with_no_commits_is_refused(self, core_repo, hub, hub_storage):
        with pytest.raises(typer.Exit):
            push()

        assert hub_storage.repo("demo") is None

    def test_an_unknown_branch_is_refused(self, core_repo, base_hash, hub, hub_storage):
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        with pytest.raises(typer.Exit):
            push(branch="no-such-branch")

        assert hub_storage.repo("demo") is None

    def test_a_missing_local_object_is_refused_before_uploading(
        self, core_repo, base_hash, hub, hub_storage
    ):
        """The plan runs before any bytes leave the machine."""
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        tree = core_repo.objects.read_json("commits", commit)["tree"]
        blob = core_repo.objects.read_tree(tree)["adapter_model.safetensors"]
        core_repo.objects.path_for("blobs", blob).unlink()

        with pytest.raises(typer.Exit):
            push()

        assert hub_storage.object_counts() == {"commits": 0, "trees": 0, "blobs": 0, "bases": 0}
        assert hub_storage.repo("demo") is None

    def test_a_dry_run_uploads_nothing(
        self, core_repo, base_hash, hub, hub_storage, hub_log
    ):
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        push(dry_run=True)

        assert hub_storage.object_counts() == {"commits": 0, "trees": 0, "blobs": 0, "bases": 0}
        assert hub_storage.repo("demo") is None
        assert hub_log.size() == 0

    def test_a_dry_run_leaves_the_configured_name_alone(self, core_repo, base_hash, hub):
        """Nothing was published, so nothing should be remembered."""
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        push(repo_name="chosen-name", dry_run=True)

        assert "hub_repo" not in core_repo.read_config()


class TestAuthenticatedPush:
    def test_a_push_without_the_token_is_refused(
        self, core_repo, base_hash, secured_hub, hub_storage
    ):
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        with pytest.raises(typer.Exit):
            push()

        assert hub_storage.object_counts()["blobs"] == 0
        assert hub_storage.repo("demo") is None

    def test_the_token_flag_authorizes_the_push(
        self, core_repo, base_hash, secured_hub, hub_storage
    ):
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        push(token=TOKEN)

        assert hub_storage.repo("demo").branches == {"main": commit}

    def test_the_environment_token_authorizes_the_push(
        self, core_repo, base_hash, secured_hub, hub_storage, monkeypatch
    ):
        monkeypatch.setenv("AETHEL_HUB_TOKEN", TOKEN)
        commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        push()

        assert hub_storage.repo("demo").branches == {"main": commit}

    def test_a_wrong_token_is_refused(self, core_repo, base_hash, secured_hub, hub_storage):
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        with pytest.raises(typer.Exit):
            push(token="wrong-token")

        assert hub_storage.repo("demo") is None


class TestClientSideVerification:
    def test_the_echoed_hash_is_checked(self):
        """`_verify_echo` accepts an honest acknowledgement and refuses any other."""
        from aethel.remote.http import RemoteError, _verify_echo

        payload = {"hash": "a" * 64, "status": "stored"}
        assert _verify_echo(_FakeResponse(payload), "a" * 64, "blob") == payload

        with pytest.raises(RemoteError):
            _verify_echo(_FakeResponse({"hash": "b" * 64}), "a" * 64, "blob")

        with pytest.raises(RemoteError):
            _verify_echo(_FakeResponse({}), "a" * 64, "blob")

    def test_a_remote_that_echoes_the_wrong_hash_is_refused(
        self, core_repo, base_hash, hub, hub_storage, monkeypatch
    ):
        """The client re-checks what the remote says it stored.

        The Hub already recomputes the hash from the bytes, so this covers the
        one case that check cannot: something between client and Hub -- a proxy,
        a misconfigured gateway -- rewriting the response. Here the upload really
        happens and only the acknowledgement is forged, which is exactly that
        scenario. The push must abort and the ref must not move.
        """
        from aethel.remote import http as http_module

        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        original = http_module.HubClient._request

        def lying_request(self, method, path, *, what, **kwargs):
            response = original(self, method, path, what=what, **kwargs)
            if method == "PUT" and "/blobs/" in path:
                return _FakeResponse({"hash": "f" * 64, "status": "stored"})
            return response

        monkeypatch.setattr(http_module.HubClient, "_request", lying_request)

        with pytest.raises(typer.Exit):
            push()

        assert hub_storage.repo("demo") is None

    def test_a_remote_that_is_not_a_hub_is_refused(
        self, core_repo, base_hash, hub, hub_storage, monkeypatch
    ):
        """`push` identifies the remote before uploading to it.

        Pointing --remote at the wrong port should fail immediately with a clear
        message rather than part-way through an upload.
        """
        from aethel.remote import http as http_module

        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

        def not_a_hub(self):
            raise http_module.RemoteError("that does not look like an Aethel Hub")

        monkeypatch.setattr(http_module.HubClient, "version", not_a_hub)

        with pytest.raises(typer.Exit):
            push()

        assert hub_storage.object_counts()["blobs"] == 0


class _FakeResponse:
    """The minimum `_verify_echo` reads: a JSON body."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_the_ref_moves_only_after_every_object_is_present(
    core_repo, base_hash, hub, hub_storage, hub_log
):
    """The Hub re-walks history before publishing, so the ref update is also
    the completeness check.

    Simulated by uploading nothing and asking for the ref anyway: the branch
    must not move, and nothing must be logged.
    """
    commit = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)

    response = hub.post("/api/v1/repos/demo/refs", json={"branch": "main", "commit": commit})

    assert response.status_code == 409
    assert hub_storage.repo("demo") is None
    assert hub_log.size() == 0
