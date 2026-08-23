"""Hub configuration, driven entirely by environment variables.

Nothing here is hard-coded to a host or a port. The Hub may run on a
teammate's laptop, an Oracle Cloud VM, or behind a tunnel, and the demo
machine decides at run time via `AETHEL_HUB_URL` — so a deployment choice
never requires a code change.

Chain settings are read but unused until the anchoring layer lands. They live
here now so that the ops board can already report "not configured" rather than
crashing, and so the deployment story is settled before the code arrives.

**No credential this file reads can write to a third party.** The Pinata token
and the wallet key that will sign anchor transactions are deliberately absent:
they belong to the command-line tools that pin and anchor, not to the process
that serves HTML to a browser. A web process that holds no outbound credential
has none to leak, and the split costs nothing because pinning and anchoring are
already client-side operations. What the Hub gets instead is `AETHEL_IPFS_GATEWAY`
— a public URL, useful for building links, worthless to an attacker.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from aethel.core.env import load_env


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class HubConfig:
    """Runtime configuration for a Hub instance."""

    #: Where the Hub keeps its own object store and log. Separate from any
    #: local .aethel repository -- the Hub is a peer, not a working copy.
    data_dir: Path = field(default_factory=lambda: _env_path("AETHEL_HUB_DATA", "./hub-data"))

    #: Bind address. 0.0.0.0 so the two demo machines can reach it over a LAN;
    #: override to 127.0.0.1 to keep it local.
    host: str = field(default_factory=lambda: os.environ.get("AETHEL_HUB_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("AETHEL_HUB_PORT", 8000))

    #: Largest accepted blob. Adapters are ~0.6 MB for distilbert at rank 8;
    #: the ceiling is generous but finite so one request cannot exhaust disk.
    max_blob_bytes: int = field(
        default_factory=lambda: _env_int("AETHEL_HUB_MAX_BLOB", 64 * 1024 * 1024)
    )

    #: Optional shared secret. When set, every write endpoint requires it via
    #: the Authorization header. Unset means an open Hub, which is fine for a
    #: local demo and stated plainly on the ops board rather than hidden.
    push_token: str | None = field(
        default_factory=lambda: os.environ.get("AETHEL_HUB_TOKEN") or None
    )

    # -- chain settings (read now, used by the anchoring layer later) --------

    #: Never hard-code a network. Testnets get retired -- Ropsten, Rinkeby,
    #: Kovan and Goerli are all gone -- so the chain is configuration.
    chain_rpc_url: str | None = field(
        default_factory=lambda: os.environ.get("AETHEL_CHAIN_RPC") or None
    )
    chain_id: int | None = field(
        default_factory=lambda: (
            int(os.environ["AETHEL_CHAIN_ID"])
            if os.environ.get("AETHEL_CHAIN_ID", "").strip().isdigit()
            else None
        )
    )
    anchor_contract: str | None = field(
        default_factory=lambda: os.environ.get("AETHEL_ANCHOR_CONTRACT") or None
    )

    # -- IPFS settings (read now, used by the mirror later) -----------------

    #: A pinning service is a location, never a source of truth. Integrity
    #: always comes from blob_sha256, verified on every fetch, so a mirror
    #: being absent or wrong can never corrupt the Hub.
    pinning_endpoint: str | None = field(
        default_factory=lambda: os.environ.get("AETHEL_PINNING_ENDPOINT") or None
    )

    #: Public gateway used to build "fetch this CID elsewhere" links. Any
    #: gateway serves any CID, which is the property the demo shows off, so
    #: which one is configured is a convenience rather than a dependency.
    ipfs_gateway: str | None = field(
        default_factory=lambda: os.environ.get("AETHEL_IPFS_GATEWAY") or None
    )

    @classmethod
    def from_environment(cls) -> "HubConfig":
        """Build a config, loading a `.env` first if one is present.

        Only this constructor touches the filesystem looking for settings.
        `HubConfig()` stays a pure read of the current environment, which is what
        lets a test build a Hub without a stray `.env` two directories up
        changing the result.
        """
        load_env()
        return cls()

    @property
    def objects_dir(self) -> Path:
        return self.data_dir / "objects"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "log.jsonl"

    @property
    def anchors_path(self) -> Path:
        return self.data_dir / "anchors.jsonl"

    @property
    def auth_required(self) -> bool:
        return self.push_token is not None

    @property
    def chain_configured(self) -> bool:
        return bool(self.chain_rpc_url and self.anchor_contract)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)
