# Aethel Review Demo: Every Command, Every Edge Case

> All commands assume you're in the repo root: `~/Capstone/Aethel`
> Your hashes **will differ** from examples below; that's the point of content-addressing.

---

## 0. Setup (Run Once Before the Review)

### Path A: Quick Seeded Demo (No GPU, < 1 second)
```bash
cd /home/sathwik/zzz/Capstone/Aethel
source .venv/bin/activate

# Seed 8 commits across 3 branches with synthetic weights
python scripts/seed_demo.py --force

# Start the Hub on the seeded store
AETHEL_HUB_DATA="$PWD/.demo/hub-data" python -m hub
```

### Path B: Full Training Demo (Needs `[ml]` extra + datasets)
```bash
cd /home/sathwik/zzz/Capstone/Aethel
source .venv/bin/activate
pip install -e ".[dev,ml,hub]"
python setup_demo_data.py
```

---

## 1. `aethel init`: Initialize a Repository

### Basic init
```bash
mkdir -p demo && cd demo
aethel init --model distilbert-base-uncased
```

### Init with explicit author
```bash
aethel init --model distilbert-base-uncased --author johndoe
```

### Re-init (idempotent: safe to run twice)
```bash
aethel init --model distilbert-base-uncased
# Prints "Reinitialized" instead of "Initialized"
```

### Edge Case: Bad model name
```bash
aethel init --model nonexistent/fake-model-xyz
# → "Could not fetch model info for 'nonexistent/fake-model-xyz'"
```

### Edge Case: Empty model
```bash
aethel init --model ""
# → Prompts for input interactively
```

---

## 2. `aethel train`: LoRA Fine-Tuning

### Train with real data
```bash
aethel train --config ../train_yaml/sst2_config.yaml
```

### Train with different datasets
```bash
aethel train --config ../train_yaml/emotion_config.yaml
aethel train --config ../train_yaml/rotten_tomatoes_config.yaml
aethel train --config ../train_yaml/ag_news_config.yaml
aethel train --config ../train_yaml/tweet_eval_hate_config.yaml
```

### Edge Case: Dataset path doesn't exist (REFUSES to train)
```bash
aethel train --config ../train_yaml/sst2_config.yaml
# If datasets/sst2 doesn't exist:
# → "Dataset path './datasets/sst2' does not exist."
# → "To train on synthetic data deliberately, pass --allow-stub"
```

### Edge Case: Explicit stub training (synthetic data)
```bash
aethel train --config ../train_yaml/sst2_config.yaml --allow-stub
# → "--allow-stub: './datasets/sst2' not found, training on SYNTHETIC data. This model learns nothing real."
```

### Edge Case: Config file doesn't exist
```bash
aethel train --config nonexistent.yaml
# → "Training config file not found: nonexistent.yaml"
```

### Edge Case: Not in a repo
```bash
cd /tmp && aethel train --config anything.yaml
# → "Not an Aethel repository. Run 'aethel init' first."
```

### Edge Case: ML extra not installed
```bash
# On a base install without torch:
aethel train --config sst2_config.yaml
# → "Training needs the ML extra. Install it with: pip install 'aethel[ml]'"
```

---

## 3. `aethel commit`: Snapshot Workspace

### Basic commit
```bash
aethel commit -m "SST-2 sentiment baseline"
```

### Edge Case: Empty workspace (nothing trained yet)
```bash
aethel commit -m "test"
# → "ObjectNotFound: No adapter weights in .aethel/workspace. 
#    Expected one of: adapter_model.safetensors, adapter_model.bin.
#    Run 'aethel train --config <yaml>' first."
```

### Edge Case: Commit while detached HEAD
```bash
aethel checkout <some-commit-hash>   # detach HEAD first
aethel commit -m "test"
# → "Detached HEAD, commit blocked."
# → "Recover with: aethel branch <name>"
# → "              aethel checkout <name>"
# → "              aethel commit -m '<message>'"
```

### Edge Case: No repo
```bash
cd /tmp && aethel commit -m "test"
# → "Not an Aethel repository (or any parent directory)."
```

---

## 4. `aethel branch`: List / Create / Delete

