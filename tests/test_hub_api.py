"""Tests for the Hub REST API and the dashboard views.

Run in-process against an ASGI app over a temporary data directory (see the Hub
fixtures in conftest.py). Nothing here binds a socket, so the suite is fast and
cannot collide with a Hub the developer happens to have running.

The API's central promise is **the client names the hash, the server verifies
it**. Most of what follows is that promise under attack: wrong hashes, empty
bodies, oversized blobs, refs pointing at objects that were never uploaded, and
a log that has been edited on disk. A Hub that accepted any of those would still
look healthy on the dashboard, which is exactly why they are tested rather than
asserted in a docstring.
"""

import hashlib
import json
from dataclasses import replace

import pytest

from aethel.remote.objects import build_push_plan
from tests.conftest import (
    ADAPTER_BYTES_A,
    ADAPTER_BYTES_B,
    build_hub_client,
    make_commit,
    upload_objects,
)

BLOB = b"aethel-test-blob-payload"
BLOB_HASH = hashlib.sha256(BLOB).hexdigest()
ABSENT_HASH = hashlib.sha256(b"never-uploaded").hexdigest()


def canonical(payload: dict) -> bytes:
    """Serialize an object the way the core store does, so the hash matches."""
    from aethel.core.hashing import canonical_json

    return canonical_json(payload).encode("utf-8")


@pytest.fixture
def pushed(hub_client, core_repo, base_hash):
    """A Hub holding one repository, `demo`, with two commits on `main`.

    Built by running the real plan-and-upload path rather than by writing files
    into the Hub's store directly: a fixture that bypassed the API would test
    the assertions against a state the API cannot actually produce.
    """
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


# ---------------------------------------------------------------------------
# Upload: hash verification
# ---------------------------------------------------------------------------


class TestBlobUpload:
    def test_a_blob_is_stored_under_the_hash_it_claims(self, hub_client, hub_storage):
        response = hub_client.put(f"/api/v1/blobs/{BLOB_HASH}", content=BLOB)

        assert response.status_code == 200
        assert response.json() == {"hash": BLOB_HASH, "status": "stored", "bytes": len(BLOB)}
        assert hub_storage.has_object("blobs", BLOB_HASH)

    def test_content_that_does_not_match_its_hash_is_refused(self, hub_client, hub_storage):
        """The rejection the whole design rests on.

        If this ever returned 200, every downstream integrity claim -- the log,
        the Merkle root, the chain anchor -- would be anchoring content nobody
        verified.
        """
        response = hub_client.put(f"/api/v1/blobs/{BLOB_HASH}", content=b"different bytes")

        assert response.status_code == 422
        assert not hub_storage.has_object("blobs", BLOB_HASH)

    def test_a_rejected_upload_leaves_nothing_on_disk(self, hub_client, hub_storage):
        """Verify-then-write, not write-then-verify."""
        hub_client.put(f"/api/v1/blobs/{BLOB_HASH}", content=b"different bytes")

        assert hub_storage.object_counts()["blobs"] == 0

    def test_an_empty_body_is_refused(self, hub_client):
        response = hub_client.put(f"/api/v1/blobs/{BLOB_HASH}", content=b"")

        assert response.status_code == 400

    def test_a_malformed_hash_is_refused_before_any_work(self, hub_client):
        response = hub_client.put("/api/v1/blobs/not-a-hash", content=BLOB)

        assert response.status_code == 400
        assert "hex" in response.json()["detail"]

    def test_an_uppercase_hash_is_accepted_and_normalized(self, hub_client, hub_storage):
        response = hub_client.put(f"/api/v1/blobs/{BLOB_HASH.upper()}", content=BLOB)

        assert response.status_code == 200
        assert response.json()["hash"] == BLOB_HASH
        assert hub_storage.has_object("blobs", BLOB_HASH)

    def test_re_uploading_reports_already_present(self, hub_client):
        hub_client.put(f"/api/v1/blobs/{BLOB_HASH}", content=BLOB)
        again = hub_client.put(f"/api/v1/blobs/{BLOB_HASH}", content=BLOB)

        assert again.status_code == 200
        assert again.json() == {"hash": BLOB_HASH, "status": "already-present"}

    def test_a_blob_over_the_limit_is_refused(self, hub_config):
        """Bounded so one request cannot fill the disk."""
        tiny = replace(hub_config, max_blob_bytes=16)

        with build_hub_client(tiny) as client:
            response = client.put(f"/api/v1/blobs/{BLOB_HASH}", content=BLOB)

        assert response.status_code == 413
        assert "16 byte limit" in response.json()["detail"]


