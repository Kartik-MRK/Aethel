# Aethel development setup - Windows PowerShell.
# Idempotent: safe to re-run. Mirrors scripts/dev.sh exactly.
$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

$Venv = ".venv"

if (-not (Test-Path $Venv)) {
    Write-Host "Creating virtual environment in $Venv"
    python -m venv $Venv
}

$Activate = Join-Path $Venv "Scripts\Activate.ps1"
. $Activate

python -m pip install --upgrade pip --quiet

# Base, dev and hub. The ML stack is large and not needed to run the tests or
# the Hub; install it explicitly with: pip install -e ".[ml]"
Write-Host "Installing aethel[dev,hub]"
pip install -e ".[dev,hub]" --quiet

Write-Host ""
Write-Host "Running checks"
# The whole tree, not just aethel/ and tests/ - a lint that skips hub/ and
# scripts lets exactly the code under active development drift.
ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

pytest tests/ -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Ready. Activate with:  .\$Venv\Scripts\Activate.ps1"
Write-Host "Start the Hub with:    python -m hub"
Write-Host "Train support (large):  pip install -e '.[ml]'"
