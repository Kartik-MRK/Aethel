# Landing work on a branch

This is the procedure for bringing work that was written outside the repository — a new package, a
new command, a change to an existing module — into `foundation` without breaking anyone else's copy.
It is deliberately short. Read it once, then follow the command blocks.

The rule behind all of it: **`foundation` must be green at every commit.** Anyone should be able to
clone it, install, run the suite, and see it pass. That is what makes it safe for four people to work
on the same tree at once, and it is the only rule here that is not negotiable.

---

## 1. The branch layout

```
main          the last reviewed state. Nobody commits here directly.
foundation    the integration branch. Everything lands here first, by pull request.
<your work>   your branch, cut from foundation, one topic, opened as a PR into foundation.
```

`main` is behind `foundation` on purpose — it moves when a review boundary is passed, not when a
feature is finished. **Always branch from `foundation`, never from `main`**, or your first PR will
carry a hundred unrelated commits.

---

## 2. Set up once

If you do not have the repository yet:

```bash
git clone https://github.com/Kartik-MRK/Aethel.git
cd Aethel
```

Then install it in editable mode with the extras you need. The base install is deliberately small —
no torch, no FastAPI — so pick what your work touches:

```bash
python3 -m pip install -e '.[dev]'            # tests + linter. Everyone needs this.
python3 -m pip install -e '.[dev,hub]'        # ...plus the Hub server
python3 -m pip install -e '.[dev,hub,ml]'     # ...plus torch/transformers/peft (large)
```

Confirm the baseline before you change anything:

```bash
python3 -m pytest -q          # expect: 715 passed
ruff check .                  # expect: All checks passed!
```

If either of those is red on a fresh clone, stop and say so in the group — that is a broken
`foundation`, not your problem to work around.

> `ruff check .` — with the dot. Never `ruff check *`, which the shell expands into a file list that
> silently skips dotfiles and directories.

### If you are on Windows, read this bit

The repository carries a `.gitattributes` that stores every text file with LF and checks it out with
LF, so a Windows clone and a Linux clone hold identical bytes. You do not have to configure anything
for that to work. Two things are still worth doing once:

```powershell
git config --global core.autocrlf false     # let .gitattributes decide, not a global setting
git config --global core.longpaths true     # this tree nests deeply enough to hit the 260-char limit
```

Then tell your editor to save with LF. VS Code: bottom-right status bar shows `CRLF` or `LF`; click it
and pick LF. Notepad and older PowerShell ISE write CRLF and will not ask.

Two reasons this matters more here than in a normal repository. A file committed with CRLF shows as
*every line changed*, so a two-line fix arrives as a 400-line diff and nobody can review it. And this
project addresses content by SHA-256 — the same text checked out with CRLF hashes to something
different from the LF original, so mixed endings could produce two different object hashes for
identical content. That is the one bug class the whole storage layer exists to prevent.

If you already have a clone with CRLF in it, fix it in place rather than re-cloning:

```powershell
git add --renormalize .
git status --short          # if this lists files, commit them as: style(repo): normalise line endings
```

Everything else in this guide is identical on both platforms. `scripts/dev.ps1` is the Windows
equivalent of `scripts/dev.sh`; both are idempotent and safe to re-run.

---

## 3. Sync, then cut your branch

Do this every time you start a piece of work, not just the first time:

```bash
git fetch origin
git checkout foundation
git pull --ff-only origin foundation
```

`--ff-only` is there on purpose. If it refuses, you have local commits on `foundation` that should
have been on a branch; move them (`git switch -c my-branch`) rather than merging.

Now cut your branch. One topic per branch, named for the topic:

```bash
git switch -c feat/evaluation-metrics
```

Names that work: `feat/<thing>`, `fix/<thing>`, `docs/<thing>`, `test/<thing>`. Names that do not:
your own name, `new`, `final`, `final2`.

---

## 4. Put the files where they belong

| What you wrote | Where it goes |
|---|---|
| A new subsystem | `aethel/<name>/` with an `__init__.py` that exports the public functions |
| A new CLI command | `aethel/commands/<name>.py`, registered in `aethel/main.py` |
| Anything Hub-side | `hub/<name>.py`; templates in `hub/templates/`, assets in `hub/static/` |
| Tests | `tests/test_<area>.py` — one file per module, matching the existing names |
| A design note | `docs/design/<name>.md` |

Copy the files in by hand rather than replacing directories. If you are tempted to `cp -r` a whole
tree over the repository, that is the moment to stop: it is how someone else's uncommitted work gets
deleted, and how a `__pycache__` or a `hub-data/` ends up in a commit.

**Never commit:** `.env`, anything with a key or token in it, `hub-data/`, `.demo/`, `.aethel/`,
adapter weights, `__pycache__/`, `*.pyc`, virtualenvs, datasets. `.gitignore` covers the usual ones;
check `git status --short` before every commit anyway and look at what is actually staged with
`git diff --cached --stat`.

One gotcha in the other direction: `.gitignore` also excludes `*.json`, `*.csv`, `*.safetensors` and
`*.bin` globally, because those are almost always data here. If you genuinely need to commit one — a
small test fixture, say — add it with `git add -f path/to/file.json` and mention it in the PR so it
is a visible decision rather than a surprise.

---

## 5. Guard anything that needs a heavy dependency

