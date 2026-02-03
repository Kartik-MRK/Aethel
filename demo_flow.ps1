# Demo Flow for Aethel-Git
Write-Host "starting Aethel-Git Demo..." -ForegroundColor Cyan

# 1. Clear previous run (for fresh start)
if (Test-Path .aethel) {
    Write-Host "Cleaning up previous repo..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force .aethel
}

# 2. Init
Write-Host "`n[Step 1] Initializing Repository..." -ForegroundColor Green
aethel init

# 3. Status (Empty)
Write-Host "`n[Step 2] Checking Status (Should be empty)..." -ForegroundColor Green
aethel status

# 4. Commit 1
Write-Host "`n[Step 3] Creating First Commit (Base Version)..." -ForegroundColor Green
aethel commit -m "Initial commit - Base Model State"

# 5. Commit 2
Write-Host "`n[Step 4] Creating Second Commit (Training Step)..." -ForegroundColor Green
aethel commit -m "Training Run 1 - Added Math Features"

# 6. Log
Write-Host "`n[Step 5] Viewing Log History..." -ForegroundColor Green
aethel log

Write-Host "`nDemo Complete!" -ForegroundColor Cyan
