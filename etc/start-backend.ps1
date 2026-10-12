# PowerShell version of start-backend.sh
$ErrorActionPreference = 'Stop'

# Resolve project root (parent of the script's directory)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ROOT = Resolve-Path "$scriptDir\.."
$BACKEND = Join-Path $ROOT 'backend'

Set-Location $BACKEND

# Ensure a virtual environment exists
if (-not (Test-Path '.venv')) {
    python -m venv .venv
    & "${PWD}\venv\Scripts\pip.exe" install -r requirements.txt
}

# Run the backend server – forward any arguments passed to the script
& "${PWD}\venv\Scripts\python.exe" -m reqman serve @args

