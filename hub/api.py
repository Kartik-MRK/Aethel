"""Hub REST API.

Endpoint shape follows one rule: **the client names the hash, the server
verifies it.** Uploads are addressed by the hash they claim to be, and the Hub
recomputes it from the received bytes before storing. A client therefore never
has to trust that the Hub stored what was sent, and a corrupted transfer fails
loudly instead of silently poisoning the store.

Blobs arrive as raw request bodies rather than multipart form data. Content
addressing makes the filename irrelevant — the hash in the URL is the name —
so multipart would add a parsing dependency and a filename field that must be
ignored anyway.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from aethel.core.errors import AethelError, InvalidHash, ObjectNotFound
from aethel.core.hashing import is_valid_hash
from aethel.core.objects import OBJECT_KINDS
from hub.config import HubConfig
from hub.log import AnchorStore, TransparencyLog
from hub.storage import HashMismatch, HubStorage, HubStorageError

router = APIRouter()

#: Structured object kinds a client may upload. Blobs have their own endpoint
#: because they are raw bytes rather than JSON.
JSON_KINDS = ("commits", "trees", "bases")


def get_config(request: Request) -> HubConfig:
    return request.app.state.config


def get_storage(request: Request) -> HubStorage:
    return request.app.state.storage


def get_log(request: Request) -> TransparencyLog:
    return request.app.state.log


def get_anchors(request: Request) -> AnchorStore:
    return request.app.state.anchors


def require_write_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Gate write endpoints behind the shared token, when one is configured.

    An unconfigured Hub is open on purpose — that is the right default for a
    laptop demo — but the ops board reports it as open rather than implying
    security that isn't there.
    """
    config: HubConfig = request.app.state.config

    if not config.auth_required:
        return

    expected = f"Bearer {config.push_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid push token.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_hash(value: str, label: str) -> str:
    if not is_valid_hash(value.lower()):
        raise HTTPException(
            status_code=400, detail=f"{label} must be a 64-character hex SHA-256 digest."
        )
    return value.lower()


# ---------------------------------------------------------------------------
# Object upload
# ---------------------------------------------------------------------------


@router.put("/api/v1/blobs/{blob_hash}", dependencies=[Depends(require_write_auth)])
async def put_blob(
    blob_hash: str,
    request: Request,
    storage: Annotated[HubStorage, Depends(get_storage)],
    config: Annotated[HubConfig, Depends(get_config)],
) -> dict:
    """Upload a blob, addressed by its own content hash."""
    digest = _validate_hash(blob_hash, "blob hash")

    if storage.has_object("blobs", digest):
        return {"hash": digest, "status": "already-present"}

    body = await request.body()

    if len(body) > config.max_blob_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Blob exceeds the {config.max_blob_bytes} byte limit.",
        )

    if not body:
        raise HTTPException(status_code=400, detail="Empty request body.")

    try:
        stored = storage.put_blob(digest, body)
    except HashMismatch as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AethelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"hash": stored, "status": "stored", "bytes": len(body)}


@router.put("/api/v1/{kind}/{object_hash}", dependencies=[Depends(require_write_auth)])
async def put_json_object(
    kind: str,
    object_hash: str,
    request: Request,
    storage: Annotated[HubStorage, Depends(get_storage)],
) -> dict:
    """Upload a commit, tree, or base object."""
    if kind not in JSON_KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown object kind '{kind}'.")

    digest = _validate_hash(object_hash, f"{kind[:-1]} hash")

    if storage.has_object(kind, digest):
        return {"hash": digest, "status": "already-present"}

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body.")

    try:
        stored = storage.put_json_object(kind, digest, body)
    except HashMismatch as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HubStorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"hash": stored, "status": "stored"}


