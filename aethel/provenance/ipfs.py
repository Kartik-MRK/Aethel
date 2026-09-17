"""Pin adapters and verify gateway bytes before accepting a mirror."""

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aethel.core.atomic import file_lock
from aethel.core.errors import AethelError
from aethel.core.hashing import hash_file, normalize_hash
from aethel.provenance.state import StateStore, utc_now


class MirrorError(AethelError):
    pass


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and local)) or not parsed.hostname:
        raise MirrorError("Use HTTPS, or HTTP on localhost for a local IPFS node")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MirrorError("Endpoint URLs must not contain credentials, queries, or fragments")
    return value.rstrip("/")


def validate_cid(cid: str) -> str:
    if not isinstance(cid, str) or not re.fullmatch(r"(?:Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{20,120})", cid):
        raise MirrorError("The pinning service returned an unsupported CID")
    return cid


def gateway_url(gateway: str, cid: str) -> str:
    return f"{validate_url(gateway)}/{validate_cid(cid)}"


def retrieve_verified(
    cid: str, digest: str, gateways: list[str], destination: Path, *,
    max_bytes: int = 64 * 1024 * 1024, timeout: float = 15, transport=None,
) -> dict:
    """Try each configured gateway; install only a complete hash-matching download."""
    digest = normalize_hash(digest)
    validate_cid(cid)
    if not gateways:
        raise MirrorError("No IPFS retrieval gateway configured")
    if max_bytes <= 0:
        raise MirrorError("Download size limit must be positive")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    failures = []
    with httpx.Client(timeout=timeout, follow_redirects=False, transport=transport) as client:
        for gateway in dict.fromkeys(gateways):
            url = gateway_url(gateway, cid)
            fd, temporary = tempfile.mkstemp(prefix=".ipfs-", dir=destination.parent)
            try:
                started = time.monotonic()
                total, hasher = 0, hashlib.sha256()
                with os.fdopen(fd, "wb") as handle, client.stream("GET", url) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes(64 * 1024):
                        if time.monotonic() - started > timeout * 3:
                            raise MirrorError("Gateway download exceeded its total time limit")
                        total += len(chunk)
                        if total > max_bytes:
                            raise MirrorError("Gateway response exceeds the configured size limit")
                        hasher.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if hasher.hexdigest() != digest:
                    raise MirrorError("Gateway bytes do not match the expected SHA-256")
                os.replace(temporary, destination)
                return {"gateway": gateway, "bytes": total, "sha256": digest, "verified_at": utc_now()}
            except (httpx.HTTPError, MirrorError) as exc:
                # Never include response bodies or authenticated request URLs in persisted errors.
                status = getattr(getattr(exc, "response", None), "status_code", None)
                failures.append(f"HTTP {status}" if status else str(exc) if isinstance(exc, MirrorError) else type(exc).__name__)
            finally:
                Path(temporary).unlink(missing_ok=True)
    raise MirrorError(f"No gateway returned verified bytes ({', '.join(failures)})")


class PinningClient:
    def __init__(self, endpoint: str, *, backend: str = "pinata", token: str | None = None, timeout: float = 30, transport=None):
        self.endpoint = validate_url(endpoint)
        if backend not in {"pinata", "kubo"}:
            raise MirrorError("Pinning backend must be pinata or kubo")
        if backend == "pinata" and not token:
            raise MirrorError("Set AETHEL_PINATA_JWT in the worker environment")
        self.backend, self.token, self.timeout, self.transport = backend, token if backend == "pinata" else None, timeout, transport

    def pin(self, path: Path, digest: str) -> str:
        if hash_file(path) != normalize_hash(digest):
            raise MirrorError("Local blob failed SHA-256 verification before pinning")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=False) as client, Path(path).open("rb") as handle:
                if self.backend == "pinata":
                    response = client.post(
                        self.endpoint + "/pinning/pinFileToIPFS", headers=headers,
                        files={"file": (digest, handle, "application/octet-stream")},
                        data={"pinataOptions": '{"cidVersion":1}', "pinataMetadata": json.dumps({"name": digest, "keyvalues": {"sha256": digest}})},
                    )
                    response.raise_for_status()
                    cid = response.json()["IpfsHash"]
                else:
                    response = client.post(
                        self.endpoint + "/api/v0/add", headers=headers,
                        params={"pin": "true", "cid-version": "1", "raw-leaves": "true", "wrap-with-directory": "false"},
                        files={"file": (digest, handle, "application/octet-stream")},
                    )
                    response.raise_for_status()
                    cid = response.json()["Hash"]
                return validate_cid(cid)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise MirrorError(f"Pinning failed ({'HTTP ' + str(status) if status else type(exc).__name__})") from None


def mirror_blob(store: StateStore, objects, digest: str, client: PinningClient, gateways: list[str], *, max_bytes=64 * 1024 * 1024, transport=None) -> dict:
    digest = normalize_hash(digest)
    with file_lock(store.path.parent / f"mirror-{digest}.lock"):
        previous = store.get("mirrors", digest) or {}
        record = {**previous, "sha256": digest, "provider": client.backend, "attempts": previous.get("attempts", 0) + 1}
        try:
            path = objects.blob_path(digest)
            if path.stat().st_size > max_bytes:
                raise MirrorError("Blob exceeds mirror size limit")
            if not record.get("cid"):
                store.put("mirrors", digest, {**record, "status": "pinning"})
                record["cid"] = client.pin(path, digest)
                store.put("mirrors", digest, {**record, "status": "pinned"})
            with tempfile.TemporaryDirectory(prefix="aethel-mirror-") as temporary:
                verified = retrieve_verified(record["cid"], digest, gateways, Path(temporary) / "blob", max_bytes=max_bytes, transport=transport)
            record.update(verified)
            record.update(status="verified", error=None)
        except (AethelError, OSError) as exc:
            record.update(status="failed", error=str(exc) if isinstance(exc, MirrorError) else type(exc).__name__)
        return store.put("mirrors", digest, record)