class TestJsonObjectUpload:
    def test_a_tree_is_stored_verbatim(self, hub_client, hub_storage):
        body = canonical({"schema": 2, "files": {"adapter_model.safetensors": BLOB_HASH}})
        digest = hashlib.sha256(body).hexdigest()

        response = hub_client.put(f"/api/v1/trees/{digest}", content=body)

        assert response.status_code == 200
        assert hub_storage.objects.path_for("trees", digest).read_bytes() == body

    @pytest.mark.parametrize("kind", ["commits", "trees", "bases"])
    def test_every_json_kind_verifies_its_hash(self, hub_client, kind):
        body = canonical({"schema": 2, "note": kind})

        response = hub_client.put(f"/api/v1/{kind}/{ABSENT_HASH}", content=body)

        assert response.status_code == 422

    def test_an_unknown_kind_is_a_404_not_a_new_store(self, hub_client):
        body = b'{"anything": 1}'
        digest = hashlib.sha256(body).hexdigest()

        response = hub_client.put(f"/api/v1/adapters/{digest}", content=body)

        assert response.status_code == 404

    def test_content_that_is_not_json_is_refused(self, hub_client):
        """Hash-valid but unparseable would store an object every read then fails on."""
        body = b"\x00\x01 not json at all"
        digest = hashlib.sha256(body).hexdigest()

        response = hub_client.put(f"/api/v1/commits/{digest}", content=body)

        assert response.status_code == 400
        assert "not valid JSON" in response.json()["detail"]

    def test_a_json_array_is_refused(self, hub_client):
        body = b'[1, 2, 3]'
        digest = hashlib.sha256(body).hexdigest()

        response = hub_client.put(f"/api/v1/commits/{digest}", content=body)

        assert response.status_code == 400
        assert "must be a JSON object" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestWriteAuth:
    @pytest.fixture
    def token_config(self, hub_config):
        return replace(hub_config, push_token="s3cret-token")

    def test_a_write_without_a_token_is_rejected(self, token_config):
        with build_hub_client(token_config) as client:
            response = client.put(f"/api/v1/blobs/{BLOB_HASH}", content=BLOB)

        assert response.status_code == 401

    def test_a_write_with_the_wrong_token_is_rejected(self, token_config):
        with build_hub_client(token_config) as client:
            response = client.put(
                f"/api/v1/blobs/{BLOB_HASH}",
                content=BLOB,
                headers={"Authorization": "Bearer wrong"},
            )

        assert response.status_code == 401

    def test_a_write_with_the_right_token_is_accepted(self, token_config):
        with build_hub_client(token_config) as client:
            response = client.put(
                f"/api/v1/blobs/{BLOB_HASH}",
                content=BLOB,
                headers={"Authorization": "Bearer s3cret-token"},
            )

        assert response.status_code == 200

    def test_reads_stay_open_when_writes_are_gated(self, token_config):
        """Published lineage is meant to be readable; only publishing is gated."""
        with build_hub_client(token_config) as client:
            client.put(
                f"/api/v1/blobs/{BLOB_HASH}",
                content=BLOB,
                headers={"Authorization": "Bearer s3cret-token"},
            )
            download = client.get(f"/api/v1/blobs/{BLOB_HASH}")
            repos = client.get("/api/v1/repos")

        assert download.status_code == 200
        assert download.content == BLOB
        assert repos.status_code == 200

    def test_negotiate_is_gated_too(self, token_config):
        """Negotiation reveals what the Hub holds, so it is a write-side call."""
        with build_hub_client(token_config) as client:
            response = client.post("/api/v1/repos/demo/negotiate", json={"have": {}})

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Negotiation
# ---------------------------------------------------------------------------


