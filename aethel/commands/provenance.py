"""Operate mirrors and checkpoints without exposing credentials to the Hub."""

import json
import os
from pathlib import Path
from typing import Annotated

import httpx
import typer

from aethel.commands._common import handle_errors
from aethel.core.env import load_env
from aethel.core.errors import AethelError
from aethel.core.hashing import hash_file, normalize_hash
from aethel.provenance.checkpoint import CheckpointError, verify_bundle
from aethel.provenance.ipfs import PinningClient, mirror_blob, retrieve_verified, validate_url
from aethel.provenance.state import StateStore

app = typer.Typer(no_args_is_help=True, help="Mirror adapter blobs and publish or verify EVM checkpoints.")


def settings(data_dir: Path | None = None):
    from hub.config import HubConfig

    load_env()
    config = HubConfig.from_environment()
    if data_dir is not None:
        config.data_dir = data_dir.expanduser().resolve()
    return config


def configured_chain(config, *, deploying=False):
    required = {
        "AETHEL_CHAIN_RPC": config.chain_rpc_url,
        "AETHEL_CHAIN_ID": config.chain_id,
        "AETHEL_LOG_ID": config.log_id,
    }
    if not deploying:
        required["AETHEL_ANCHOR_CONTRACT"] = config.anchor_contract
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise AethelError(f"Set {', '.join(missing)}")
    try:
        from aethel.provenance.chain import ChainClient

        return ChainClient(config.chain_rpc_url, config.chain_id, config.anchor_contract, config.log_id, confirmations=config.chain_confirmations)
    except ImportError as exc:
        raise AethelError("Install the provenance extra: pip install -e '.[provenance]'") from exc


def signing_key():
    key = os.environ.get("AETHEL_ANCHOR_KEY")
    if not key:
        raise AethelError("Set AETHEL_ANCHOR_KEY in the worker environment")
    return key


def emit(record):
    typer.echo(json.dumps(record, indent=2, allow_nan=False))


def chain_action(action):
    try:
        return action()
    except AethelError:
        raise
    except Exception as exc:
        # Provider exceptions may contain authenticated RPC URLs.
        raise CheckpointError(f"Chain operation failed ({type(exc).__name__}); inspect the saved job and retry") from None


@app.command()
@handle_errors
def deploy(data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None):
    """Deploy the compiled checkpoint contract using the worker's configured key."""
    config = settings(data_dir)
    chain = configured_chain(config, deploying=True)
    emit(chain_action(lambda: chain.deploy(signing_key(), StateStore(config.provenance_path), max_fee_gwei=float(os.environ.get("AETHEL_MAX_FEE_GWEI", "50")))))


@app.command()
@handle_errors
def anchor(data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None):
    """Snapshot the accepted log and publish or resume a checkpoint transaction."""
    from hub.log import AnchorStore, TransparencyLog

    config = settings(data_dir)
    chain = configured_chain(config)
    record = chain_action(lambda: chain.publish(
        TransparencyLog(config.log_path), AnchorStore(config.anchors_path), StateStore(config.provenance_path),
        signing_key(), max_fee_gwei=float(os.environ.get("AETHEL_MAX_FEE_GWEI", "50")),
    ))
    emit(record)


@app.command()
@handle_errors
def mirror(blob_hash: str | None = typer.Argument(None), data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None):
    """Pin logged adapter blobs, retry failures, and verify downloaded copies."""
    from hub.log import TransparencyLog
    from hub.storage import HubStorage

    config = settings(data_dir)
    if not config.pinning_endpoint:
        raise AethelError("Set AETHEL_PINNING_ENDPOINT in the worker environment")
    client = PinningClient(config.pinning_endpoint, backend=os.environ.get("AETHEL_PINNING_BACKEND", "pinata"), token=os.environ.get("AETHEL_PINATA_JWT"))
    storage = HubStorage(config.data_dir)
    eligible = {
        storage.read_commit(digest)["adapter_blob"]
        for digest in TransparencyLog(config.log_path).snapshot()
    }
    if blob_hash:
        digest = normalize_hash(blob_hash)
        if digest not in eligible:
            raise AethelError("Only adapter blobs from accepted commits can be mirrored")
        eligible = {digest}
    state = StateStore(config.provenance_path)
    records = [mirror_blob(state, storage.objects, digest, client, config.ipfs_gateways, max_bytes=config.max_blob_bytes) for digest in sorted(eligible)]
    emit(records)
    if any(record["status"] != "verified" for record in records):
        raise typer.Exit(code=1)


@app.command()
@handle_errors
def verify(
    commit_hash: str,
    rpc: str = typer.Option(..., "--rpc", help="Independently chosen RPC endpoint."),
    chain_id: int = typer.Option(..., "--chain-id"),
    contract: str = typer.Option(..., "--contract"),
    log_id: str = typer.Option(..., "--log-id"),
    bundle: Annotated[Path | None, typer.Option("--bundle")] = None,
    hub: str | None = typer.Option(None, "--hub"),
    confirmations: int = typer.Option(2, "--confirmations", min=1),
):
    """Verify inclusion and all earlier prefixes against a separately trusted chain."""
    from aethel.provenance.chain import ChainClient

    digest = normalize_hash(commit_hash)
    chain = ChainClient(rpc, chain_id, contract, log_id, confirmations=confirmations)
    if bundle:
        if bundle.stat().st_size > 16 * 1024 * 1024:
            raise AethelError("Bundle exceeds 16 MiB")
        payload = json.loads(bundle.read_text())
    elif hub:
        size = chain_action(chain.latest_size)
        url = validate_url(hub) + f"/api/v1/checkpoints/{size}/bundle/{digest}"
        try:
            with httpx.Client(timeout=15) as client, client.stream("GET", url) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes(65536):
                    data.extend(chunk)
                    if len(data) > 16 * 1024 * 1024:
                        raise AethelError("Bundle exceeds 16 MiB")
                payload = json.loads(data)
        except httpx.HTTPError:
            raise AethelError("Could not retrieve the checkpoint bundle from the Hub") from None
    else:
        raise AethelError("Provide --bundle or --hub")
    emit(chain_action(lambda: verify_bundle(payload, digest, chain)))


@app.command("fetch-blob")
@handle_errors
def fetch_blob(
    blob_hash: str,
    gateway: Annotated[list[str], typer.Option("--gateway")],
    output: Annotated[Path, typer.Option("--output")],
    cid: str = typer.Option(..., "--cid"),
):
    """Retrieve an IPFS blob and refuse bytes that differ from the expected hash."""
    if output.exists() and hash_file(output) != normalize_hash(blob_hash):
        raise AethelError("Output already contains different bytes; choose another path")
    emit(retrieve_verified(cid, blob_hash, gateway, output))


@app.command()
@handle_errors
def status(data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None):
    """Read persisted mirror and checkpoint jobs. No credentials are printed."""
    load_env()
    config = settings(data_dir)
    state = StateStore(config.provenance_path)
    emit({"checkpoints": state.records("checkpoints"), "mirrors": state.records("mirrors")})


@app.command()
@handle_errors
def sync(data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None):
    """Run one recoverable mirror and checkpoint cycle, suitable for a timer."""
    errors = []
    for name, action in (("mirror", lambda: mirror(None, data_dir)), ("anchor", lambda: anchor(data_dir))):
        try:
            action()
        except (AethelError, typer.Exit) as exc:
            errors.append({"operation": name, "status": "failed", "error": type(exc).__name__})
    if errors:
        emit(errors)
        raise typer.Exit(code=1)
