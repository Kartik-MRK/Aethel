# Development and repository maintenance

See the [documentation index](README.md) for current scope and [Architecture](architecture.md)
for implementation boundaries. This guide covers source maintenance rather than Aethel's own
adapter branches; Git branches and `aethel branch` serve different repositories.

## Git workflow and team identity

Documentation and repository housekeeping are maintained directly on `main`. Start from the
current merged state:

```bash
git switch main
git pull --ff-only origin main
git status --short
```

Coordinate separate feature work with the team before selecting its branch. If a fast-forward is refused,
inspect the local/remote history and preserve local commits rather than resetting them away.

Check your Git author identity before committing. Use the actual team member's account and a
simple one-line conventional title, such as `docs: consolidate project documentation`.
Documentation-only maintenance can be committed and pushed directly to `main` after its checks.
The current team preference is an empty commit body; when a PR is needed for other work, use a
clear one-line title and no description unless the team requests one.

The project is Team 91: Sathwik, Karthik, Adyaa, and Shravan. Report ownership according to the
team's actual work. Preserve third-party license notices for packaged fonts and other dependencies.

## Source layout

| Path | Contents |
|---|---|
| [aethel/core](../aethel/core/) | Pure repository/storage/hash/ref/locking logic |
| [aethel/commands](../aethel/commands/) | CLI entry points and orchestration |
| [aethel/evaluation](../aethel/evaluation/) and [aethel/ml](../aethel/ml/) | Dataset protocol, evaluation, adapter comparison |
| [aethel/remote](../aethel/remote/) | Hub transport, push planning, clone/pull |
| [aethel/provenance](../aethel/provenance/) | Mirror and checkpoint operations; packaged contract artifact |
| [hub](../hub/) | API, storage/log, templates, static assets, status probes |
| [contracts](../contracts/) | Solidity source, pinned compiler dependencies, build script |
| [deploy](../deploy/) | Optional systemd worker/server and HTTPS proxy templates |
| [scripts](../scripts/) | Setup, data/demo preparation, verification, asset building |
| [tests](../tests/) | Core, CLI, training, Hub, retrieval, and provenance regression tests |
| [train_yaml](../train_yaml/) | Small classification example configurations |
| [docs](./) | Current guides and public evidence; no duplicate historical manuals |

Add new modules to the appropriate package and register CLI commands in `aethel/main.py`.
Keep domain logic separate from terminal rendering. Preserve optional-dependency imports so
basic versioning and retrieval work without ML/server extras. Avoid expanding the supported
model/task/merge scope without a concrete team requirement.

## Verification

Set up a virtual environment and install dependencies for the affected area:

```bash
python -m pip install -e '.[dev,hub]'
python -m pytest tests/
ruff check .
git diff --check
```

For the full feature suite, including CPU model tests and contract execution:

```bash
python -m pip install -e '.[dev,hub,ml,provenance-test,fonts]'
python -m pytest tests/ --cov=aethel --cov=hub --cov-report=term-missing
```

The four CI jobs in [.github/workflows/ci.yml](../.github/workflows/ci.yml) cover:

| Job | Environment and checks |
|---|---|
| Core | Python 3.10–3.13, base/dev only, full available tests and core coverage ≥80% |
| Hub | Python 3.12, dev/Hub, explicit imports, lint and HTTP tests |
| ML | Python 3.12, CPU dependencies, offline training/evaluation/diff tests |
| Provenance | Python 3.12 and Node 22, contract artifact rebuild/parity and EVM/IPFS/Hub integration |