### List all branches
```bash
aethel branch
# * main     95c419882714 SST-2 sentiment baseline
#   emotion  a1b2c3d4e5f6 Emotion 6-class
```

### Create a new branch
```bash
aethel branch emotion
# → "Created branch emotion at 95c419882714"
# → "Switch to it with: aethel checkout emotion"
```

### Create branch from a specific start point
```bash
aethel branch experiment <commit-hash-or-branch>
```

### Delete a branch
```bash
aethel branch -d emotion
# → "Deleted branch emotion"
# → "Its commits are still in the object store at a1b2c3d4e5f6 and nothing was destroyed."
```

### Edge Case: Create branch that already exists
```bash
aethel branch main
# → "BranchExists: A branch named 'main' already exists."
```

### Edge Case: Delete the current branch
```bash
aethel branch -d main
# → "Cannot delete 'main'; it is the current branch."
# → "Check out a different branch first."
```

### Edge Case: Delete non-existent branch
```bash
aethel branch -d nonexistent
# → "Branch 'nonexistent' does not exist."
```

### Edge Case: Create branch before any commits
```bash
# In a fresh repo with no commits:
aethel branch test
# → "No commits yet, nothing for a branch to point at. Make your first commit, then create a branch."
```

---

## 5. `aethel checkout`: Restore Workspace

### Switch to a branch
```bash
aethel checkout emotion
# → "Switched to branch emotion"
# → "Commit: a1b2c3d4e5f6 Emotion 6-class"
# → "Restored 3 file(s) into .aethel/workspace"
```

### Switch back
```bash
aethel checkout main
```

### Detach HEAD at a specific commit
```bash
aethel checkout <full-64-char-commit-hash>
# → "HEAD is now detached at 95c419882714"
# → "Commits are blocked while detached. Create a branch here with: aethel branch <name>"
```

### Edge Case: Checkout with uncommitted changes (BLOCKED)
```bash
# Modify a file in .aethel/workspace, then:
aethel checkout emotion
# → "Workspace has uncommitted changes:"
# → "  modified: adapter_model.safetensors"
# → "Commit them, or re-run with --force to discard."
```

### Force checkout (discard changes)
```bash
aethel checkout emotion --force
aethel checkout --force emotion    # both arg orders work
```

### Edge Case: Checkout non-existent target
```bash
aethel checkout nope
# → "InvalidRef: 'nope' did not match any branch or commit."
```

---

## 6. `aethel log`: Show Commit History

### Current branch history
```bash
aethel log
```

### All branches
```bash
aethel log --all
```

### Limit output
```bash
aethel log -n 5
```

### Log from a specific branch or commit
```bash
aethel log emotion
aethel log <commit-hash>
```

### Edge Case: No commits yet
```bash
# In a fresh repo:
aethel log
# → "No commits yet. Run 'aethel train' then 'aethel commit'."
```

---

## 7. `aethel status`: Current State

### Show status
```bash
aethel status
# → "On branch main"
# → "Commit: 95c419882714 SST-2 sentiment baseline"
# → "Workspace clean."
```

### Status with modified workspace
```bash
# After modifying a workspace file:
aethel status
# → "Workspace changes:"
# → "  modified: adapter_model.safetensors"
```

### Status while detached
```bash
aethel checkout <commit-hash>
aethel status
# → "HEAD detached at 95c419882714"
```

---

## 8. `aethel fsck`: Verify Integrity

### Basic integrity check
```bash
aethel fsck
# → "Checked 14 objects (3 commits, 3 trees, 7 blobs, 1 bases)"
# → "Repository integrity OK."
```

### Verbose mode (list every object)
```bash
aethel fsck -v
# → "ok   blob    1bb2d78c6d04..."
# → "ok   commit  95c419882714..."
# → (lists all objects)
```

### Edge Case: Corrupt an object, then fsck
```bash
# Corrupt one byte:
printf 'x' >> .aethel/objects/blobs/1b/b2d78c...   # (use a real path)
aethel fsck
# → "1 CORRUPT object(s):"
# → "  blob 1bb2d78c6d043feb1f72c4bb4b7294722bc7d3e7c87a0c57cb980c3797d513d3"
# → "    .aethel/objects/blobs/1b/b2d78c..."
# → "Repository integrity check FAILED."
# (exit code 1)
```