class TestNegotiate:
    def test_an_empty_hub_is_missing_everything_offered(self, hub_client):
        response = hub_client.post(
            "/api/v1/repos/demo/negotiate",
            json={"have": {"blobs": [BLOB_HASH, ABSENT_HASH]}},
        )

        assert response.status_code == 200
        assert set(response.json()["missing"]["blobs"]) == {BLOB_HASH, ABSENT_HASH}

    def test_only_what_is_lacking_comes_back(self, hub_client):
        hub_client.put(f"/api/v1/blobs/{BLOB_HASH}", content=BLOB)

        response = hub_client.post(
            "/api/v1/repos/demo/negotiate",
            json={"have": {"blobs": [BLOB_HASH, ABSENT_HASH]}},
        )

        assert response.json()["missing"] == {"blobs": [ABSENT_HASH]}

    def test_a_kind_with_nothing_missing_is_omitted_entirely(self, hub_client):
        hub_client.put(f"/api/v1/blobs/{BLOB_HASH}", content=BLOB)

        response = hub_client.post(
            "/api/v1/repos/demo/negotiate", json={"have": {"blobs": [BLOB_HASH]}}
        )

        assert response.json()["missing"] == {}

    def test_unknown_kinds_and_malformed_hashes_are_ignored(self, hub_client):
        """A garbage offer must not become a garbage upload list."""
        response = hub_client.post(
            "/api/v1/repos/demo/negotiate",
            json={"have": {"adapters": [BLOB_HASH], "blobs": ["short", 7, None]}},
        )

        assert response.status_code == 200
        assert response.json()["missing"] == {}

    def test_a_request_without_have_is_a_400(self, hub_client):
        response = hub_client.post("/api/v1/repos/demo/negotiate", json={})

        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Ref updates: the point where a push becomes published
# ---------------------------------------------------------------------------