Hub and ML tests skip when their extras are absent. Report the installed extras and skip
counts rather than presenting one environment's result as universal. Tests use temporary
repositories; seeded dashboard metrics and stub training are not model-quality evidence.
The HTTP tests exercise FastAPI in process, but restrictive execution sandboxes may still
block the test transport. The [evidence record](evidence/README.md#software-validation) lists
the most recent complete local validation and its limits.

Select checks that exercise the behavior changed. For documentation maintenance, validate local
links and code examples, remove references to deleted paths, and run lint/whitespace checks.
For moved scripts, verify imports and path handling without triggering network downloads.
Do not rerun training or publish external artifacts merely to change prose.

### Contracts and browser verification

The compiled contract JSON is a required runtime package asset, not redundant generated clutter:

```bash
npm ci --prefix contracts --ignore-scripts
npm run --prefix contracts build
git diff --exit-code -- aethel/provenance/contracts/AethelCheckpoint.json
python scripts/check_verify_js.py
```

The compiler's package/lock files live under `contracts/`. The Hub has no Node frontend build.
For a browser check, install Playwright, have Google Chrome available, and run against a
prepared or disposable seeded Hub:

```bash
python -m pip install playwright
python scripts/check_dashboard.py --url http://127.0.0.1:8787
```

The default output directory is `.demo/public-review/screenshots`. The check covers navigation,
filtering, refresh, themes, mobile overflow, browser proof recomputation, and console errors.
It needs a Hub with at least one commit. Playwright is an optional browser verification tool, not a runtime
dependency. Use `--output` to keep screenshots from separate rehearsals apart.

### Packaging and fonts

```bash
python -m pip wheel . --no-deps --wheel-dir /tmp/aethel-wheel
python -m pip install -e '.[fonts]'
python scripts/build_fonts.py
```

Use a suitable output directory on Windows. The wheel includes CLI/core packages, templates,
JavaScript, CSS, fonts, and compiled contract JSON. Font rebuilding can download pinned upstream
sources into `.font-cache`; serving the dashboard uses the committed `.woff2` files and needs
no external font provider. Keep the [font notices](../hub/static/fonts/NOTICE.md) and OFL files.

## Script inventory

| Script | Use |
|---|---|
| `dev.sh` / `dev.ps1` | Install base/dev/Hub and run checks |
| `setup_demo_data.py` | Download five small public CSV datasets into the checkout's `datasets/` |
| `seed_demo.py` | Create a synthetic Hub fixture through its real API |
| `prepare_review_models.py` | Train two measured public-data experiments in a new work directory |
| `publish_review_provenance.py` | Configure/publish a prepared experiment using separate worker settings; external operations are deliberate |
| `serve_review.py` | Serve a prepared demonstration using its restricted Hub environment |
| `check_dashboard.py` / `check_verify_js.py` | Browser behavior and cross-language Merkle verification |
| `build_fonts.py` | Rebuild packaged font subsets from pinned sources |

The five-dataset helper, synthetic seed, and measured-experiment preparation have different purposes;
they are not interchangeable. The dataset helper is in `scripts/`; its outputs go to the
checkout-root `datasets/` location.

## Files to preserve and files to regenerate

Committed source, tests, templates/assets, training examples, contract build inputs/artifacts,
public receipt JSON, and license files are part of the project. Keep public evidence stable;
record new checks separately rather than rewriting a historical receipt's result.

Runtime repositories (`.aethel/`), Hub data (`hub-data/`), prepared demos (`.demo/`), source
CSV datasets, credentials, and local environments are ignored state. Back them up according
to [Operations](operations.md) before any deliberate removal. A source cleanup is not a request
to destroy a team's model history or local credentials.

Python/test/lint caches, coverage scratch output, and `build/` can be regenerated. Editable
installation metadata and installed dependencies may still be needed by the local environment;
do not delete them blindly. Store new example files in their existing subject directories
rather than adding competing root-level setup documents.

## Documentation rules

Keep the root README short. Put usage in `guide.md`, design in `architecture.md`, operational
procedures in `operations.md`, maintenance in this guide, and measured results under `evidence/`.
Use the index for supported scope and the reading order. Cross-link instead of copying complete
sections. Update CLI examples and claims when behavior changes. The HTML explainer is the deliberate standalone companion for the team and project guide.
Keep its technical content aligned with the Markdown guides and source.
