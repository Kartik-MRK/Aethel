"""Publish the prepared review adapters to IPFS and checkpoint their log on Sepolia.

Uses the existing worker credentials. Persists public configuration and receipts;
re-running resumes pending transactions and reuses pins. Sends no mainnet transactions.
"""

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def public_settings(target: Path) -> dict:
    from aethel.core.env import parse_env

    path = target / "hub.env"
    if path.exists():
        return parse_env(path.read_text())
    values = {
        "AETHEL_HUB_DATA": str(target / "hub-data"),
        "AETHEL_HUB_HOST": "127.0.0.1", "AETHEL_HUB_PORT": "8787",
        "AETHEL_HUB_TOKEN": secrets.token_urlsafe(32),
        "AETHEL_CHAIN_RPC": "https://ethereum-sepolia-rpc.publicnode.com",
        "AETHEL_CHAIN_ID": "11155111", "AETHEL_CHAIN_CONFIRMATIONS": "2",
        "AETHEL_CHAIN_EXPLORER": "https://sepolia.etherscan.io",
        "AETHEL_LOG_ID": secrets.token_hex(32),
        "AETHEL_ANCHOR_CONTRACT": "",
        "AETHEL_IPFS_GATEWAY": "https://gateway.pinata.cloud/ipfs",
        "AETHEL_IPFS_FALLBACK_GATEWAY": "https://ipfs.io/ipfs",
        "AETHEL_PROBE_INTERVAL": "60",
    }
    save_settings(path, values)
    return values


def save_settings(path: Path, values: dict):
    # Contains a Hub write token, but no wallet key or Pinata credential.
    from aethel.core.atomic import atomic_write_text

    atomic_write_text(path, "".join(f"{key}={value}\n" for key, value in values.items()))
    path.chmod(0o600)


def main():
    from aethel.core.env import load_env
    from aethel.provenance.chain import ChainClient
    from aethel.provenance.ipfs import PinningClient, mirror_blob
    from aethel.provenance.state import StateStore
    from hub.log import AnchorStore, TransparencyLog
    from hub.storage import HubStorage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / ".demo" / "public-review")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--gateway", help="Public or dedicated IPFS gateway base ending in /ipfs.")
    args = parser.parse_args()
    target = args.dir.resolve()
    if not (target / "models.json").is_file():
        raise SystemExit("Run scripts/prepare_review_models.py first")
    config = public_settings(target)
    if args.gateway:
        from aethel.provenance.ipfs import validate_url

        config["AETHEL_IPFS_GATEWAY"] = validate_url(args.gateway)
        save_settings(target / "hub.env", config)
    if args.prepare_only:
        print("Prepared read-only chain configuration and a Hub token. No external writes performed.")
        return
    load_env(ROOT / ".env")
    key = os.environ.get("AETHEL_ANCHOR_KEY")
    token = os.environ.get("AETHEL_PINATA_JWT")
    if not key or not token:
        raise SystemExit("The worker requires AETHEL_ANCHOR_KEY and AETHEL_PINATA_JWT")
    store = StateStore(target / "hub-data" / "provenance.sqlite3")
    chain = ChainClient(config["AETHEL_CHAIN_RPC"], 11155111, config.get("AETHEL_ANCHOR_CONTRACT") or None, config["AETHEL_LOG_ID"], confirmations=2)
    if chain.address is None:
        for _ in range(90):
            deployed = chain.deploy(key, store, max_fee_gwei=10)
            if deployed["status"] == "confirmed":
                chain.address = deployed["contract"]
                config["AETHEL_ANCHOR_CONTRACT"] = chain.address
                save_settings(target / "hub.env", config)
                (target / "deployment.json").write_text(json.dumps(deployed, indent=2) + "\n")
                print(f"Contract confirmed: {chain.address}", flush=True)
                break
            time.sleep(2)
        else:
            raise SystemExit("Deployment remains pending; rerun to resume the saved transaction")
    log = TransparencyLog(target / "hub-data" / "log.jsonl")
    anchors = AnchorStore(target / "hub-data" / "anchors.jsonl")
    for _ in range(90):
        checkpoint = chain.publish(log, anchors, store, key, max_fee_gwei=10)
        if checkpoint["status"] == "confirmed":
            break
        time.sleep(2)
    else:
        raise SystemExit("Checkpoint remains pending; rerun to resume")
    print(f"Checkpoint confirmed: {checkpoint['root']}, {checkpoint['size']} entries", flush=True)
    storage = HubStorage(target / "hub-data")
    pins = PinningClient("https://api.pinata.cloud", token=token)
    mirrors = []
    for digest in sorted({storage.read_commit(value)["adapter_blob"] for value in log.snapshot()}):
        record = mirror_blob(store, storage.objects, digest, pins, [config["AETHEL_IPFS_GATEWAY"], config["AETHEL_IPFS_FALLBACK_GATEWAY"]])
        print(f"IPFS {record['status']}: {digest} -> {record.get('cid', 'no CID')}", flush=True)
        mirrors.append(record)
    leaves = log.snapshot()
    bundle = {"schema": 1, "checkpoint": checkpoint, "proof": log.inclusion_proof(leaves[0], size=checkpoint["size"]), "leaves": leaves}
    (target / f"checkpoint-{checkpoint['size']}.json").write_text(json.dumps(bundle, indent=2) + "\n")
    (target / "publication.json").write_text(json.dumps({"checkpoint": checkpoint, "mirrors": mirrors}, indent=2) + "\n")
    if any(record["status"] != "verified" for record in mirrors):
        raise SystemExit("Some gateway retrievals failed; CID records are retained for retry")
    print("Public checkpoint and IPFS retrieval verified. Start the dashboard with scripts/serve_review.py.", flush=True)


if __name__ == "__main__":
    main()