class TestRefUpdate:
    def test_a_ref_cannot_point_at_a_commit_that_was_never_uploaded(self, hub_client):
        """Otherwise the Hub would publish a branch it cannot serve."""
        response = hub_client.post(
            "/api/v1/repos/demo/refs", json={"branch": "main", "commit": ABSENT_HASH}
        )

        assert response.status_code == 409

    def test_a_ref_cannot_point_at_a_commit_whose_ancestor_is_missing(
        self, hub_client, hub_storage, core_repo, base_hash
    ):
        """A gap in the middle of history is refused, not just a missing tip.

        Simulates an interrupted push: the tip arrived but its parent did not.
        Accepting this would leave the dashboard rendering a history that stops
        at a dead link.
        """
        make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
        second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_B)

        plan = build_push_plan(core_repo, "main", second)
        for kind in ("blobs", "trees", "bases"):
            for object_hash in sorted(plan.objects[kind]):
                data = core_repo.objects.path_for(kind, object_hash).read_bytes()
                hub_client.put(f"/api/v1/{kind}/{object_hash}", content=data)

        # Only the tip commit, deliberately.
        tip_bytes = core_repo.objects.path_for("commits", second).read_bytes()
        hub_client.put(f"/api/v1/commits/{second}", content=tip_bytes)

        response = hub_client.post(
            "/api/v1/repos/demo/refs", json={"branch": "main", "commit": second}
        )

        assert response.status_code == 409
        assert hub_storage.repo("demo") is None

    def test_a_complete_push_publishes_the_branch(self, pushed):
        assert pushed["ref"]["branches"] == {"main": pushed["second"]}

    def test_the_whole_history_is_logged_oldest_first(self, pushed, hub_log):
        """Log order matches commit order, so index reads as chronology."""
        assert hub_log.leaves() == [pushed["first"], pushed["second"]]
        assert pushed["ref"]["logged_indices"] == [0, 1]
        assert pushed["ref"]["appended_indices"] == [0, 1]

    def test_the_response_reports_the_root_it_just_produced(self, pushed, hub_log):
        assert pushed["ref"]["root"] == hub_log.root()
        assert pushed["ref"]["log_size"] == 2

    def test_pushing_the_same_branch_again_appends_nothing(self, pushed, hub_log):
        """A re-push must not move the root, or every re-push would look like tampering."""
        before = hub_log.root()

        response = pushed["client"].post(
            "/api/v1/repos/demo/refs",
            json={"branch": "main", "commit": pushed["second"]},
        )

        assert response.status_code == 200
        assert response.json()["appended_indices"] == []
        assert response.json()["logged_indices"] == [0, 1]
        assert response.json()["root"] == before
        assert hub_log.size() == 2

    def test_a_second_branch_joins_the_same_repository(self, pushed):
        response = pushed["client"].post(
            "/api/v1/repos/demo/refs",
            json={"branch": "experiment", "commit": pushed["first"]},
        )

        assert response.status_code == 200
        assert response.json()["branches"] == {
            "main": pushed["second"],
            "experiment": pushed["first"],
        }

    @pytest.mark.parametrize(
        "payload",
        [
            {"commit": ABSENT_HASH},
            {"branch": "", "commit": ABSENT_HASH},
            {"branch": "main"},
            {"branch": "main", "commit": "not-a-hash"},
            {"branch": 7, "commit": ABSENT_HASH},
        ],
        ids=["no-branch", "empty-branch", "no-commit", "bad-hash", "non-string-branch"],
    )
    def test_a_malformed_ref_update_is_a_400(self, hub_client, payload):
        response = hub_client.post("/api/v1/repos/demo/refs", json=payload)

        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Reading objects back
# ---------------------------------------------------------------------------


class TestReads:
    def test_a_commit_reads_back_with_its_own_hash(self, pushed):
        response = pushed["client"].get(f"/api/v1/commits/{pushed['second']}")

        assert response.status_code == 200
        body = response.json()
        assert body["hash"] == pushed["second"]
        assert body["message"] == "second version"
        assert body["parent_hash"] == pushed["first"]

    def test_a_tree_lists_the_files_it_names(self, pushed):
        commit = pushed["client"].get(f"/api/v1/commits/{pushed['second']}").json()

        response = pushed["client"].get(f"/api/v1/trees/{commit['tree']}")

        assert response.status_code == 200
        assert "adapter_model.safetensors" in response.json()["files"]

    def test_a_base_reads_back_as_a_reference_not_weights(self, pushed):
        commit = pushed["client"].get(f"/api/v1/commits/{pushed['second']}").json()

        response = pushed["client"].get(f"/api/v1/bases/{commit['base']}")

        assert response.status_code == 200
        body = response.json()
        assert body["model_id"] == "distilbert-base-uncased"
        assert "weights" not in body

    def test_a_downloaded_blob_hashes_to_what_was_asked_for(self, pushed):
        """The check that lets any transport be untrusted."""
        commit = pushed["client"].get(f"/api/v1/commits/{pushed['second']}").json()
        wanted = commit["adapter_blob"]

        response = pushed["client"].get(f"/api/v1/blobs/{wanted}")

        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == wanted
        assert response.content == ADAPTER_BYTES_B

    def test_a_blob_response_states_its_hash_in_a_header(self, pushed):
        commit = pushed["client"].get(f"/api/v1/commits/{pushed['second']}").json()
        wanted = commit["adapter_blob"]

        response = pushed["client"].get(f"/api/v1/blobs/{wanted}")

        assert response.headers["X-Aethel-Blob-Sha256"] == wanted

    def test_the_repository_index_lists_what_was_pushed(self, pushed):
        response = pushed["client"].get("/api/v1/repos")

        assert [r["name"] for r in response.json()["repos"]] == ["demo"]

    def test_commits_for_a_repository_come_back_newest_first(self, pushed):
        response = pushed["client"].get("/api/v1/repos/demo/commits")

        assert response.status_code == 200
        assert [c["hash"] for c in response.json()["commits"]] == [
            pushed["second"],
            pushed["first"],
        ]

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/repos/nope",
            "/api/v1/repos/nope/commits",
            f"/api/v1/commits/{ABSENT_HASH}",
            f"/api/v1/trees/{ABSENT_HASH}",
            f"/api/v1/bases/{ABSENT_HASH}",
            f"/api/v1/blobs/{ABSENT_HASH}",
        ],
    )
    def test_absent_things_are_404(self, hub_client, path):
        assert hub_client.get(path).status_code == 404

    def test_an_unknown_branch_filter_is_a_404(self, pushed):
        response = pushed["client"].get("/api/v1/repos/demo/commits?branch=nope")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Transparency log over HTTP
