# Hub and provenance operations

See the [user guide](guide.md) for client commands and [Evidence](evidence/README.md) for saved public receipts.

The Hub accepts versioned adapters. A separate worker mirrors adapter blobs to IPFS and
publishes a Merkle checkpoint on an EVM chain. The dashboard reads worker records and runs
bounded background verification. The independent CLI verifies a downloaded bundle using
RPC, chain, contract, and log identity supplied by the verifier.

## Run a Hub

```bash
python -m pip install -e '.[hub]'
python -m hub
```

The default listener is `0.0.0.0:8000` and the store is `./hub-data`, relative to the process
working directory. Browse `http://127.0.0.1:8000`. For predictable restarts, set an absolute
`AETHEL_HUB_DATA` path. `.aethel/` is a client repository; it is separate from the Hub store.
The server loads allowed settings from `.env`; use [.env.example](../.env.example) as a reference.

| Setting | Default / role |
|---|---|
| `AETHEL_HUB_DATA` | `./hub-data`; objects, refs, log, anchor and worker state |
| `AETHEL_HUB_HOST`, `AETHEL_HUB_PORT` | `0.0.0.0`, `8000` |
| `AETHEL_HUB_MAX_BLOB` | 67,108,864 bytes; applies to blob and structured-object uploads |
| `AETHEL_HUB_TOKEN` | Optional shared bearer token for write routes |
| `AETHEL_HUB_RELOAD` | Optional development reload |
| `AETHEL_HUB_URL` | Client URL override, not the server's bind address |

Reads are public. A configured token gates writes; an unset token leaves publishing open,
which the health page reports explicitly. This is a single-team Hub, not an account platform.
The web process does not need signing or pinning credentials.

The main pages are `/` (repositories), `/r/<name>`, `/c/<full-hash>`, `/provenance`, and `/ops`.
`/api` is the maintained endpoint reference; `/openapi.json` is the generated schema. Publishing
uses object uploads, negotiation, and ref updates under `/api/v1`. Clone/pull require
`GET /api/v1/objects/{kind}/{object_hash}`, which preserves exact stored bytes. JSON inspection
routes reserialize their responses and are not substitutes for verified downloads.

## Run the prepared demonstration

```bash
.venv/bin/python scripts/serve_review.py
```

Open `http://127.0.0.1:8787`. This launcher requires an already prepared local store at `.demo/public-review/hub-data`; it is not created by a fresh Git clone.
Its `hub.env` contains read-only provider settings and a Hub write token. The server launcher
removes inherited signing and Pinata credentials and does not load the worker's `.env`.

The demonstration has two real DistilBERT adapters trained on 600 public SST-2 examples.
Each uses 360 training examples and the same 120-example validation split; 120 test examples
remain unused. These small experiments exercise the workflow. They do not establish a general
sentiment benchmark. The second adapter regressed; its recorded results remain visible.

To prepare a separate demonstration, supply a new `--dir` to `scripts/prepare_review_models.py`.
The script claims its `work` directory before loading ML dependencies or downloading data and
refuses any existing work path, including an empty directory, file, or symlink. A failed run
can leave its claimed directory for inspection; use a new destination for another attempt.

With `[hub,ml,provenance]` installed, a new local experiment can be prepared and served with:

```bash
python scripts/prepare_review_models.py --dir .demo/experiment
python scripts/publish_review_provenance.py --dir .demo/experiment --prepare-only
python scripts/serve_review.py --dir .demo/experiment
```

Preparation downloads the model/data and performs training. `--prepare-only` writes Hub
settings without publishing transactions or IPFS pins. This new instance has no confirmed
checkpoint until its operator explicitly runs the separate publication workflow. The public
receipts in [Evidence](evidence/README.md) describe the original recorded instance.

1. Open the repository and compare both measured versions.
2. Open a commit. Download its checkpoint bundle and inspect its IPFS record.
3. On Provenance, open the Sepolia transaction and the dedicated IPFS copy.
4. Run the independent verification command displayed on the page with a trusted RPC.
5. Use `aethel provenance fetch-blob` with the recorded SHA-256, CID, and gateway to verify
   the external bytes before installing them locally.
6. Show the failure tests for altered history and corrupt gateway downloads. Do not alter
   the live demonstration store to stage an attack.

## Independent verification

The API's bundle is input to verification, not an authority for chain selection. Obtain the
expected chain, contract, and log identity from an independent release record. A compromised
Hub can serve a self-consistent false bundle with a different contract; the caller's trusted
configuration is what rejects that substitution.

