# Check if Python is installed
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python is not installed or not in the PATH."
    exit 1
}

# Create virtual environment if it doesn't exist
if (-not (Test-Path .venv)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
} else {
    Write-Host "Virtual environment already exists."
}

# Activate virtual environment
Write-Host "Activating virtual environment..."
& .\.venv\Scripts\Activate.ps1

# Upgrade pip
Write-Host "Upgrading pip..."
python -m pip install --upgrade pip

# Install requirements
if (Test-Path requirements.txt) {
    Write-Host "Installing dependencies from requirements.txt..."
    pip install -r requirements.txt
} else {
    Write-Warning "requirements.txt not found."
}

Write-Host "Setup complete. To activate the environment, run: .\.venv\Scripts\Activate.ps1"
