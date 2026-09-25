# Public review evidence

Public receipts from completed public-testnet and IPFS operations, using two actual
trained adapters. The live chain, gateway, and service checks were repeated later on the
same build and agreed with the table below.
They are separate from the generated examples in unit tests.

## Blockchain

| Field | Recorded value |
|---|---|
| Network | Sepolia, chain ID 11155111 |
| Contract | `0xFe000a436a8Af301e96aFe3815Ab0ba0003C078E` |
| Log ID | `053b1222dab7d5c519cb146bd2661bd2287e17a0e9309a1e336ce9646758ede2` |
| Checkpoint size | 2 accepted commits |
| Root | `aeb33d38a626e681507a75cd94a78d8efd0a0fdf7dc95ac300196d3957cb4b28` |
| Block | 11764982 |
| Required confirmations | 2 |
| Independent check | Confirmed through Tenderly at 47 confirmations |

- [Deployed contract](https://sepolia.etherscan.io/address/0xFe000a436a8Af301e96aFe3815Ab0ba0003C078E)
- [Checkpoint transaction](https://sepolia.etherscan.io/tx/0xe795908c05e4178150f55ed9157768d7cded709bbe5a5a778cf3ca7121ceaf9b)
- [Deployment transaction](https://sepolia.etherscan.io/tx/0xb827fad329497e22666b2152e88a1e402aa9704fcb47dcfb38feac738e4cc662)
- [Independent verification bundle](evidence/checkpoint-2.json)
- [Machine-readable result](evidence/provenance-review.json)

The Hub uses PublicNode. The independent verification command used Tenderly, checked the exact
compiled contract code and log identity, and reproduced the checkpoint from the exported leaves.
Confirmation counts above describe the saved check; current chain state must be checked again.

```bash
.venv/bin/aethel provenance verify \
  db5be1090f64878a49b8dbb670bbcce69bf65c2ede90a09f324ec74c69538aec \
  --bundle docs/evidence/checkpoint-2.json \
  --rpc https://sepolia.gateway.tenderly.co \
  --chain-id 11155111 \
  --contract 0xFe000a436a8Af301e96aFe3815Ab0ba0003C078E \
  --log-id 053b1222dab7d5c519cb146bd2661bd2287e17a0e9309a1e336ce9646758ede2
```

## IPFS

Both adapters were pinned through Pinata and then downloaded from the account's dedicated
gateway. Each retrieval reproduced the expected SHA-256. Shared public gateways returned HTTP
429; switching to the existing dedicated gateway recovered retrieval without new pin requests.

| Training seed | Adapter SHA-256 | IPFS CID |
|---|---|---|
| 42 | `93970363ae2094b3c420779b375b9cf2490d39ad8b210e41800a4ded90ee23bc` | `bafybeihsiewfwvy3u6e67bt7t6zs4fylikxlxca3347ovjq5osudkvjnta` |
| 43 | `e8e13125e1fbedfaae1da45bd41c3680f65fa76d1e1e1d4cf6dcb4c219a2e525` | `bafybeihawg2juud5gjin5apk6upvrlfgfjvimlltgdwkp2kuflbo75ylvm` |

Each adapter is 2,667,272 bytes. Retrieve and verify the first:

```bash
.venv/bin/aethel provenance fetch-blob \
  93970363ae2094b3c420779b375b9cf2490d39ad8b210e41800a4ded90ee23bc \
  --cid bafybeihsiewfwvy3u6e67bt7t6zs4fylikxlxca3347ovjq5osudkvjnta \
  --gateway https://amethyst-blank-crocodile-745.mypinata.cloud/ipfs \
  --gateway https://ipfs.io/ipfs \
  --output /tmp/review-adapter.safetensors
```

The external bytes are actual safetensors adapters, not fixture files. A mirror does not include
the base weights or enough repository metadata to reconstruct the Hub without its backups.

## Measured pilot

Base: `distilbert/distilbert-base-uncased`, revision
`12040accade4e8a0f71eabdb258fecc2e7e948be`. Data: 600 public rows from `stanfordnlp/sst2`.
The recorded split contains 360 training, 120 validation, and 120 test examples. Both runs use
the same split seed of 42, one epoch, LoRA rank 4, and alpha 8. The test split was not scored.

| Training seed | Validation accuracy | Macro-F1 | Loss | Commit |
|---|---|---|---|---|
| 42 | 59.17% | 0.5017 | 0.671746 | `db5be1090f64878a49b8dbb670bbcce69bf65c2ede90a09f324ec74c69538aec` |
| 43 | 50.83% | 0.3370 | 0.672748 | `52f2b822ab6e97ec3d08eba93ef4055cec5521683149d5e1e3625a8382bbc66d` |

The second run regressed. These are small workflow pilots with newly initialized classification
heads; broader tuning, baselines, repetitions, and final test-set evaluation remain required for
the final report. Training records, split identities, and metrics are retained with the commits.

## Dashboard and checks

Start with `.venv/bin/python scripts/serve_review.py` and open `http://127.0.0.1:8787`.
The dashboard provides checkpoint receipts, downloadable bundles, CID records, live chain and
gateway states, repository filtering, expandable training metadata, and browser proof checking.

The final local verification results are:

| Check | Result |
|---|---|
| Full development venv: `.venv/bin/python -m pytest -q -o addopts=--strict-markers` | 836 passed, no skips, 1 warning |
| CI `core` job simulated, `[dev]` only | 347 passed, 158 skipped; 95.58% core coverage |
| CI `hub` job simulated, `[dev,hub]` | 790 passed, 13 skipped |
| CI `provenance` job simulated, `[dev,hub,provenance-test,fonts]` | 814 passed, 5 skipped |
| Base install, system `python3` | 797 passed, 6 skipped |
| Ruff | Passed |
| Python/JavaScript proof parity | 306/306 agreed |
| Chrome dashboard checks | Passed: navigation, filtering, refresh, light/dark themes, mobile overflow, browser proof recomputation, console |
| Wheel contents | Compiled contract, Hub templates, JavaScript, CSS, and fonts present |
| Patch whitespace and documentation checks | Passed |

These are local checks; a hosted CI run is still pending. The single pytest warning is a
Starlette TestClient deprecation concerning httpx. Skip counts are not missing tests: a
module-level skip records the entire module as one skip item, so the skipped and passed counts do
not sum to the collected total.

The live API check returned a confirmed two-entry checkpoint with more than eleven thousand
confirmations, a SHA-256-verified gateway sample, two verified mirror records, and all eight
health checks marked good. The confirmation count is the only part of this that moves; the block
number, the root, and the digests stay fixed, and the dashboard refreshes the live state.

The final display correction distinguishes pending, unavailable, stale, failed, and unconfigured
chain checks from a newer root awaiting anchoring. Regression checks cover a confirmed checkpoint
losing fresh RPC evidence and an older confirmed checkpoint remaining valid after log growth.

Chrome checks ran with the dashboard's Content Security Policy enabled. Screenshots are in
`.demo/public-review/screenshots`. Contract tests execute the compiled
Solidity on PyEVM and cover ownership, stale updates, retries, confirmations, reorgs, and altered
prefixes. IPFS tests reject corrupt/oversized responses and verify retry state preservation.

This is a working public-testnet demonstration. Persistent remote HTTPS hosting, an independent
contract security review, and deployment backup/restore drills remain operational release work.
The project still needs the planned merge, clone/pull, and full reproduction workflows.
