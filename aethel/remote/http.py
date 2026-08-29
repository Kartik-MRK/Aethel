"""HTTP transport to a Hub.

Knows nothing about repositories -- it is handed hashes and bytes. Keeping it
repository-blind is what lets the same client serve `push` today and a `pull`
or a mirror check later without growing conditionals.

Two rules shape every method:

**The hash goes in the URL, and the response is checked against it.** Uploads
are addressed by the hash the client claims; the Hub recomputes it from the
bytes and refuses a mismatch. This client then re-checks the hash the Hub
echoes back, so a proxy that rewrote the body in flight is caught on both ends.

**A failure names the object.** "413 on blob a1b2c3d4 (adapter_model.safetensors,
612 KB)" is actionable; "HTTP 413" during a 40-object push is not.
"""

from collections.abc import Iterator
from pathlib import Path

from aethel.core.errors import AethelError

#: Generous enough for a slow LAN upload of a multi-megabyte adapter, short
#: enough that an unreachable host fails during the demo rather than hanging.
DEFAULT_TIMEOUT = 30.0

API = "/api/v1"


class RemoteError(AethelError):
    """A Hub request failed. Rendered by the command layer like any other."""


def _require_httpx():
    """Import httpx with an actionable message if it is absent.

    Imported lazily so that `aethel --help`, `log`, `commit` and every other
    offline command keep working on an install that predates this dependency.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RemoteError(
            "Pushing needs httpx. Install it with:\n  pip install 'httpx>=0.27'"
        ) from exc
    return httpx


class HubClient:
    """A client for one Hub, over HTTP."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._client = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "HubClient":
        httpx = _require_httpx()
        headers = {"User-Agent": "aethel-cli"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout,
            follow_redirects=True,
        )
        return self

    def __exit__(self, *exc_info) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # -- internals ---------------------------------------------------------

    def _request(self, method: str, path: str, *, what: str, **kwargs):
        """Issue a request, turning transport and HTTP errors into RemoteError.

        Every failure mode a demo actually hits gets its own message: nothing
        listening, wrong host, missing token, body rejected. A bare stack trace
        from httpx in front of a panel is a wasted minute.
        """
        httpx = _require_httpx()

        if self._client is None:
            raise RemoteError("HubClient must be used as a context manager.")

        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise RemoteError(
                f"Cannot reach the Hub at {self.base_url}. Is it running?\n"
                f"  Start it with: python -m hub  (or scripts/dev.sh)"
            ) from exc
        except httpx.TimeoutException as exc:
            raise RemoteError(
                f"Timed out after {self.timeout:g}s talking to {self.base_url} ({what})."
            ) from exc
        except httpx.HTTPError as exc:
            raise RemoteError(f"Transport error talking to {self.base_url} ({what}): {exc}") from exc

        if response.status_code == 401:
            raise RemoteError(
                f"The Hub rejected the push token ({what}).\n"
                f"  Set it with: export AETHEL_HUB_TOKEN=<token>  (or --token)"
            )

        if response.status_code >= 400:
            raise RemoteError(f"{what} failed: HTTP {response.status_code} {_detail(response)}")

        return response

    # -- endpoints ---------------------------------------------------------

    def version(self) -> dict:
        """Identify the remote, and confirm it is actually a Hub.

        Called before uploading so that pointing `--remote` at the wrong port
        fails immediately with a clear message rather than after the first
        object upload.
        """
        response = self._request("GET", f"{API}/version", what="version check")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RemoteError(
                f"{self.base_url} responded to {API}/version with non-JSON, "
                f"that does not look like an Aethel Hub."
            ) from exc

        if not isinstance(payload, dict) or "hub" not in payload:
            raise RemoteError(
                f"{self.base_url} answered {API}/version but not like an Aethel Hub."
            )

        return payload

    def negotiate(self, repo: str, have: dict[str, list[str]]) -> dict[str, list[str]]:
        """Ask which of these objects the Hub is missing."""
        response = self._request(
            "POST",
            f"{API}/repos/{repo}/negotiate",
            what="negotiation",
            json={"have": have},
        )
        missing = response.json().get("missing", {})

        if not isinstance(missing, dict):
            raise RemoteError("Hub returned a malformed negotiation response.")

        return missing

    def put_blob(self, blob_hash: str, path: Path) -> dict:
        """Upload one blob, streamed from disk.

        Streamed rather than read into memory: adapters are small today, but a
        rank-64 adapter on a larger base is tens of megabytes and there is no
        reason for the client to hold it all at once.
        """
        size = path.stat().st_size

        with open(path, "rb") as handle:
            response = self._request(
                "PUT",
                f"{API}/blobs/{blob_hash}",
                what=f"upload of blob {blob_hash[:12]} ({_human(size)})",
                content=handle,
                headers={"Content-Type": "application/octet-stream"},
            )

        return _verify_echo(response, blob_hash, "blob")

    def put_object(self, kind: str, object_hash: str, data: bytes) -> dict:
        """Upload a commit, tree, or base as its exact stored bytes.

        The stored bytes are sent verbatim rather than re-serialized from a
        parsed dict. Re-serializing risks a different byte sequence -- one
        whitespace or escaping difference -- and therefore a different hash,
        which the Hub would correctly reject.
        """
        response = self._request(
            "PUT",
            f"{API}/{kind}/{object_hash}",
            what=f"upload of {kind[:-1]} {object_hash[:12]}",
            content=data,
            headers={"Content-Type": "application/json"},
        )
        return _verify_echo(response, object_hash, kind[:-1])

    def update_ref(self, repo: str, branch: str, commit_hash: str) -> dict:
        """Advance the remote branch. Done last, once every object is present.

        The Hub re-walks the history before moving the ref, so this call is
        also the remote's completeness check: if anything failed to upload, the
        ref does not move and the published branch stays valid.
        """
        response = self._request(
            "POST",
            f"{API}/repos/{repo}/refs",
            what=f"ref update {repo}/{branch}",
            json={"branch": branch, "commit": commit_hash},
        )
        return response.json()

    def inclusion_proof(self, commit_hash: str) -> dict | None:
        """Fetch a commit's inclusion proof, or None if it is not logged."""
        httpx = _require_httpx()

        if self._client is None:
            raise RemoteError("HubClient must be used as a context manager.")

        try:
            response = self._client.get(f"{API}/log/proof/{commit_hash}")
        except httpx.HTTPError as exc:
            raise RemoteError(f"Could not fetch inclusion proof: {exc}") from exc

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RemoteError(
                f"Inclusion proof request failed: HTTP {response.status_code} {_detail(response)}"
            )

        return response.json()

    def fetch_blob(self, blob_hash: str) -> Iterator[bytes]:
        """Stream a blob down. The caller re-hashes; this client does not.

        Deliberate: verification belongs where the bytes land, so that the same
        check covers a Hub download, an IPFS gateway, and a file copied off a
        USB stick. A transport that vouched for itself would defeat the point.
        """
        httpx = _require_httpx()

        if self._client is None:
            raise RemoteError("HubClient must be used as a context manager.")

        try:
            with self._client.stream("GET", f"{API}/blobs/{blob_hash}") as response:
                if response.status_code >= 400:
                    raise RemoteError(
                        f"Could not download blob {blob_hash[:12]}: HTTP {response.status_code}"
                    )
                yield from response.iter_bytes()
        except httpx.HTTPError as exc:
            raise RemoteError(f"Could not download blob {blob_hash[:12]}: {exc}") from exc


def _verify_echo(response, expected: str, label: str) -> dict:
    """Check the hash the Hub echoes back matches what we asked it to store.

    Cheap, and it closes the one gap the server-side check cannot: a proxy or
    misconfigured gateway that answered on the Hub's behalf.
    """
    try:
        payload = response.json()
    except ValueError as exc:
        raise RemoteError(f"Hub returned non-JSON for {label} {expected[:12]}.") from exc

    returned = payload.get("hash")
    if returned != expected:
        raise RemoteError(
            f"Hub acknowledged {label} {returned or '<none>'} "
            f"but {expected[:12]} was uploaded. Refusing to trust this remote."
        )

    return payload


def _detail(response) -> str:
    """Pull FastAPI's `detail` out of an error body, falling back to text."""
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "").strip()[:200]

    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return str(payload)[:200]


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


__all__ = ["HubClient", "RemoteError", "DEFAULT_TIMEOUT"]