@router.post("/api/v1/repos/{repo_name}/negotiate", dependencies=[Depends(require_write_auth)])
async def negotiate(
    repo_name: str,
    payload: dict,
    storage: Annotated[HubStorage, Depends(get_storage)],
) -> dict:
    """Report which of the client's objects the Hub is missing.

    Turns a push into an incremental sync: the client offers the hashes it
    holds, and uploads only what comes back as missing.
    """
    have = payload.get("have")
    if not isinstance(have, dict):
        raise HTTPException(status_code=400, detail="Expected a 'have' object.")

    cleaned: dict[str, list[str]] = {}
    for kind, hashes in have.items():
        if kind not in OBJECT_KINDS or not isinstance(hashes, list):
            continue
        cleaned[kind] = [h.lower() for h in hashes if isinstance(h, str) and is_valid_hash(h.lower())]

    return {"repo": repo_name, "missing": storage.missing_objects(cleaned)}


@router.post("/api/v1/repos/{repo_name}/refs", dependencies=[Depends(require_write_auth)])
async def update_ref(
    repo_name: str,
    payload: dict,
    storage: Annotated[HubStorage, Depends(get_storage)],
    log: Annotated[TransparencyLog, Depends(get_log)],
) -> dict:
    """Advance a branch and append its history to the transparency log.

    The ref moves only after every object it reaches is present, so a published
    branch can never point at something a client cannot fetch.

    Ancestors are logged oldest-first so log order matches commit order. The
    append is idempotent, so re-pushing a branch adds only the genuinely new
    commits and leaves the root unchanged when there are none -- `logged_indices`
    reports where the whole history sits, `appended_indices` only what this call
    created.
    """
    branch = payload.get("branch")
    commit_hash = payload.get("commit")

    if not isinstance(branch, str) or not branch:
        raise HTTPException(status_code=400, detail="Expected a 'branch' name.")
    if not isinstance(commit_hash, str):
        raise HTTPException(status_code=400, detail="Expected a 'commit' hash.")

    digest = _validate_hash(commit_hash, "commit hash")

    try:
        history = storage.commit_history(digest)
    except ObjectNotFound as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AethelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logged = log.append_many(
        [commit["hash"] for commit in reversed(history)],
        repo=repo_name,
        accepted_at=_now(),
    )

    try:
        record = storage.set_branch(repo_name, branch, digest)
    except HubStorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "repo": repo_name,
        "branch": branch,
        "commit": digest,
        "logged_indices": logged.indices,
        "appended_indices": logged.added,
        "log_size": log.size(),
        "root": log.root(),
        "branches": record.branches,
    }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@router.get("/api/v1/repos")
async def list_repos(storage: Annotated[HubStorage, Depends(get_storage)]) -> dict:
    return {"repos": [record.to_dict() for record in storage.repos().values()]}