### Edge Case: Unreachable objects after branch delete
```bash
aethel branch -d emotion
aethel fsck
# → "5 unreachable object(s) not referenced by any branch; harmless (interrupted commits)"
# → "Repository integrity OK."
```

---

## 9. `aethel push`: Publish to Hub

> **Prerequisite**: Hub must be running in another terminal (see Section 11).

### Basic push
```bash
aethel push --remote http://localhost:8000 --repo sentiment-lora
```

### Push a specific branch
```bash
aethel push --remote http://localhost:8000 --repo sentiment-lora --branch emotion
```

### Push with token (when Hub requires auth)
```bash
aethel push --remote http://localhost:8000 --repo sentiment-lora --token pick-a-token
# Or via environment:
export AETHEL_HUB_TOKEN=pick-a-token
aethel push --remote http://localhost:8000 --repo sentiment-lora
```

### Dry run (show what would be uploaded)
```bash
aethel push --remote http://localhost:8000 --repo sentiment-lora --dry-run
# → Shows table of objects that would be sent
# → "--dry-run: nothing was uploaded."
```

### Re-push (idempotent: nothing new)
```bash
aethel push --remote http://localhost:8000 --repo sentiment-lora
# → "Hub already holds every object, nothing to upload."
# → "Pushed 0 object(s) · 6 already present"
# → "Transparency log: 1 leaves (0 new)"
# Log root unchanged!
```

### Edge Case: Hub not running
```bash
aethel push --remote http://localhost:9911 --repo sentiment-lora
# → "RemoteError: Cannot reach the Hub at http://localhost:9911. Is it running?"
# → "Start it with: python -m hub  (or scripts/dev.sh)"
```

### Edge Case: Wrong or missing token
```bash
aethel push --remote http://localhost:8000 --repo sentiment-lora --token wrong-token
# → "RemoteError: The Hub rejected the push token (negotiation)."
# → "Set it with: export AETHEL_HUB_TOKEN=<token>  (or --token)"
```

### Edge Case: Push while detached
```bash
aethel checkout <commit-hash>
aethel push --remote http://localhost:8000 --repo test
# → "InvalidRef: HEAD is detached at 95c419882714. Check out a branch first, or name one with --branch."
```

### Edge Case: Push a branch with no commits
```bash
aethel push --remote http://localhost:8000 --repo test --branch empty-branch
# → "InvalidRef: Branch 'empty-branch' has no commits yet. Run 'aethel train' then 'aethel commit' first."
```

### Edge Case: Default remote from environment
```bash
export AETHEL_HUB_URL=http://localhost:8000
aethel push --repo sentiment-lora
# Uses $AETHEL_HUB_URL automatically
```

---

## 10. `aethel --help`: General

```bash
aethel --help
aethel init --help
aethel train --help
aethel commit --help
aethel branch --help
aethel checkout --help
aethel log --help
aethel fsck --help
aethel push --help
aethel eval --help
aethel diff --help
aethel provenance --help
```

`aethel provenance` is the newest group and has its own subcommands:

```bash
aethel provenance status      # local checkpoint and mirror records
aethel provenance deploy      # deploy the checkpoint contract (owner only)
aethel provenance anchor      # publish the current log root
aethel provenance mirror      # pin a commit's adapter and verify the gateway round trip
aethel provenance verify      # check a bundle against a chain you name yourself
aethel provenance fetch-blob  # download a mirror and check its SHA-256
aethel provenance sync        # mirror then anchor, in one pass
```

---

## 11. Hub Server: Starting and Configuration

### Start with default settings
```bash
python -m hub
# Serves on 0.0.0.0:8000
```

### Start with seeded data
```bash
AETHEL_HUB_DATA="$PWD/.demo/hub-data" python -m hub
```

### Start with custom port
```bash
AETHEL_HUB_PORT=9000 python -m hub
```

### Start with auth token required
```bash
AETHEL_HUB_TOKEN=pick-a-token python -m hub
```

### Start with absolute data path (avoids the relative-path trap)
```bash
export AETHEL_HUB_DATA="$HOME/aethel-hub-data"
python -m hub
```