`aethel provenance verify <commit>` requires `--rpc`, `--chain-id`, `--contract`, `--log-id`,
and either `--bundle <file>` or `--hub <url>`. It checks the actual contract and all earlier
checkpoint prefixes. Use the concrete command under [Evidence](evidence/README.md#blockchain)
to check the saved public receipt. A stored bundle supports offline Merkle inspection, but a
fresh chain verdict still needs RPC access.

`aethel provenance fetch-blob <sha256> --cid <cid> --gateway <url> --output <new-file>` verifies
an external adapter copy. Repeat `--gateway` for fallbacks. An existing different output is
refused. This downloads one blob; it does not replace `aethel clone` for repository retrieval.

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

## Configuration ownership and precedence

A process environment value takes precedence over values loaded from `.env`, except the
prepared demonstration launcher deliberately loads its chosen `hub.env` with override enabled.
The environment parser does not interpolate `$` expressions. The normal server loader admits
only its allowlisted settings. Client remote selection uses explicit flags, then client
environment values, then saved repository settings, then the localhost default.

| Setting | Owner / behavior |
|---|---|
| `AETHEL_AUTHOR` | Local author default; not an authenticated identity assertion |
| `AETHEL_CHAIN_RPC`, `AETHEL_CHAIN_ID` | Hub probes and worker: expected RPC and chain |
| `AETHEL_ANCHOR_CONTRACT`, `AETHEL_LOG_ID` | Trusted deployment identity; address omitted only for deployment |
| `AETHEL_CHAIN_CONFIRMATIONS` | Required chain depth, default 2, minimum 1 |
| `AETHEL_PROBE_INTERVAL` | Background check interval, default 60 seconds, minimum 10 |
| `AETHEL_CHAIN_EXPLORER` | Public receipt-link base; not verification authority |
| `AETHEL_ANCHOR_KEY` | Signing worker only |
| `AETHEL_MAX_FEE_GWEI` | Worker fee cap; example configuration uses 10 |
| `AETHEL_PINNING_ENDPOINT`, `AETHEL_PINNING_BACKEND` | Pinning API and `pinata`/`kubo` protocol selection |
| `AETHEL_PINATA_JWT` | Pinning worker credential |
| `AETHEL_IPFS_GATEWAY`, `AETHEL_IPFS_FALLBACK_GATEWAY` | Retrieval locations for independent byte checks |
| `AETHEL_ETHERSCAN_KEY` | Optional tooling credential; not required by runtime bytecode verification |
| `AETHEL_PINATA_KEY`, `AETHEL_PINATA_SECRET` | Legacy settings not used by the JWT-based worker |

The signing key and pinning token are never persisted by clone or served as dashboard settings.
`--data-dir` on supported worker commands selects a Hub store explicitly. The `provenance`
subcommands are `deploy`, `mirror`, `anchor`, `sync`, `status`, `verify`, and `fetch-blob`.
`sync` runs one mirror/anchor cycle; the supplied timer schedules repeated cycles.

## Deployment files

The [deploy](../deploy/) directory provides separate Hub and worker systemd units, a 15-minute worker
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
the documented testnet deployment; they are not a claim of irreversible finality. A changed or missing
checkpoint loses its confirmed verdict on the next RPC check. Cached good results expire.
Each chain verification rereads earlier checkpoints at its sampled block number and checks that
the sampled tip remains canonical before reporting success. Historical confirmation records are
not reused across probes, because a reorganization can replace previously confirmed blocks.

## Retry and recovery behavior

| Failure | Behavior and recovery |
|---|---|
| Workspace replacement interrupted | Inspect `.aethel/workspace.backup` and preserve its contents before retrying. A live writer must finish before clearing its lock. |
| Download interrupted | Retry clone in a new destination or retry pull; verified cached pull objects are reused. |
| Partial trailing log record | Stop writers, back up the store, inspect the incomplete JSONL tail, and recover against verified history before restarting. |
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

## Operational scope

Use the [development verification commands](development.md#verification) for tests, contract
rebuilds, packaging, and browser checks. Live Sepolia/IPFS checks have separate network and
service requirements; offline tests do not establish current provider availability.

These service templates support an operator-managed installation. The documented deployment
is a single-team system on a public testnet, without a production service-level guarantee or
multi-user account administration. Operators choose their hosting environment, retain coherent
backups, monitor provider availability, and validate the trust configuration.