The suite runs on machines that do not have torch installed, and it has to stay green there. So a
test that imports torch, transformers, peft or FastAPI **skips** rather than errors. Two things at
the top of the file:

```python
import pytest

pytest.importorskip("torch", reason="the evaluator needs the [ml] extra")

pytestmark = pytest.mark.ml
```

The `ml` marker is already declared in `pyproject.toml`, and the same pattern with `"fastapi"` is
used by every Hub test file — `tests/test_hub_views.py` is the example to copy. In library code the
equivalent is to import the heavy module inside the function that uses it, not at module top level,
so that `aethel --help` still works on a machine with no torch.

---

## 6. Before you push

Run all four. They take under fifteen seconds together.

```bash
ruff check .
python3 -m pytest -q
git status --short
git diff --cached --stat
```

Then check the list:

- The suite passes, and the number went **up** by however many tests you added — not down.
- Nothing you added is unreachable from a test. A module with no test does not count as landed.
- No secret, key, token, mnemonic or absolute path from your machine appears anywhere in the diff.
- No AI-assistant attribution, co-author trailer or generated signature in any commit message, file
  header or docstring.
- Commit messages are one line, conventional style, no body and no trailers:

  ```
  feat(evaluation): compute held-out accuracy and adapter size for a commit
  fix(refs): reject a branch name that ends in .lock
  test(core): cover the resolve path for an ambiguous prefix
  ```

  Types in use: `feat`, `fix`, `test`, `docs`, `refactor`, `style`, `chore`. Scopes already in use:
  `core`, `cli`, `hub`, `tests`. Add a new scope only when none of those fits.

---

## 7. Push and open the pull request

```bash
git push -u origin feat/evaluation-metrics
```

Then open a PR into `foundation` — not `main` — on GitHub. Write three things in the body:

1. **What it does**, in two sentences.
2. **How to see it work**, as commands someone else can paste.
3. **What is not done yet**, honestly. This is the most useful line in the whole PR.

Leave it for one other person to read before merging. If CI is red, it does not merge; fix it on the
branch and push again.

If `foundation` moved while you were working and the PR shows a conflict:

```bash
git fetch origin
git rebase origin/foundation
# fix the conflicts, then:
git push --force-with-lease
```

`--force-with-lease` rather than `--force` — it refuses if someone else pushed to your branch in the
meantime, which is exactly the accident plain `--force` causes.

---

## 8. The two pieces of work waiting to land

Both of these are written and living outside the repository. Neither one needs anybody else's files
changed, which is the point of the seams — the interfaces are already there.

### The evaluation and comparison package

Lands as `aethel/evaluation/` — dataset loading, base-plus-adapter loading, loss/accuracy/adapter
size, and the current-versus-parent comparison — plus the hook already present in
`aethel/commands/commit.py` that writes the result into `training_info["evaluation"]`.

What already exists on the other side of the seam, so **do not change it**:

- `hub/views.py` reads `training_info["evaluation"]["current"]` and prefers it over the training
  metrics when both are present (`_first_metric`). The dashboard's accuracy column and chart will
  start showing real numbers the moment real ones arrive — no Hub change needed.
- `training_info["metrics"]` stays where it is. That is the trainer's loss and runtime, a different
  measurement, and both shapes are expected to coexist in one commit record.
- On a root commit, `"parent"` and `"comparison"` are `None`, not zeroes. Keep that — the dashboard
  distinguishes "not measured" from a real value of zero, and the tests around the metrics pipeline
  assert that distinction.

Still owed with it: its own test file, `tests/test_evaluation.py`, guarded as in section 5, and a real
run recorded so the numbers on the dashboard stop being seeded fixtures.

And one correction to make on the way in, because it changes what the number means. The evaluator
currently calls `load_validation_dataset`, which reads `dataset_file` out of `training_info.json` —
that is the file the adapter *trained* on, so the accuracy it reports is accuracy on seen data. The
current-versus-parent comparison is still meaningful, because both sides are scored the same way, but
the absolute figure is not quotable until there is a deterministic train/validation split and the
evaluator scores the held-out half. Do that in the same branch if there is time, or immediately after
it merges. Until it exists, say "accuracy on the training split" and not "accuracy" — the deck and
`README.md` both already say so, and a number that quietly overstates itself is the one thing a panel
will find.

### Divergence detection between consecutive patches

Lands as a module under `aethel/core/` or `aethel/ml/` plus a call site in the commit path: embed the
new patch and its parent, take the cosine similarity, and when it falls below the threshold, prompt
the user to stay on the branch or fork. Non-destructive — the prompt happens before anything is
written, and both answers are valid.

Still owed with it: a test file covering the similarity computation and the threshold decision with
synthetic weight tensors (no torch needed for the arithmetic if the vectors are constructed
directly), and the prompt exercised with the input mocked. Merge semantics are a separate, later
piece of work; this branch should not wait on them.

---

## 9. After a merge

Two small chores that keep the documentation from drifting:

```bash
git fetch origin && git checkout foundation && git pull --ff-only origin foundation
python3 -m pytest -q                      # the new total is the number everyone quotes
python3 -m pytest --cov=aethel --cov=hub -q | tail -20
```

Update the test count and any per-file line counts wherever they are asserted — a stated number that
is no longer true costs more credibility than no number at all.