### Start with uvicorn directly
```bash
uvicorn hub.app:build --factory --host 0.0.0.0 --port 8000
```

### Edge Case: Hub extras not installed
```bash
# On base install without fastapi:
python -m hub
# → "The Hub needs its server dependencies (uvicorn). Install them with: pip install -e '.[hub]'"
```

### Prove data survives restart
```bash
# Before stopping:
curl -s http://localhost:8000/api/v1/log | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'leaves {d[\"size\"]}  root {d[\"root\"]}')"
# Stop Hub (Ctrl+C), restart, same command → same output
```

---

## 12. Dashboard Pages (Browser)

| URL | What You See |
|---|---|
| `http://localhost:8000/` | Landing: all repos, KPI tiles, log root |
| `http://localhost:8000/r/sentiment-lora` | Repository: accuracy chart with branch lines, version history with DAG rail |
| `http://localhost:8000/c/<full-64-char-hash>` | Commit: provenance, base model, files, Merkle inclusion proof |
| `http://localhost:8000/api` | API Reference: every endpoint documented |
| `http://localhost:8000/ops` | Operations: 8 health checks (object store, integrity, repo index, log, anchored root, chain, IPFS, auth) |
| `http://localhost:8000/provenance` | Provenance: live chain state, anchor records, mirror records, downloadable proof bundles |
| `http://localhost:8000/api/v1/provenance` | The same provenance state as JSON |

### Edge Case: Short hash on commit page
```
http://localhost:8000/c/<12-char-short-hash>
# → 404. Commit pages require the full 64-character hash.
```

### Edge Case: Non-existent repo
```
http://localhost:8000/r/nonexistent
# → 404 page
```

---

## 13. REST API: All Endpoints (curl)

### Version
```bash
curl -s http://localhost:8000/api/v1/version | python3 -m json.tool
```

### Health Check
```bash
curl -s http://localhost:8000/api/v1/health | python3 -m json.tool
```

### List repositories
```bash
curl -s http://localhost:8000/api/v1/repos | python3 -m json.tool
```

### Get single repository
```bash
curl -s http://localhost:8000/api/v1/repos/sentiment-lora | python3 -m json.tool
```

### List commits for a repo
```bash
curl -s http://localhost:8000/api/v1/repos/sentiment-lora/commits | python3 -m json.tool
```

### List commits for a specific branch
```bash
curl -s "http://localhost:8000/api/v1/repos/sentiment-lora/commits?branch=main" | python3 -m json.tool
```

### Get a single commit
```bash
curl -s http://localhost:8000/api/v1/commits/<full-64-char-hash> | python3 -m json.tool
```

### Get a tree
```bash
curl -s http://localhost:8000/api/v1/trees/<tree-hash> | python3 -m json.tool
```

### Get a base reference
```bash
curl -s http://localhost:8000/api/v1/bases/<base-hash> | python3 -m json.tool
```

### Download a blob
```bash
curl -s http://localhost:8000/api/v1/blobs/<blob-hash> -o adapter.bin
```

### Edge Case: Invalid hash format
```bash
curl -s http://localhost:8000/api/v1/commits/not-a-hash
# → 400: "commit hash must be a 64-character hex SHA-256 digest."
```

### Edge Case: Non-existent commit
```bash
curl -s http://localhost:8000/api/v1/commits/0000000000000000000000000000000000000000000000000000000000000000
# → 404
```

### Edge Case: Non-existent repo
```bash
curl -s http://localhost:8000/api/v1/repos/nonexistent
# → 404: "No repository 'nonexistent'."
```

### Edge Case: Non-existent branch on a repo
```bash
curl -s "http://localhost:8000/api/v1/repos/sentiment-lora/commits?branch=fake"
# → 404: "No branch 'fake'."
```

---

## 14. Transparency Log & Merkle Proofs

### Get log state
```bash
curl -s http://localhost:8000/api/v1/log | python3 -m json.tool
# Shows: size, root, entries[], last_anchor, current_root_anchored
```

