"""Read-only background checks and dashboard data for external provenance."""

import asyncio
import tempfile
import time
from pathlib import Path

from aethel.provenance.checkpoint import CheckpointError, checked_prefix
from aethel.provenance.ipfs import MirrorError, retrieve_verified
from aethel.provenance.state import StateStore, utc_now


class ProvenanceMonitor:
    def __init__(self, config, log):
        self.config, self.log = config, log
        self.state = StateStore(config.provenance_path)
        self.chain = {"status": "pending" if config.chain_configured else "unconfigured", "detail": "Waiting for the first RPC check" if config.chain_configured else "Configure a chain, contract, and log ID"}
        self.gateway = {"status": "pending" if config.ipfs_gateways else "unconfigured", "detail": "No verified gateway sample yet"}
        self.checked = 0.0
        # Confirmed earlier checkpoints do not change. Reusing them keeps a repeating
        # probe to a fixed number of RPC calls instead of one per prior checkpoint.
        self.chain_cache: dict = {}

    def check_chain(self) -> dict:
        if not self.config.chain_configured:
            return self.chain
        try:
            from aethel.provenance.chain import ChainClient

            chain = ChainClient(self.config.chain_rpc_url, self.config.chain_id, self.config.anchor_contract, self.config.log_id, confirmations=self.config.chain_confirmations, cache=self.chain_cache)
            size = chain.latest_size()
            if not size:
                return {"status": "pending", "detail": "RPC and contract checked; no checkpoint published", "checked_at": utc_now()}
            leaves = self.log.snapshot()
            root = checked_prefix(leaves, size)
            record = chain.verify(size, root, leaves=leaves)
            return {**record, "detail": f"{size} entries checked against the chain; {max(0, len(leaves) - size)} newer entries"}
        except CheckpointError as exc:
            status = "pending" if "pending:" in str(exc) else "failed"
            return {"status": status, "detail": str(exc), "checked_at": utc_now()}
        except Exception as exc:
            return {"status": "unavailable", "detail": f"Chain check unavailable ({type(exc).__name__})", "checked_at": utc_now()}

    def check_gateway(self) -> dict:
        if not self.config.ipfs_gateways:
            return self.gateway
        records = [r for r in self.state.records("mirrors") if r.get("cid")]
        if not records:
            return {"status": "pending", "detail": "No mirrored adapter available to probe", "checked_at": utc_now()}
        # Rotate the sample; no request blocks rendering, and every download is bounded.
        record = records[int(time.time() // self.config.probe_interval) % len(records)]
        try:
            with tempfile.TemporaryDirectory(prefix="aethel-probe-") as temporary:
                result = retrieve_verified(record["cid"], record["sha256"], self.config.ipfs_gateways, Path(temporary) / "blob", timeout=5, max_bytes=self.config.max_blob_bytes)
            return {**result, "status": "verified", "detail": "Sampled adapter downloaded and SHA-256 verified", "cid": record["cid"]}
        except MirrorError as exc:
            return {"status": "unavailable", "detail": str(exc), "checked_at": utc_now()}
        except Exception as exc:
            return {"status": "unavailable", "detail": f"Gateway sample failed ({type(exc).__name__})", "checked_at": utc_now()}

    async def run(self):
        while True:
            chain, gateway = await asyncio.gather(asyncio.to_thread(self.check_chain), asyncio.to_thread(self.check_gateway))
            self.chain, self.gateway = chain, gateway
            self.checked = time.monotonic()
            await asyncio.sleep(self.config.probe_interval)

    def summary(self) -> dict:
        chain, gateway = dict(self.chain), dict(self.gateway)
        if self.checked and time.monotonic() - self.checked > max(120, self.config.probe_interval * 3):
            for result in (chain, gateway):
                if result["status"] in {"confirmed", "verified"}:
                    result.update(status="stale", detail="Last check is stale; waiting for a fresh probe")
        mirrors = self.state.records("mirrors")
        checkpoints = sorted(self.state.records("checkpoints"), key=lambda r: r.get("size", 0), reverse=True)
        return {
            "chain": chain, "gateway": gateway, "mirrors": mirrors, "checkpoints": checkpoints,
            "mirrored_count": sum(r.get("status") == "verified" for r in mirrors),
            "failed_count": sum(r.get("status") == "failed" for r in mirrors),
            "chain_id": self.config.chain_id, "contract": self.config.anchor_contract,
            "log_id": self.config.log_id, "explorer": self.config.explorer_url,
        }


def summary(request) -> dict:
    application = request.scope.get("app")
    monitor = getattr(getattr(application, "state", None), "provenance", None)
    if monitor:
        return monitor.summary()
    return {"chain": {"status": "unconfigured", "detail": "No chain monitor"}, "gateway": {"status": "unconfigured", "detail": "No gateway monitor"}, "mirrors": [], "checkpoints": [], "mirrored_count": 0, "failed_count": 0}
