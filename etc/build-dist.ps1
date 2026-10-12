# PowerShell script to build the full Windows desktop distribution (Tauri)
# Usage: .\etc\build-dist.ps1 --desktop

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet('--desktop')]
    [string]$Target = '--desktop'
)

function Log { param([string]$msg) Write-Host "[build-dist] $msg" }

# Ensure required tools are available
foreach ($cmd in @('cargo','node','npm','python')) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Log "ERROR: $cmd not found. Install and add to PATH."; exit 1
    }
}
Log "All required tools are present."

# Resolve project layout
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ROOT = Resolve-Path "$scriptDir\.."
$DESKTOP = Join-Path $ROOT 'desktop'
$distDesktop = Join-Path (Join-Path $ROOT 'dist') 'desktop'
$TAURI = Join-Path $DESKTOP 'src-tauri'
$LOGDIR = Join-Path $DESKTOP '.logs'

if (-not (Test-Path $LOGDIR)) { New-Item -ItemType Directory -Path $LOGDIR | Out-Null }
Log "Log dir: $LOGDIR"

function Bundle-Backend {
    Log "Bundling Python backend into $TAURI\backend"
    $stage = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $stage | Out-Null
    foreach ($pkg in @('reqman','reqman_server')) {
        $src = Join-Path (Join-Path $ROOT 'backend') $pkg
        if (Test-Path $src) { robocopy $src $stage $null /E /XD __pycache__ /NFL /NDL /NJH /NJS | Out-Null }
    }
    foreach ($file in @('requirements.txt','pyproject.toml','LICENSE')) {
        $srcFile = Join-Path (Join-Path $ROOT 'backend') $file
        if (Test-Path $srcFile) {
            Copy-Item $srcFile $stage
            if ($file -eq 'requirements.txt') {
                # Upgrade pip
                python -m pip install --upgrade pip | Out-Null
                # Install required packages into the staging directory
                python -m pip install -r $srcFile --target $stage | Out-Null
            }
        }
    }
    $main = @"
import sys
from reqman.cli import main
sys.exit(main())
"@
    Set-Content -Path (Join-Path $stage '__main__.py') -Value $main -Encoding UTF8
    $tauriBackend = Join-Path $TAURI 'backend'
    if (Test-Path $tauriBackend) { Remove-Item -Recurse -Force $tauriBackend }
    Move-Item $stage $tauriBackend
    Log "Backend bundled."

# -------------------------------------------------
# Build the Python backend as a standalone executable (PyInstaller)
# -------------------------------------------------
$specPath = Join-Path $ROOT 'backend\build\reqman_backend.spec'
if (Test-Path $specPath) {
    Log "Running PyInstaller to produce backend exe"
    # Ensure the spec can locate the backend folder
    $env:BACKEND_ROOT = Join-Path $ROOT 'backend'
    Log "Set BACKEND_ROOT=$env:BACKEND_ROOT"
    # Ensure required Python packages are installed for backend build
    $requirements = Join-Path (Join-Path $ROOT 'backend') 'requirements.txt'
    python -m pip install -q -r $requirements

    # Ensure PyInstaller is available
    if (-not (python -c "import PyInstaller" 2>$null)) { python -m pip install --quiet pyinstaller }
    python -m PyInstaller $specPath --noconfirm --distpath (Join-Path $ROOT 'backend\dist') --clean
    $backendExe = Join-Path $ROOT 'backend\dist\reqman_backend\reqman_backend.exe'
    if (Test-Path $backendExe) {
        Log "Backend exe built at $backendExe"
        # Ensure destination directory exists (distDesktop is defined later)
        if (-not (Test-Path $distDesktop)) { New-Item -ItemType Directory -Path $distDesktop | Out-Null }
        Copy-Item -Path $backendExe -Destination $distDesktop -Force
                Log "Copied backend exe to $distDesktop"
        # Replace Python backend with the compiled exe for the Tauri bundle
        $tauriBackendDir = Join-Path $TAURI 'backend'
        if (Test-Path $tauriBackendDir) { Remove-Item -Recurse -Force $tauriBackendDir }
        New-Item -ItemType Directory -Path $tauriBackendDir | Out-Null
        Copy-Item -Path $backendExe -Destination $tauriBackendDir -Force
        Log "Added compiled backend exe to Tauri resources"
    } else {
        Log "WARN: Backend exe not found after PyInstaller"
    }
} else {
    Log "WARN: PyInstaller spec not found at $specPath"
}

}

# Frontend build
$frontend = Join-Path $ROOT 'frontend'
if (-not (Test-Path (Join-Path $frontend 'node_modules'))) { Push-Location $frontend; npm install; Pop-Location }
Log "Building frontend assets"
Push-Location $frontend; npm run build; Pop-Location

Bundle-Backend

# Tauri desktop build
Log "Building desktop app (release)"
Push-Location $DESKTOP; npm run build; Pop-Location

Log "Build finished. Installers located under $DESKTOP\src-tauri\target\release\bundle"

# Copy installers to dist/desktop for easy distribution
$distDesktop = Join-Path (Join-Path $ROOT 'dist') 'desktop'
if (-not (Test-Path $distDesktop)) { New-Item -ItemType Directory -Path $distDesktop | Out-Null }
Copy-Item -Path (Join-Path (Join-Path (Join-Path (Join-Path (Join-Path $DESKTOP 'src-tauri') 'target') 'release') 'bundle') '*') -Destination $distDesktop -Recurse -Force
Log "Copied installers to $distDesktop"