### Get inclusion proof for a commit
```bash
curl -s http://localhost:8000/api/v1/log/proof/<full-commit-hash> | python3 -m json.tool
# Shows: commit_hash, leaf_index, log_size, root, proof[]
```

### Verify proof server-side (VALID)
```bash
# Save a proof:
curl -s http://localhost:8000/api/v1/log/proof/<full-commit-hash> > proof.json

# Verify it:
curl -s -X POST http://localhost:8000/api/v1/log/verify \
     -H 'Content-Type: application/json' --data @proof.json
# → {"valid": true, "commit_hash": "...", "root": "..."}
```

### Verify proof with TAMPERED data (INVALID): **Great demo moment!**
```bash
# Save proof, flip one hex digit, then verify:
curl -s http://localhost:8000/api/v1/log/proof/<full-commit-hash> > proof.json

# Tamper with one sibling hash:
python3 -c "
import json
with open('proof.json') as f: d = json.load(f)
s = d['proof'][0]['sibling']
d['proof'][0]['sibling'] = s[:5] + ('0' if s[5] != '0' else '1') + s[6:]
print(json.dumps(d))
" > tampered.json

curl -s -X POST http://localhost:8000/api/v1/log/verify \
     -H 'Content-Type: application/json' --data @tampered.json
# → {"valid": false, "commit_hash": "...", "root": "..."}
```

### In-browser verification
```
# On the commit page (http://localhost:8000/c/<hash>),
# click "Recompute in this browser": runs SHA-256 in JS,
# verifies proof without trusting the Hub server.
```

### Edge Case: Proof for non-logged commit
```bash
curl -s http://localhost:8000/api/v1/log/proof/0000000000000000000000000000000000000000000000000000000000000000
# → 404: "Commit 000000000000 is not in the transparency log."
```

---

## 14b. Provenance: Chain and IPFS

Every command below works from a base install; the chain commands need the `[provenance]` extra
and the mirror commands need a pinning credential.

### Local records
```bash
aethel provenance status
# → {"checkpoints": [...], "mirrors": [...]}
```

### Deploy the checkpoint contract (once, by the owner)
```bash
aethel provenance deploy --rpc "$AETHEL_CHAIN_RPC"
# → {"status": "confirmed", "block": ..., "contract": "0x..."}
```

### Anchor the current log root
```bash
aethel provenance anchor
# → refuses if the local log is shorter than the latest on-chain checkpoint
# → refuses if the signing key is not the contract owner
# → {"status": "confirmed", "size": 2, "root": "...", "confirmations": ...}
```

Re-running `anchor` with nothing new to publish exits without sending a transaction.

### Mirror a commit's adapter, then verify the round trip
```bash
aethel provenance mirror <commit-hash>
# → pins, downloads from the gateway, and compares SHA-256
# → {"status": "verified", "bytes": 2667272, "gateway": "..."}
```

Only adapters from accepted commits can be mirrored, and a failed verification leaves the record
at `failed` with its CID retained so a retry reuses it.

### Verify a bundle against a chain you name yourself
```bash
aethel provenance verify <commit-hash> \
  --bundle docs/evidence/checkpoint-2.json \
  --rpc https://sepolia.gateway.tenderly.co \
  --chain-id 11155111 \
  --contract 0xFe000a436a8Af301e96aFe3815Ab0ba0003C078E \
  --log-id 053b1222dab7d5c519cb146bd2661bd2287e17a0e9309a1e336ce9646758ede2
# → checks deployed bytecode, log identity, confirmations, inclusion, and every earlier prefix
```

The point of this command is that it takes the chain configuration from you, not from the bundle.
A bundle can only ever be evidence for a chain you already trust.

### Download a mirrored adapter without trusting a pinning record
```bash
aethel provenance fetch-blob <sha256> \
  --cid bafybeihsiewfwvy3u6e67bt7t6zs4fylikxlxca3347ovjq5osudkvjnta \
  --gateway https://amethyst-blank-crocodile-745.mypinata.cloud/ipfs \
  --gateway https://ipfs.io/ipfs \
  --output /tmp/review-adapter.safetensors
# → the file is installed only after the hash matches
```

### Edge Case: chain not configured
```bash
aethel provenance anchor
# → "Configure AETHEL_CHAIN_RPC, AETHEL_ANCHOR_CONTRACT, and AETHEL_LOG_ID"
```

