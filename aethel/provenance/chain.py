"""EVM checkpoint transactions and RPC-backed historical verification.

Only transaction methods receive signing keys. Read methods validate the configured
chain, exact deployed bytecode, log identity, checkpoint history, and confirmations.
"""

import json
import time
from importlib.resources import files

from aethel.core.atomic import file_lock
from aethel.core.hashing import hash_json, normalize_hash
from aethel.provenance.checkpoint import CheckpointError, checked_prefix
from aethel.provenance.state import StateStore, utc_now


def artifact() -> dict:
    return json.loads(files("aethel.provenance").joinpath("contracts/AethelCheckpoint.json").read_text())


class ChainClient:
    def __init__(self, rpc: str | None, chain_id: int, contract: str | None, log_id: str, *, confirmations: int = 2, timeout: float = 5, web3=None):
        from web3 import HTTPProvider, Web3

        if type(chain_id) is not int or chain_id <= 0 or confirmations < 1:
            raise CheckpointError("Set a positive chain ID and at least one confirmation")
        self.w3 = web3 or Web3(HTTPProvider(rpc, request_kwargs={"timeout": timeout}, exception_retry_configuration=None))
        self.chain_id, self.log_id = chain_id, normalize_hash(log_id.removeprefix("0x"), label="log id")
        self.confirmations = confirmations
        self.address = Web3.to_checksum_address(contract) if contract else None
        self.artifact = artifact()

    def contract(self):
        if not self.address:
            raise CheckpointError("No checkpoint contract configured")
        return self.w3.eth.contract(address=self.address, abi=self.artifact["abi"])

    def validate(self):
        if self.w3.eth.chain_id != self.chain_id:
            raise CheckpointError("RPC chain ID differs from the trusted configuration")
        contract = self.contract()
        if bytes(self.w3.eth.get_code(self.address)).hex() != self.artifact["deployedBytecode"][2:]:
            raise CheckpointError("Contract bytecode does not match the supported checkpoint contract")
        if bytes(contract.functions.logId().call()).hex() != self.log_id:
            raise CheckpointError("Contract log ID differs from the trusted configuration")
        return contract

    def latest_size(self) -> int:
        return self.validate().functions.latestSize().call()

    def verify(self, size: int, root: str, *, leaves: list[str] | None = None) -> dict:
        deadline = time.monotonic() + 30
        root = normalize_hash(root)
        contract = self.validate()
        tip = self.w3.eth.get_block("latest")
        record = contract.functions.checkpoints(size).call(block_identifier=tip.number)
        onchain_root, block, previous = record
        if block == 0 or bytes(onchain_root).hex() != root:
            raise CheckpointError("Checkpoint root is absent or different on the configured chain")
        confirmations = tip.number - block + 1
        if confirmations < self.confirmations:
            raise CheckpointError(f"Checkpoint is pending: {confirmations}/{self.confirmations} confirmations")
        block_hash = self.w3.eth.get_block(block).hash.to_0x_hex()
        history = 1
        if leaves is not None:
            if checked_prefix(leaves, size) != root:
                raise CheckpointError("Local prefix differs from the on-chain checkpoint")
            cursor = previous
            while cursor:
                if time.monotonic() > deadline:
                    raise CheckpointError("Checkpoint history verification exceeded its time budget")
                if history >= 1024 or cursor >= size:
                    raise CheckpointError("Invalid or excessive checkpoint history")
                # Confirmations do not prevent reorgs. Read each earlier checkpoint
                # again against the same tip used for this verification.
                old_root, old_block, predecessor = contract.functions.checkpoints(cursor).call(block_identifier=tip.number)
                if not old_block or bytes(old_root).hex() != checked_prefix(leaves, cursor):
                    raise CheckpointError("An earlier checkpoint disagrees with this log prefix")
                if predecessor >= cursor:
                    raise CheckpointError("Invalid checkpoint predecessor")
                cursor = predecessor
                history += 1
        # A reorg during verification must not produce a confirmed verdict.
        if self.w3.eth.get_block(block).hash.to_0x_hex() != block_hash:
            raise CheckpointError("Checkpoint block changed during verification; retry")
        if self.w3.eth.get_block(tip.number).hash != tip.hash:
            raise CheckpointError("Chain tip changed during verification; retry")
        return {
            "status": "confirmed", "size": size, "root": root,
            "chain_id": self.chain_id, "contract": self.address, "log_id": self.log_id,
            "block": block, "block_hash": block_hash, "confirmations": confirmations,
            "required_confirmations": self.confirmations, "previous_size": previous,
            "history_checked": history if leaves is not None else 0, "checked_at": utc_now(),
        }

    def transact(self, function, key: str, store: StateStore, job_key: str, *, max_fee_gwei: float = 50) -> dict:
        """Persist signed bytes before broadcasting; retries reuse the same transaction."""
        from web3.exceptions import TransactionNotFound

        if self.w3.eth.chain_id != self.chain_id:
            raise CheckpointError("RPC chain ID differs from the trusted configuration")
        account = self.w3.eth.account.from_key(key)
        with file_lock(store.path.parent / "chain-worker.lock"):
            job = store.get("transactions", job_key)
            if job is None:
                cap = self.w3.to_wei(max_fee_gwei, "gwei")
                gas_price = self.w3.eth.gas_price
                if gas_price > cap:
                    raise CheckpointError("Network gas price exceeds AETHEL_MAX_FEE_GWEI")
                tx = function.build_transaction({
                    "from": account.address, "chainId": self.chain_id,
                    "nonce": self.w3.eth.get_transaction_count(account.address, "pending"),
                    "gasPrice": gas_price, "value": 0,
                })
                tx["gas"] = min(int(tx["gas"] * 1.2), 2_000_000)
                if self.w3.eth.get_balance(account.address) < tx["gas"] * gas_price:
                    raise CheckpointError("Signing account needs testnet funds for transaction gas")
                signed = account.sign_transaction(tx)
                job = {
                    "status": "signed", "tx_hash": signed.hash.to_0x_hex(),
                    "raw_transaction": signed.raw_transaction.to_0x_hex(),
                    "chain_id": self.chain_id, "sender": account.address, "nonce": tx["nonce"],
                }
                store.put("transactions", job_key, job)
            try:
                receipt = self.w3.eth.get_transaction_receipt(job["tx_hash"])
            except TransactionNotFound:
                try:
                    self.w3.eth.send_raw_transaction(bytes.fromhex(job["raw_transaction"][2:]))
                except Exception:
                    # A timeout can mean accepted. Retain the signed transaction for retry.
                    return store.put("transactions", job_key, {**job, "status": "pending", "error": "Broadcast unconfirmed; retry the same job"})
                try:
                    receipt = self.w3.eth.get_transaction_receipt(job["tx_hash"])
                except TransactionNotFound:
                    return store.put("transactions", job_key, {**job, "status": "pending"})
            if receipt.status != 1:
                store.put("transactions", job_key, {**job, "status": "failed"})
                raise CheckpointError("Checkpoint transaction reverted; inspect the saved transaction before retrying")
            count = self.w3.eth.block_number - receipt.blockNumber + 1
            canonical = self.w3.eth.get_block(receipt.blockNumber).hash == receipt.blockHash
            return store.put("transactions", job_key, {
                **job, "status": "confirmed" if canonical and count >= self.confirmations else "pending",
                "block": receipt.blockNumber, "block_hash": receipt.blockHash.to_0x_hex(),
                "confirmations": count, "contract": receipt.contractAddress, "error": None,
            })

    def deploy(self, key: str, store: StateStore, *, max_fee_gwei=50) -> dict:
        account = self.w3.eth.account.from_key(key)
        factory = self.w3.eth.contract(abi=self.artifact["abi"], bytecode=self.artifact["bytecode"])
        job_key = hash_json({"kind": "deploy", "chain": self.chain_id, "log": self.log_id, "sender": account.address})
        job = self.transact(factory.constructor(bytes.fromhex(self.log_id)), key, store, job_key, max_fee_gwei=max_fee_gwei)
        return {key: value for key, value in job.items() if key != "raw_transaction"}

    def publish(self, log, anchors, store: StateStore, key: str, *, max_fee_gwei=50) -> dict:
        # One locked snapshot binds size and root even while other pushes arrive.
        leaves = log.snapshot()
        size = len(leaves)
        root = checked_prefix(leaves, size)
        contract = self.validate()
        job_key = hash_json({"kind": "publish", "chain": self.chain_id, "contract": self.address, "size": size, "root": root})
        latest = contract.functions.latestSize().call()
        if latest > size:
            raise CheckpointError("Local log is shorter than the latest on-chain checkpoint")
        if latest:
            old_root = bytes(contract.functions.checkpoints(latest).call()[0]).hex()
            try:
                self.verify(latest, old_root, leaves=leaves)
            except CheckpointError as exc:
                if "pending:" not in str(exc):
                    raise
                record = store.get("checkpoints", str(latest)) or {}
                return store.put("checkpoints", str(latest), {
                    **record, "status": "pending", "size": latest, "root": old_root,
                    "chain_id": self.chain_id, "contract": self.address, "log_id": self.log_id,
                    "error": str(exc),
                })
        if latest != size:
            account = self.w3.eth.account.from_key(key)
            if contract.functions.owner().call() != account.address:
                raise CheckpointError("Signing key is not the checkpoint contract owner")
            job = self.transact(contract.functions.publish(size, bytes.fromhex(root), latest), key, store, job_key, max_fee_gwei=max_fee_gwei)
            if job["status"] != "confirmed":
                record = {key: value for key, value in job.items() if key != "raw_transaction"}
                record.update(size=size, root=root, contract=self.address, log_id=self.log_id)
                return store.put("checkpoints", str(size), record)
        else:
            job = store.get("transactions", job_key) or store.get("checkpoints", str(size)) or {}
        record = self.verify(size, root, leaves=leaves)
        if job.get("tx_hash"):
            record["tx_hash"] = job["tx_hash"]
        anchors.append(record)
        return store.put("checkpoints", str(size), record)