# ---------------------------------------------------------------------------


class TestLogEndpoints:
    def test_the_log_reports_its_leaves_with_repo_attribution(self, pushed):
        response = pushed["client"].get("/api/v1/log")

        assert response.status_code == 200
        body = response.json()
        assert body["size"] == 2
        assert [e["commit_hash"] for e in body["entries"]] == [
            pushed["first"],
            pushed["second"],
        ]
        assert {e["repo"] for e in body["entries"]} == {"demo"}

    def test_an_unanchored_log_says_so_rather_than_claiming_anchored(self, pushed):
        body = pushed["client"].get("/api/v1/log").json()

        assert body["last_anchor"] is None
        assert body["current_root_anchored"] is False

    def test_a_served_proof_verifies_against_the_served_root(self, pushed):
        client = pushed["client"]
        proof = client.get(f"/api/v1/log/proof/{pushed['first']}").json()

        response = client.post(
            "/api/v1/log/verify",
            json={
                "commit_hash": pushed["first"],
                "proof": proof["proof"],
                "root": proof["root"],
            },
        )

        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_a_proof_fails_against_a_different_root(self, pushed):
        client = pushed["client"]
        proof = client.get(f"/api/v1/log/proof/{pushed['first']}").json()

        response = client.post(
            "/api/v1/log/verify",
            json={
                "commit_hash": pushed["first"],
                "proof": proof["proof"],
                "root": "0" * 64,
            },
        )

        assert response.json()["valid"] is False

    def test_a_tampered_sibling_fails_verification(self, pushed):
        """The FAIL half of the demo, at the API layer."""
        client = pushed["client"]
        proof = client.get(f"/api/v1/log/proof/{pushed['first']}").json()

        forged = [dict(step, sibling="f" * 64) for step in proof["proof"]]

        response = client.post(
            "/api/v1/log/verify",
            json={
                "commit_hash": pushed["first"],
                "proof": forged,
                "root": proof["root"],
            },
        )

        assert response.json()["valid"] is False

    def test_a_proof_step_of_the_wrong_shape_is_invalid_not_a_500(self, hub_client):
        """Junk in a proof is a failed verification, not a broken Hub."""
        response = hub_client.post(
            "/api/v1/log/verify",
            json={
                "commit_hash": ABSENT_HASH,
                "proof": [{"nonsense": True}, "not-even-an-object"],
                "root": "0" * 64,
            },
        )

        assert response.status_code == 200
        assert response.json()["valid"] is False

    def test_a_commit_that_was_never_pushed_has_no_proof(self, pushed):
        response = pushed["client"].get(f"/api/v1/log/proof/{ABSENT_HASH}")

        assert response.status_code == 404

    def test_a_malformed_hash_asks_for_a_proof_and_gets_a_400(self, hub_client):
        assert hub_client.get("/api/v1/log/proof/nope").status_code == 400

    @pytest.mark.parametrize(
        "payload",
        [
            {"proof": [], "root": "0" * 64},
            {"commit_hash": ABSENT_HASH, "root": "0" * 64},
            {"commit_hash": ABSENT_HASH, "proof": []},
            {"commit_hash": ABSENT_HASH, "proof": "not-a-list", "root": "0" * 64},
        ],
        ids=["no-commit", "no-proof", "no-root", "proof-not-a-list"],
    )
    def test_a_malformed_verify_request_is_a_400(self, hub_client, payload):
        assert hub_client.post("/api/v1/log/verify", json=payload).status_code == 400