@router.get("/api/v1/repos/{repo_name}")
async def get_repo(
    repo_name: str,
    storage: Annotated[HubStorage, Depends(get_storage)],
) -> dict:
    record = storage.repo(repo_name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No repository '{repo_name}'.")
    return record.to_dict()


@router.get("/api/v1/repos/{repo_name}/commits")
async def get_repo_commits(
    repo_name: str,
    storage: Annotated[HubStorage, Depends(get_storage)],
    branch: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    record = storage.repo(repo_name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No repository '{repo_name}'.")

    targets = record.branches
    if branch is not None:
        # Checked before the lookup: indexing first turns an unknown branch
        # into a KeyError and a 500, which reads as a broken Hub rather than a
        # bad request.
        if branch not in record.branches:
            raise HTTPException(status_code=404, detail=f"No branch '{branch}'.")
        targets = {branch: record.branches[branch]}

    seen: set[str] = set()
    commits: list[dict] = []

    for tip in targets.values():
        for commit in storage.commit_history(tip, limit=limit):
            if commit["hash"] not in seen:
                seen.add(commit["hash"])
                commits.append(commit)

    commits.sort(key=lambda c: c.get("timestamp", ""), reverse=True)
    return {"repo": repo_name, "commits": commits[:limit]}


@router.get("/api/v1/commits/{commit_hash}")
async def get_commit(
    commit_hash: str,
    storage: Annotated[HubStorage, Depends(get_storage)],
) -> dict:
    digest = _validate_hash(commit_hash, "commit hash")
    try:
        return {"hash": digest, **storage.read_commit(digest)}
    except AethelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/v1/blobs/{blob_hash}")
async def get_blob(
    blob_hash: str,
    storage: Annotated[HubStorage, Depends(get_storage)],
) -> FileResponse:
    """Download a blob.

    The client is expected to re-hash what it receives and compare against the
    hash it asked for. That check is what lets any transport — this Hub, an
    IPFS gateway, a USB stick — be untrusted without risking integrity.
    """
    digest = _validate_hash(blob_hash, "blob hash")

    try:
        path = storage.blob_path(digest)
    except AethelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={
            "X-Aethel-Blob-Sha256": digest,
            "Content-Disposition": f'attachment; filename="{digest[:12]}.bin"',
        },
    )


@router.get("/api/v1/trees/{tree_hash}")
async def get_tree(
    tree_hash: str,
    storage: Annotated[HubStorage, Depends(get_storage)],
) -> dict:
    digest = _validate_hash(tree_hash, "tree hash")
    try:
        return {"hash": digest, "files": storage.read_tree(digest)}
    except AethelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/v1/bases/{base_hash}")
async def get_base(
    base_hash: str,
    storage: Annotated[HubStorage, Depends(get_storage)],
) -> dict:
    digest = _validate_hash(base_hash, "base hash")
    try:
        return {"hash": digest, **storage.read_base(digest)}
    except AethelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Transparency log
# ---------------------------------------------------------------------------


@router.get("/api/v1/log")
async def get_log_state(
    log: Annotated[TransparencyLog, Depends(get_log)],
    anchors: Annotated[AnchorStore, Depends(get_anchors)],
) -> dict:
    latest = anchors.latest()
    root = log.root()

    return {
        "size": log.size(),
        "root": root,
        "entries": [
            {
                "index": e.index,
                "commit_hash": e.commit_hash,
                "repo": e.repo,
                "accepted_at": e.accepted_at,
            }
            for e in log.entries()
        ],
        "last_anchor": latest,
        "current_root_anchored": bool(latest and latest.get("root") == root),
    }


@router.get("/api/v1/log/proof/{commit_hash}")
async def get_inclusion_proof(
    commit_hash: str,
    log: Annotated[TransparencyLog, Depends(get_log)],
) -> dict:
    """Serve an inclusion proof for a commit.

    The response is self-contained: leaf, sibling path, root, and log size.
    A verifier recomputes the root from those alone — it never calls back here,
    which is exactly why a dishonest Hub cannot fake inclusion.
    """
    digest = _validate_hash(commit_hash, "commit hash")

    try:
        proof = log.inclusion_proof(digest)
    except InvalidHash as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if proof is None:
        raise HTTPException(
            status_code=404,
            detail=f"Commit {digest[:12]} is not in the transparency log.",
        )

    return proof


@router.post("/api/v1/log/verify")
async def verify_inclusion(payload: dict) -> dict:
    """Verify a proof server-side, for parity with the browser verifier.

    Convenience and a test hook — never the authoritative check. Asking the Hub
    whether the Hub is honest proves nothing; the real verification runs
    client-side against the on-chain root.
    """
    commit_hash = payload.get("commit_hash")
    proof = payload.get("proof")
    root = payload.get("root")

    if not isinstance(commit_hash, str) or not isinstance(proof, list) or not isinstance(root, str):
        raise HTTPException(
            status_code=400, detail="Expected 'commit_hash', 'proof', and 'root'."
        )

    valid = TransparencyLog.verify(commit_hash, proof, root)
    return {"valid": valid, "commit_hash": commit_hash, "root": root}


# ---------------------------------------------------------------------------
# Health and version (backing the ops board)
# ---------------------------------------------------------------------------


@router.get("/api/v1/health")
async def health(
    request: Request,
    config: Annotated[HubConfig, Depends(get_config)],
    storage: Annotated[HubStorage, Depends(get_storage)],
    log: Annotated[TransparencyLog, Depends(get_log)],
    anchors: Annotated[AnchorStore, Depends(get_anchors)],
) -> JSONResponse:
    """Per-subsystem health.

    Every check is wrapped so one dead dependency degrades its own row rather
    than failing the page. An ops board that cannot render during an incident
    is useless precisely when it is needed.
    """
    checks: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    # Object store writability
    try:
        counts = storage.object_counts()
        probe = config.data_dir / ".health-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        total = sum(counts.values())
        add("Object store", "good", f"{total} objects, writable")
    except Exception as exc:
        counts = {}
        add("Object store", "critical", f"unwritable: {type(exc).__name__}")

    # Sampled integrity
    try:
        sample = storage.verify_sample()
        if sample["corrupt"]:
            add(
                "Integrity sample",
                "critical",
                f"{len(sample['corrupt'])} corrupt of {sample['checked']} sampled",
            )
        else:
            add("Integrity sample", "good", f"{sample['checked']} objects re-hashed, all match")
    except Exception as exc:
        add("Integrity sample", "warning", f"could not sample: {type(exc).__name__}")

    # Repository index
    try:
        repos = storage.repos()
        add("Repository index", "good", f"{len(repos)} repositories")
    except Exception as exc:
        add("Repository index", "critical", f"unreadable: {type(exc).__name__}")

    # Transparency log, and the self-audit row
    root = None
    try:
        root = log.root()
        add("Transparency log", "good", f"{log.size()} leaves, root {(root or '—')[:12]}")
    except Exception as exc:
        add("Transparency log", "critical", f"unreadable: {type(exc).__name__}")

    # The most valuable row: does the log still match what was anchored?
    try:
        latest = anchors.latest()
        if latest is None:
            add(
                "Log vs anchored root",
                "warning",
                "never anchored — chain layer not yet enabled",
            )
        elif latest.get("root") == root:
            add("Log vs anchored root", "good", f"matches anchor at block {latest.get('block', '?')}")
        else:
            add(
                "Log vs anchored root",
                "critical",
                "RECOMPUTED ROOT DOES NOT MATCH THE ANCHORED ROOT — history may have been altered",
            )
    except Exception as exc:
        add("Log vs anchored root", "warning", f"could not compare: {type(exc).__name__}")

    # Chain RPC — reported, not dialled. A health endpoint must not block on a
    # third-party network call.
    if config.chain_configured:
        add("Chain", "good", f"configured: chain id {config.chain_id}, contract set")
    else:
        add("Chain", "warning", "not configured (AETHEL_CHAIN_RPC, AETHEL_ANCHOR_CONTRACT)")

    if config.pinning_endpoint:
        add("IPFS mirror", "good", "pinning endpoint configured")
    else:
        add("IPFS mirror", "warning", "not configured — Hub is the only copy")

    add(
        "Write auth",
        "good" if config.auth_required else "warning",
        "token required" if config.auth_required else "open — no push token set",
    )

    worst = "good"
    for check in checks:
        if check["status"] == "critical":
            worst = "critical"
            break
        if check["status"] == "warning":
            worst = "warning"

    return JSONResponse(
        status_code=200 if worst != "critical" else 503,
        content={"status": worst, "checks": checks, "counts": counts},
    )


@router.get("/api/v1/version")
async def version(request: Request, config: Annotated[HubConfig, Depends(get_config)]) -> dict:
    """What is actually deployed — so a stale process is visible, not guessed."""
    return {
        "hub": request.app.state.hub_version,
        "git_sha": request.app.state.git_sha,
        "started_at": request.app.state.started_at,
        "data_dir": str(config.data_dir),
        "chain_configured": config.chain_configured,
        "auth_required": config.auth_required,
    }
