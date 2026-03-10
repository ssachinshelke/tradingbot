Param(
    [string]$Python = "python",
    [string]$Version = "v1"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Installing build dependencies..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt
& $Python -m pip install pyinstaller
Write-Host "Validating numpy/MetaTrader5 compatibility..."
& $Python -c "import numpy as n; import sys; v=n.__version__.split('.'); print('numpy='+n.__version__); sys.exit(0 if int(v[0]) < 2 else 1)"

Write-Host "Building executable..."
& $Python -m PyInstaller --clean --noconfirm "packaging/tradingm5_ui.spec"

$releaseDir = Join-Path $root "release"
if (!(Test-Path $releaseDir)) {
    New-Item -ItemType Directory -Path $releaseDir | Out-Null
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$bundleName = "Tradingm5UI_${Version}_${stamp}"
$bundleDir = Join-Path $releaseDir $bundleName
New-Item -ItemType Directory -Path $bundleDir | Out-Null

$exePath = Join-Path $root "dist\Tradingm5UI.exe"
if (!(Test-Path $exePath)) {
    throw "Build finished but executable not found at: $exePath"
}
Copy-Item $exePath -Destination (Join-Path $bundleDir "Tradingm5UI.exe")

$notesPath = Join-Path $bundleDir "README_RELEASE.txt"
@"
Tradingm5UI Release Bundle
=========================

1) Run Tradingm5UI.exe
2) Add accounts in UI -> Accounts tab
3) Verify Healthcheck in UI
4) Place orders from Trading tab

No source code is shipped in this bundle.

Logs:
- Runtime logs: .\logs\ui_backend_*.log
- Closed deals journal: .\logs\closed_deals_journal.jsonl
"@ | Set-Content -Path $notesPath -Encoding UTF8

$zipPath = Join-Path $releaseDir ($bundleName + ".zip")
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}
Compress-Archive -Path (Join-Path $bundleDir "*") -DestinationPath $zipPath

Write-Host "Release zip created:" $zipPath
