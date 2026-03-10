Param(
    [string]$Python = "python",
    [string]$Version = "v1"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

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

Copy-Item "dist\Tradingm5UI.exe" -Destination (Join-Path $bundleDir "Tradingm5UI.exe")
if (Test-Path "scripts\start_ui.bat") {
    Copy-Item "scripts\start_ui.bat" -Destination (Join-Path $bundleDir "start_ui.bat")
}
if (Test-Path "accounts.example.json") {
    Copy-Item "accounts.example.json" -Destination (Join-Path $bundleDir "accounts.example.json")
}
if (Test-Path ".env.example") {
    Copy-Item ".env.example" -Destination (Join-Path $bundleDir ".env.example")
}

$notesPath = Join-Path $bundleDir "README_RELEASE.txt"
@"
Tradingm5UI Release Bundle
=========================

1) Run Tradingm5UI.exe (or start_ui.bat)
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