### Edge Case: provenance extra not installed
```bash
aethel provenance anchor
# → "Install the provenance extra: pip install -e '.[provenance]'"
```

### Edge Case: mirror a commit that is not in the log
```bash
aethel provenance mirror <some-other-commit>
# → "Only adapter blobs from accepted commits can be mirrored"
```

---

## 15. Tests: Show Code Quality

### Run full test suite
```bash
python -m pytest
# → 797 passed, 6 skipped on a base install (the skips are ML and web3 tests)
# → 836 passed in the full venv
```

### Run with coverage
```bash
python -m pytest --cov=aethel.core
# → 96% core coverage
```

### Run specific test files
```bash
python -m pytest tests/test_core_refs.py          # 52 tests - HEAD, branches
python -m pytest tests/test_core_merkle.py         # 29 tests - domain separation, proofs
python -m pytest tests/test_core_objects.py         # 29 tests - store, dedup, integrity
python -m pytest tests/test_core_hashing.py         # 24 tests - canonical JSON, SHA-256
python -m pytest tests/test_core_commits.py         # 23 tests - creation, lineage
python -m pytest tests/test_core_atomic.py          # 18 tests - crash safety, locking
python -m pytest tests/test_core_resolve.py         # 15 tests - branch/hash resolution
python -m pytest tests/test_s1_corruption.py        # 10 tests - metadata-collision regression
```

### Lint
```bash
ruff check .
```

### Cross-check JS verifier against Python hashlib
```bash
python scripts/check_verify_js.py
```

---

## 16. Full End-to-End Demo Script (Copy-Paste Ready)

### Terminal 1: Hub Server
```bash
cd /home/sathwik/zzz/Capstone/Aethel
source .venv/bin/activate
python scripts/seed_demo.py --force
AETHEL_HUB_DATA="$PWD/.demo/hub-data" python -m hub
```

### Terminal 2: CLI Demo
```bash
cd /home/sathwik/zzz/Capstone/Aethel
source .venv/bin/activate

# ── Create workspace ──
mkdir -p demo-review && cd demo-review

# ── Phase 1: Init ──
aethel init --model distilbert-base-uncased --author johndoe

# ── Phase 2: Train + Commit ──
aethel train --config ../train_yaml/sst2_config.yaml
aethel commit -m "SST-2 sentiment baseline"
aethel log
aethel status

# ── Phase 3: Second version ──
aethel train --config ../train_yaml/sst2_config.yaml
aethel commit -m "SST-2 more epochs"
aethel log

# ── Phase 4: Branch for different task ──
aethel branch emotion
aethel checkout emotion
aethel train --config ../train_yaml/emotion_config.yaml
aethel commit -m "Emotion 6-class"
aethel log --all

# ── Phase 5: Time travel ──
aethel checkout main       # sentiment adapter restored
aethel checkout emotion    # emotion adapter restored
aethel checkout main

# ── Phase 6: Integrity ──
aethel fsck

# ── Phase 7: Push to Hub ──
aethel push --remote http://localhost:8000 --repo my-model
aethel checkout emotion
aethel push --remote http://localhost:8000 --repo my-model

# ── Phase 8: Re-push (idempotent) ──
aethel push --remote http://localhost:8000 --repo my-model

# ── Phase 9: Dry run ──
aethel push --remote http://localhost:8000 --repo my-model --dry-run

# ── Phase 10: Browse dashboard ──
echo "Open: http://localhost:8000/"
echo "Open: http://localhost:8000/r/my-model"
echo "Open: http://localhost:8000/ops"
```

---

## 17. Edge Cases Summary Table

