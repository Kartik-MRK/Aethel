# IPFS and blockchain operation

The Hub accepts versioned adapters. A separate worker mirrors adapter blobs to IPFS and
publishes a Merkle checkpoint on an EVM chain. The dashboard reads worker records and runs
bounded background verification. The independent CLI verifies a downloaded bundle using
RPC, chain, contract, and log identity supplied by the verifier.

## Run the prepared review

```bash
.venv/bin/python scripts/serve_review.py
```

Open `http://127.0.0.1:8787`. The prepared store is `.demo/public-review/hub-data`.
Its `hub.env` contains read-only provider settings and a Hub write token. The server launcher
removes inherited signing and Pinata credentials and does not load the worker's `.env`.

The demonstration has two real DistilBERT adapters trained on 600 public SST-2 examples.
Each uses 360 training examples and the same 120-example validation split; 120 test examples
remain unused. These small pilot runs establish the workflow. They do not establish a final
sentiment benchmark. The second adapter regressed; its recorded results remain visible.

1. Open the repository and compare both measured versions.
2. Open a commit. Download its checkpoint bundle and inspect its IPFS record.
3. On Provenance, open the Sepolia transaction and the dedicated IPFS copy.
4. Run the independent verification command displayed on the page with a trusted RPC.
5. Use `aethel provenance fetch-blob` with the recorded SHA-256, CID, and gateway to verify
   the external bytes before installing them locally.
6. Show the failure tests for altered history and corrupt gateway downloads. Do not alter
   the live demonstration store to stage an attack.

The API's bundle is input to verification, not an authority for chain selection. Obtain the
expected chain, contract, and log identity from an independent release record. A compromised
Hub can serve a self-consistent false bundle with a different contract; the caller's trusted
configuration is what rejects that substitution.

## Install and configure another instance

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[hub,provenance]'
```

Set these in the Hub environment:

```dotenv
AETHEL_HUB_DATA=/var/lib/aethel
AETHEL_HUB_HOST=127.0.0.1
AETHEL_HUB_PORT=8000
AETHEL_HUB_TOKEN=SET_A_RANDOM_HUB_WRITE_TOKEN
AETHEL_CHAIN_RPC=SET_YOUR_RPC_ENDPOINT
AETHEL_CHAIN_ID=11155111
AETHEL_LOG_ID=SET_A_RANDOM_64_CHARACTER_HEX_LOG_ID
AETHEL_ANCHOR_CONTRACT=SET_THE_DEPLOYED_CONTRACT_ADDRESS
AETHEL_CHAIN_CONFIRMATIONS=2
AETHEL_CHAIN_EXPLORER=https://sepolia.etherscan.io
AETHEL_IPFS_GATEWAY=https://YOUR_GATEWAY.mypinata.cloud/ipfs
AETHEL_IPFS_FALLBACK_GATEWAY=https://ipfs.io/ipfs
AETHEL_PROBE_INTERVAL=60
```

The capitalized values above are operator settings, not runnable defaults. Generate the log ID
once and retain it with the contract address. Each independent Hub log needs its own identity
and contract. Changing a deployment's chain identity requires a deliberate migration of its
worker state and verifier configuration.

Only the worker receives:

```dotenv
AETHEL_ANCHOR_KEY=SET_THE_SIGNING_PRIVATE_KEY
AETHEL_MAX_FEE_GWEI=10
AETHEL_PINNING_BACKEND=pinata
AETHEL_PINNING_ENDPOINT=https://api.pinata.cloud
AETHEL_PINATA_JWT=SET_THE_PINNING_TOKEN
```

Deploy once, then copy the returned contract address to the trusted configuration:

```bash
aethel provenance deploy
aethel provenance mirror
aethel provenance anchor
aethel provenance status
```

Deployment requires RPC, chain ID, and log ID but no existing contract address. Pending
transactions remain pending in the output. Repeat the same command to reconcile the receipt
after enough blocks have passed. `sync` runs one mirror and anchor cycle; either operation can
fail without preventing the other from being attempted.

For a local IPFS node, use `AETHEL_PINNING_BACKEND=kubo`, a loopback API such as
`http://127.0.0.1:5001`, and a loopback gateway such as `http://127.0.0.1:8080/ipfs`.
The Kubo administration API must remain private. Remote provider and gateway URLs require HTTPS.

## Deployment files

The `deploy/` directory provides separate Hub and worker systemd units, a 15-minute worker
timer, and a Caddy reverse-proxy configuration. They are templates for a Linux host; they have
not been enabled on a remote machine in this workspace.

- Install the checkout and virtual environment at `/opt/aethel`.
- Create service users `aethel` and `aethel-worker`, sharing the `aethel` group.
- Create `/var/lib/aethel` with shared group write access and the setgid bit. Existing mutable
  state files must also be group-writable; the Hub uses `UMask=0007` for that shared state.
