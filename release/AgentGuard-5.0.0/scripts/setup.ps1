[CmdletBinding()]
param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    Write-Host "Creating Python virtual environment..."
    if ($py) {
        # Prefer the documented baseline, then any installed launcher, then PATH python.
        & $py.Source -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPath)) {
            & $py.Source -m venv .venv
        }
    }
    if (($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPath)) -and $pythonCommand) {
        & $pythonCommand.Source -m venv .venv
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw "Python 3.12 or later is required. Install it, then run this script again."
    }
}

$python = $pythonPath
Write-Host "Installing backend, ML, and AutoGen dependencies..."
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit $LASTEXITCODE)." }
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Backend/ML/AutoGen dependency installation failed (exit $LASTEXITCODE)." }

New-Item -ItemType Directory -Force data | Out-Null
# Use a fresh disposable DB for bootstrap checks; never mutate an existing user DB here.
$env:AGENTGUARD_DATABASE_URL = "sqlite:///./data/agentguard.db"
& $python -c "from backend.database import init_db; init_db(); print('SQLite database initialized.')"
if ($LASTEXITCODE -ne 0) { throw "SQLite initialization failed (exit $LASTEXITCODE)." }

$phase6Artifact = Join-Path $projectRoot "ml\artifacts\phase6\phase6_model_bundle.joblib"
if (Test-Path -LiteralPath $phase6Artifact) {
    Write-Host "Validating the included Phase 6 model artifact..."
    & $python -m ml.phase6_pipeline inspect --artifact-dir ml/artifacts/phase6
    if ($LASTEXITCODE -ne 0) { throw "Included Phase 6 artifact is incompatible; retrain with the documented Phase 6 command." }
} else {
    Write-Host "Training the Phase 6 artifact from the checked-in synthetic dataset..."
    & $python -m ml.phase6_pipeline train --dataset ml/data/phase6_scenarios_v1.jsonl --artifact-dir ml/artifacts/phase6 --seed 20261027 --report-dir reports
    if ($LASTEXITCODE -ne 0) { throw "Phase 6 model training failed (exit $LASTEXITCODE)." }
}

if (-not $SkipFrontend) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { throw "Node.js 20.9+ and npm are required for the dashboard." }
    Write-Host "Installing dashboard dependencies from package-lock.json..."
    Push-Location frontend
    try {
        & $npm.Source ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed (exit $LASTEXITCODE)." }
    } finally { Pop-Location }
}

Write-Host "Setup complete. Start the app with: .\scripts\start.ps1"
