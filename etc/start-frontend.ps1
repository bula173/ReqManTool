# PowerShell version of start-frontend.sh
$ErrorActionPreference = 'Stop'

param(
    [Parameter(ValueFromRemainingArguments=$true)]
    $RemainingArgs
)

# Resolve project root (parent of the script's directory)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ROOT = Resolve-Path "$scriptDir\.."
$FRONTEND = Join-Path $ROOT 'frontend'

Set-Location $FRONTEND

# Ensure node_modules exists
if (-not (Test-Path 'node_modules')) {
    npm install
}

# Run the frontend (pass any arguments to npm)
npm run dev @RemainingArgs

