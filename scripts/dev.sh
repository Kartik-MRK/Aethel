#!/usr/bin/env bash
# Aethel development setup for Linux and macOS.
# Idempotent: safe to re-run. Mirrors scripts/dev.ps1 exactly.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
VENV=".venv"

if [ ! -d "$VENV" ]; then
  echo "Creating virtual environment in $VENV"
  "$PYTHON" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

python -m pip install --upgrade pip --quiet

# Base, dev and hub. The ML stack is large and not needed to run the tests or
# the Hub; install it explicitly with: pip install -e ".[ml]"
echo "Installing aethel[dev,hub]"
pip install -e ".[dev,hub]" --quiet

echo
echo "Running checks"
# The whole tree, not just aethel/ and tests/: a lint that skips hub/ and
# scripts lets exactly the code under active development drift.
ruff check .
pytest tests/

echo
echo "Ready. Activate with:  source $VENV/bin/activate"
echo "Start the Hub with:    python -m hub"
echo "Train support (large):  pip install -e '.[ml]'"
