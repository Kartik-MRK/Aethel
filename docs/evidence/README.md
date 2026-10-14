# Evidence and validation record

The public receipts below record completed Sepolia and IPFS operations using two real
trained adapters. They are historical checks, separate from synthetic test fixtures. The
confirmation depth, gateway availability, and canonical chain must be checked again for a
live demonstration. The original JSON receipt and bundle remain unchanged in this directory.

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
- [Independent verification bundle](checkpoint-2.json)
- [Machine-readable result](provenance-review.json)

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
  --output /tmp/aethel-adapter.safetensors
```

The external bytes are actual safetensors adapters, not fixture files. A mirror does not include
the base weights or enough repository metadata to reconstruct the Hub without its backups.

## Measured experiment

Base: `distilbert/distilbert-base-uncased`, revision
`12040accade4e8a0f71eabdb258fecc2e7e948be`. Data: 600 public rows from `stanfordnlp/sst2`.
The recorded split contains 360 training, 120 validation, and 120 test examples. Both runs use
the same split seed of 42, one epoch, LoRA rank 4, and alpha 8. The test split was not scored.

| Training seed | Validation accuracy | Macro-F1 | Loss | Commit |
|---|---|---|---|---|
| 42 | 59.17% | 0.5017 | 0.671746 | `db5be1090f64878a49b8dbb670bbcce69bf65c2ede90a09f324ec74c69538aec` |
| 43 | 50.83% | 0.3370 | 0.672748 | `52f2b822ab6e97ec3d08eba93ef4055cec5521683149d5e1e3625a8382bbc66d` |

The second run regressed. These are small workflow experiments with newly initialized classification
heads; a broader accuracy claim would need suitable baselines, repetitions, and final test-set
evaluation. The recorded values describe this experiment only. Training records, split identities,
and metrics are retained with the commits.

## Software validation

Full-system validation on 13 October 2026 (`0f694e9`) recorded:

| Check | Result |
|---|---|
| Full installed-dependency suite, Python 3.13 | 954 passed, zero failures, zero skips |
| Combined `aethel` and `hub` coverage | 84.40% |
| `aethel.core` coverage | 96.14%; CI floor is 80% |
| Targeted retrieval/CLI optional-dependency absence simulation | 63 passed, 5 Hub-dependent skips |
| Ruff and patch whitespace | Passed |
| Wheel | Built; clone/pull commands and retrieval modules included |

The optional-dependency check blocked ML, FastAPI, and provenance imports; it was not a
separate clean installation. The installed Starlette TestClient emits one deprecation warning
about httpx. ML integration tests use tiny local BERT/DistilBERT models; contract tests execute
compiled Solidity in a local EVM. IPFS/provider behavior uses controlled test responses.

Additional validation at `2a8a4c9` checked 306/306 Python/JavaScript Merkle parity cases,
unchanged Solidity 0.8.30 compilation output, packaged assets, and Chrome navigation,
filtering, refresh, themes, mobile layout, and browser proof verification with CSP enabled.
These are dated software checks, not a claim of continuous external-service availability.
Use [Development](../development.md#verification) to reproduce them.

## Integrity behavior covered by regression tests

| Area | Verified behavior |
|---|---|
| Input preparation | Refuses an existing work directory, file, or symlink before writing |
| Publication | Validates all ancestors and object dependencies, including already accepted history |
| Configuration and contract values | Correct required variables per operation; consistent bytes/HexBytes log identifiers |
| Dataset identity | Portable internal paths; altered data, split, and metadata are detected |
| Workspace and refs | Complete staged replacement, failed-write rollback, expected HEAD/tip checks |
| Atomic storage | Exclusive temporary names, verified copied bytes, corruption repair from verified originals |
| Input bounds | Streamed uploads/downloads and complete object graphs have size/count limits |
| Evaluation binding | Changed adapter/config/training metadata and mismatched bases are rejected |
| Retrieval | All published history, exact JSON bytes, retry reuse, missing/corrupt object refusals |
| History policy | Fast-forward updates; dirty work, divergence, and concurrent ref changes are preserved |

## Reusing the evidence

The [operations guide](../operations.md#run-the-prepared-demonstration) explains the prepared dashboard.
Local `.demo/public-review` artifacts and screenshots are not shipped in the source repository;
a fresh checkout needs preparation or a restored backup. The JSON files here are public evidence,
not credentials or full repository backups. Base weights and source datasets remain separate.

The measured experiment supports a working versioning, sharing, and provenance demonstration. It does not
establish general model quality, trusted author identity, honest training, or production-scale
service reliability. Merging and multi-user platform features are outside the
[supported scope](../README.md#supported-scope).