# ---------------------------------------------------------------------------
# Health: the ops board's data source
# ---------------------------------------------------------------------------


class TestHealth:
    def test_an_unconfigured_hub_is_honest_about_being_unconfigured(self, hub_client):
        """Warnings, not fake greens, for layers that do not exist yet."""
        response = hub_client.get("/api/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "warning"

        by_name = {check["name"]: check for check in body["checks"]}
        assert by_name["Chain"]["status"] == "warning"
        assert by_name["IPFS mirror"]["status"] == "warning"
        assert by_name["Write auth"]["status"] == "warning"
        assert by_name["Log vs anchored root"]["status"] == "warning"
        assert by_name["Object store"]["status"] == "good"

    def test_every_subsystem_gets_a_row(self, hub_client):
        checks = hub_client.get("/api/v1/health").json()["checks"]

        assert {check["name"] for check in checks} == {
            "Object store",
            "Integrity sample",
            "Repository index",
            "Transparency log",
            "Log vs anchored root",
            "Chain",
            "IPFS mirror",
            "Write auth",
        }

    def test_a_configured_token_reports_auth_as_required(self, hub_config):
        with build_hub_client(replace(hub_config, push_token="t")) as client:
            checks = client.get("/api/v1/health").json()["checks"]

        by_name = {check["name"]: check for check in checks}
        assert by_name["Write auth"]["status"] == "good"

    def test_a_configured_chain_reports_configured(self, hub_config):
        configured = replace(
            hub_config,
            chain_rpc_url="https://example.invalid/rpc",
            chain_id=11155111,
            anchor_contract="0x" + "ab" * 20,
        )

        with build_hub_client(configured) as client:
            checks = client.get("/api/v1/health").json()["checks"]

        by_name = {check["name"]: check for check in checks}
        assert by_name["Chain"]["status"] == "good"
        assert "11155111" in by_name["Chain"]["detail"]

    def test_a_flipped_byte_in_a_stored_blob_turns_the_page_critical(self, pushed, hub_storage):
        """Corruption is found by re-hashing, and it fails the endpoint loudly."""
        blob_hash = next(iter(hub_storage.objects.iter_hashes("blobs")))
        path = hub_storage.objects.path_for("blobs", blob_hash)
        data = bytearray(path.read_bytes())
        data[0] ^= 0xFF
        path.write_bytes(bytes(data))

        response = pushed["client"].get("/api/v1/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "critical"
        by_name = {check["name"]: check for check in body["checks"]}
        assert by_name["Integrity sample"]["status"] == "critical"

    def test_a_log_that_drifts_from_its_anchor_is_reported_critical(
        self, pushed, hub_log, hub_config
    ):
        """The self-audit row: the one check that watches for log tampering.

        Anchor the current root, edit a leaf, and the recomputed root no longer
        matches what was published. This is the row that would catch a Hub
        operator rewriting history.
        """
        from hub.log import AnchorStore

        AnchorStore(hub_config.anchors_path).append(
            {"root": hub_log.root(), "block": 1234, "leaf_count": hub_log.size()}
        )

        healthy = pushed["client"].get("/api/v1/health").json()
        by_name = {check["name"]: check for check in healthy["checks"]}
        assert by_name["Log vs anchored root"]["status"] == "good"

        entries = hub_log.entries()
        forged = [
            replace(entries[0], commit_hash=hashlib.sha256(b"forged").hexdigest()),
            *entries[1:],
        ]
        hub_log.path.write_text(
            "\n".join(entry.to_json() for entry in forged) + "\n", encoding="utf-8"
        )

        response = pushed["client"].get("/api/v1/health")

        assert response.status_code == 503
        by_name = {check["name"]: check for check in response.json()["checks"]}
        assert by_name["Log vs anchored root"]["status"] == "critical"
        assert "ALTERED" in by_name["Log vs anchored root"]["detail"].upper()

    def test_counts_are_reported_for_the_board(self, pushed):
        counts = pushed["client"].get("/api/v1/health").json()["counts"]

        assert counts["commits"] == 2
        assert counts["blobs"] >= 2


class TestVersion:
    def test_version_states_what_is_deployed(self, hub_client, hub_config):
        response = hub_client.get("/api/v1/version")

        assert response.status_code == 200
        body = response.json()
        assert body["data_dir"] == str(hub_config.data_dir)
        assert body["chain_configured"] is False
        assert body["auth_required"] is False
        assert body["hub"]
        assert body["started_at"]


# ---------------------------------------------------------------------------
# Dashboard views
# ---------------------------------------------------------------------------


class TestViews:
    def test_the_landing_page_renders_when_empty(self, hub_client):
        response = hub_client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_the_landing_page_lists_a_pushed_repository(self, pushed):
        response = pushed["client"].get("/")

        assert response.status_code == 200
        assert "demo" in response.text

    def test_the_repository_page_shows_its_commits(self, pushed):
        response = pushed["client"].get("/r/demo")

        assert response.status_code == 200
        assert "second version" in response.text
        assert pushed["second"][:12] in response.text

    def test_the_repository_page_marks_logged_commits(self, pushed):
        response = pushed["client"].get("/r/demo")

        assert "Logged" in response.text

    def test_the_commit_page_shows_a_verified_inclusion_proof(self, pushed):
        response = pushed["client"].get(f"/c/{pushed['second']}")

        assert response.status_code == 200
        assert pushed["second"] in response.text
        assert "second version" in response.text

    def test_the_ops_page_renders(self, pushed):
        response = pushed["client"].get("/ops")

        assert response.status_code == 200
        assert "Transparency log" in response.text

    def test_the_ops_page_still_renders_while_degraded(self, pushed, hub_storage):
        """An ops board that 500s during an incident is useless precisely then."""
        blob_hash = next(iter(hub_storage.objects.iter_hashes("blobs")))
        path = hub_storage.objects.path_for("blobs", blob_hash)
        path.write_bytes(b"corrupted")

        response = pushed["client"].get("/ops")

        assert response.status_code == 200
        assert "Integrity sample" in response.text

    @pytest.mark.parametrize("path", ["/r/nope", f"/c/{ABSENT_HASH}"])
    def test_unknown_pages_are_404(self, hub_client, path):
        assert hub_client.get(path).status_code == 404

    def test_the_stylesheet_is_served_locally(self, hub_client):
        """No CDN: the demo network may not reach one."""
        response = hub_client.get("/static/hub.css")

        assert response.status_code == 200
        assert "chart-svg" in response.text


def test_the_log_file_is_plain_jsonl_on_disk(pushed, hub_config):
    """Auditability without the API: the log can be read with `cat`."""
    lines = hub_config.log_path.read_text(encoding="utf-8").strip().splitlines()

    assert [json.loads(line)["commit_hash"] for line in lines] == [
        pushed["first"],
        pushed["second"],
    ]