- Store `/etc/aethel/hub.env` and `/etc/aethel/worker.env` as root-owned mode-0600 files.
  systemd reads these files before changing user. Keep the worker file out of the Hub's user
  permissions, checkout, image, and environment.
- Configure DNS and `AETHEL_PUBLIC_HOST` for Caddy. The Hub stays on loopback behind HTTPS.
- Install the units, start the Hub, then enable the worker timer after a manual cycle passes.
- Alert on sustained `/api/v1/health` warnings or failures and worker service failures. A gateway
  failure must not be hidden by a healthy local object store.

Use a confirmation threshold appropriate to the deployment. Two confirmations are used for
the review testnet; they are not a claim of irreversible finality. A changed or missing
checkpoint loses its confirmed verdict on the next RPC check. Cached good results expire.

## Retry and recovery behavior

| Failure | Behavior and recovery |
|---|---|
| Broadcast timeout | Signed transaction bytes and hash were saved first. Retry rebroadcasts those bytes rather than creating a new payment. |
| Transaction mined, receipt not saved | Retry finds the on-chain checkpoint and rebuilds the local receipt. |
| Insufficient confirmations | Remains pending until the configured threshold is met. |
| Wrong RPC chain, contract code, or log ID | Verification fails; no confirmed label is produced. |
| Older prefix changed or truncated | Prefix reconstruction fails against the checkpoint. |
| New commits after checkpoint | Older prefix stays valid; the dashboard reports the additional entries. |
| Pin succeeds, gateway fails | CID persists. Retry retrieves the same CID without another pin request. |
| Gateway returns HTTP 429 | Configure the account's dedicated gateway or allow the next scheduled retry. Shared gateways can rate-limit valid pins. |
| Wrong bytes or oversized response | Download is rejected. Existing destination files are preserved. |
| RPC or gateway unavailable | Dashboard reports unavailable; page rendering continues. |

The worker currently uses the repository's explicit file-lock policy. A process killed while
holding a lock can leave its lock file behind. Stop the worker, confirm the recorded process
is no longer running, then remove only that stale lock before retrying. Never delete a live
worker's lock, nonce state, or signed transaction record to force a retry.

Use one signing worker per wallet. Other applications spending from the same account can
consume a pending nonce. A reverted transaction or externally replaced nonce needs operator
inspection; the worker does not silently choose another transaction or raise its gas price.

Stop both services before copying the entire data directory for backup, or use SQLite's backup
API together with a coherent object/ref/log snapshot. Include `provenance.sqlite3`, its live WAL
when copying a running database, `anchors.jsonl`, `log.jsonl`, `repos.json`, and all objects.
Keep a separate protected backup of the worker credentials and trusted contract configuration.
Test restoration by re-hashing objects and re-verifying the anchored prefix through RPC.

## Scope and limits

- The checkpoint contract is non-upgradeable. Only its owner can append a larger checkpoint.
  Ownership changes require nomination and acceptance. The owner can append an inconsistent
  new root, but a verifier checking all earlier prefixes detects the inconsistency.
- The contract does not accept funds. The signing account pays network gas. Keep production
  assets out of the demonstration wallet. Independent security review remains required before
  using this contract as a production trust boundary.
- IPFS mirrors adapter blobs only. Repository refs, commit metadata, evaluation records, and base
  weights still require Hub backups and their recorded sources. A CID alone cannot restore the Hub.
- Proof bundles support up to 100,000 leaves and verification supports up to 1,024 checkpoints.
  This is a bounded single-team deployment, not a high-volume public transparency service.
- Read results depend on the selected RPC. An RPC that lies about chain state is outside the
  single-provider trust model; use a separately operated provider or your own node for verification.
- Pinning stores a copy; availability and retention depend on the pinning service and account.
  The Hub download stays available when IPFS fails.

## Checks

```bash
.venv/bin/python -m pip install -e '.[dev,hub,ml,provenance-test,fonts]'
npm ci --prefix contracts --ignore-scripts
npm run --prefix contracts build
.venv/bin/python -m pytest -ra
.venv/bin/ruff check .
python3 scripts/check_verify_js.py
```

The EVM tests deploy the compiled Solidity contract into PyEVM and exercise ownership,
append rules, stale predecessors, receipt recovery, confirmations, reorgs, and prefix integrity.
Gateway tests cover corrupt content, size limits, failure persistence, and Pinata/Kubo protocols.
The public review separately exercises Sepolia and Pinata with actual trained adapter bytes.

For the optional browser check, install Playwright in the same virtual environment and have
Google Chrome installed through the operating system's package manager. The script selects
Playwright's `chrome` channel. Start the prepared review server first, then run:

```bash
.venv/bin/python -m pip install playwright
.venv/bin/python scripts/check_dashboard.py
```

The check writes light, dark, and mobile screenshots to `.demo/public-review/screenshots` and
checks filtering, refresh, navigation, browser proof recomputation, overflow, and console errors.
Playwright is a review tool; it is not required to serve the Hub or use the core CLI.
