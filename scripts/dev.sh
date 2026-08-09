#!/usr/bin/env bash
# Aethel development setup — Linux / macOS.
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

# Base + dev only. The ML stack is large and not needed to run the tests;
# install it explicitly with: pip install -e ".[ml]"
echo "Installing aethel[dev]"
pip install -e ".[dev]" --quiet

echo
echo "Running checks"
ruff check aethel/ tests/
pytest tests/ -q

echo
echo "Ready. Activate with:  source $VENV/bin/activate"
echo "Train support (large):  pip install -e '.[ml]'"
