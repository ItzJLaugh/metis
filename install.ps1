# Metis installer for Windows. Run install.bat (double-click) or:  powershell -ExecutionPolicy Bypass -File install.ps1
# Creates .venv next to this file, installs Metis, downloads the two models, and adds a "Metis" desktop shortcut.
# Nothing is installed system-wide; delete this folder (and %USERPROFILE%\.metis) to remove Metis.
param(
    [switch]$NoShortcut,  # skip the desktop shortcut
    [switch]$NoModels,    # skip downloading the models now (they download on first use instead)
    [switch]$NoGpu        # don't offer the NVIDIA GPU version of PyTorch
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`n$text" -ForegroundColor Red; exit 1 }

# 1. Find Python 3.10 or newer: the "py" launcher first, then "python".
Step "Looking for Python 3.10 or newer"
$Python = $null
foreach ($candidate in @(@("py", "-3"), @("python"))) {
    $exe = $candidate[0]
    $args_ = @($candidate | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try { $version = & $exe @args_ -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { continue }
    if ($version -and [version]$version -ge [version]"3.10") { $Python = $candidate; break }
}
if (-not $Python) {
    Fail ("Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ " +
          "(tick 'Add python.exe to PATH'), or run:  winget install Python.Python.3.13`nThen run install.bat again.")
}
Write-Host "Using Python $version"

# 2. A private environment for Metis in .venv
$Venv = Join-Path $Root ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Step "Creating the Metis environment (.venv)"
    & $Python[0] @($Python | Select-Object -Skip 1) -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create .venv." }
}

# 3. Packages
Step "Installing Metis's packages (a few minutes the first time)"
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r (Join-Path $Root "connector\requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "Installing packages failed. Check your internet connection and run install.bat again." }

# 4. Optional: NVIDIA GPU build of PyTorch (much faster lookups)
if (-not $NoGpu -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $answer = Read-Host "`nAn NVIDIA GPU was found. Install the GPU version of PyTorch for faster lookups? (about 2.5 GB) [y/N]"
    if ($answer -match '^(y|yes)$') {
        Step "Installing PyTorch for NVIDIA GPUs"
        & $VenvPython -m pip install "torch==2.14.1+cu130" --index-url https://download.pytorch.org/whl/cu130
        if ($LASTEXITCODE -ne 0) { Write-Host "GPU install failed; Metis will use the CPU." -ForegroundColor Yellow }
    }
}

# 5. Download the models now, so the first question isn't slow
if (-not $NoModels) {
    Step "Downloading the models (about 1.5 GB, once)"
    $env:PYTHONPATH = Join-Path $Root "connector"
    $env:LAYA_DEVICE = "cpu"
    & $VenvPython -c "from metis_mcp import core; core.embedder(); core.agent(); print('Models ready.')"
    Remove-Item Env:LAYA_DEVICE
    if ($LASTEXITCODE -ne 0) { Write-Host "Model download failed; they will download the first time you use Metis." -ForegroundColor Yellow }
}

# 6. Desktop shortcut to the app
if (-not $NoShortcut) {
    Step "Adding a Metis shortcut to your desktop"
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Metis.lnk"))
    $link.TargetPath = Join-Path $Root "start-metis.bat"
    $link.WorkingDirectory = $Root
    $link.WindowStyle = 7  # start minimized: the .bat window closes as soon as the app opens
    $link.Description = "Metis: connect Claude to your folders"
    $link.Save()
}

Write-Host "`nMetis is installed." -ForegroundColor Green
Write-Host "Open it from the 'Metis' shortcut on your desktop (or start-metis.bat), add a folder, then click Connect."