| Scenario | Command | Expected Error |
|---|---|---|
| Command outside repo | `aethel log` (in /tmp) | `Not an Aethel repository` |
| Log before any commit | `aethel log` | `No commits yet` |
| Commit with empty workspace | `aethel commit -m "x"` | `ObjectNotFound: No adapter weights` |
| Train with missing dataset | `aethel train -c config.yaml` | `Dataset path does not exist` |
| Checkout with dirty workspace | `aethel checkout other` | `Workspace has uncommitted changes` |
| Detach HEAD | `aethel checkout <hash>` | `HEAD is now detached` |
| Commit while detached | `aethel commit -m "x"` | `Detached HEAD, commit blocked` |
| Push while detached | `aethel push ...` | `HEAD is detached` |
| Checkout non-existent | `aethel checkout nope` | `'nope' did not match any branch or commit` |
| Branch already exists | `aethel branch main` | `A branch named 'main' already exists` |
| Delete current branch | `aethel branch -d main` | `Cannot delete 'main'; it is the current branch` |
| Push to dead Hub | `aethel push --remote http://localhost:9911 ...` | `Cannot reach the Hub` |
| Push with wrong token | `aethel push --token wrong ...` | `Hub rejected the push token` |
| Push empty branch | `aethel push --branch empty ...` | `Branch has no commits yet` |
| Corrupt object + fsck | `printf 'x' >> .aethel/objects/...` + `aethel fsck` | `CORRUPT object(s)` |
| Delete branch + fsck | `aethel branch -d X` + `aethel fsck` | `unreachable object(s)` |
| Short hash on web | `/c/<12-chars>` | 404 |
| Invalid hash in API | `curl .../commits/bad` | 400 |
| Tampered Merkle proof | Flip 1 hex digit + verify | `{"valid": false}` |
| `AETHEL_HUB_DATA` trap | Start Hub from wrong directory | Empty dashboard (different data dir) |
| Hub restart persistence | Stop + restart Hub | Same root, same repos |
| Anchor with nothing new | Run `aethel provenance anchor` twice | Second run sends no transaction |
| Bundle for the wrong chain | `verify` with a different `--chain-id` | Refuses before checking inclusion |
| Tampered gateway bytes | Flip a byte in a downloaded blob | `Gateway bytes do not match the expected SHA-256` |
| Mirror an unlogged commit | `aethel provenance mirror <other>` | `Only adapter blobs from accepted commits can be mirrored` |

---

## 18. Environment Variables Reference

| Variable | Default | Purpose |
|---|---|---|
| `AETHEL_HUB_DATA` | `./hub-data` | Hub's object store location |
| `AETHEL_HUB_HOST` | `0.0.0.0` | Bind address |
| `AETHEL_HUB_PORT` | `8000` | Bind port |
| `AETHEL_HUB_TOKEN` | *(unset = open)* | Push auth token |
| `AETHEL_HUB_URL` | `http://127.0.0.1:8000` | Default remote for `aethel push` |
| `AETHEL_AUTHOR` | `$USER` | Commit author |
| `AETHEL_HUB_MAX_BLOB` | 64 MiB | Max blob upload size |
| `AETHEL_CHAIN_RPC` | *(unset)* | Checkpoint chain RPC URL |
| `AETHEL_CHAIN_ID` | `11155111` | Chain ID, Sepolia by default |
| `AETHEL_CHAIN_CONFIRMATIONS` | `2` | Confirmations required before a checkpoint counts |
| `AETHEL_ANCHOR_CONTRACT` | *(unset)* | Deployed checkpoint contract address |
| `AETHEL_LOG_ID` | *(unset)* | Transparency log identity bound into the contract |
| `AETHEL_ANCHOR_KEY` | *(unset)* | Signing key, read only by the transaction commands |
| `AETHEL_MAX_FEE_GWEI` | `10` | Gas price ceiling; the command refuses above it |
| `AETHEL_PROBE_INTERVAL` | `60` | Seconds between background chain and gateway probes |
| `AETHEL_PINNING_ENDPOINT` | `https://api.pinata.cloud` | Pinning service endpoint |
| `AETHEL_PINNING_BACKEND` | `pinata` | `pinata` or `kubo` |
| `AETHEL_PINATA_JWT` | *(unset)* | Pinning credential, read only by the mirror commands |
| `AETHEL_IPFS_GATEWAY` | `https://gateway.pinata.cloud/ipfs` | Retrieval gateway, tried in order |

The signing key and the pinning credential are read only by the commands that write to a third
party. `aethel provenance status`, `verify`, and `fetch-blob` never load them, and the review
server strips them from its own environment before it starts.
