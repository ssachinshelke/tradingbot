Param(
    [string]$Python = "python",
    [string]$Version = "v1"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Installing dependencies for hardened build..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt
& $Python -m pip install nuitka ordered-set zstandard

Write-Host "Building hardened Windows binary with Nuitka..."
& $Python -m nuitka `
  --onefile `
  --standalone `
  --assume-yes-for-downloads `
  --enable-plugin=multiprocessing `
  --output-dir=dist_nuitka `
  --output-filename=Tradingm5UI.exe `
  --include-data-dir=ui_backend/web=ui_backend/web `
  run_ui.py

$releaseDir = Join-Path $root "release"
if (!(Test-Path $releaseDir)) { New-Item -ItemType Directory -Path $releaseDir | Out-Null }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$bundleName = "Tradingm5UI_${Version}_windows_nuitka_${stamp}"
$bundleDir = Join-Path $releaseDir $bundleName
New-Item -ItemType Directory -Path $bundleDir | Out-Null

$exePath = Join-Path $root "dist_nuitka\Tradingm5UI.exe"
if (!(Test-Path $exePath)) { throw "Nuitka build output not found: $exePath" }
Copy-Item $exePath -Destination (Join-Path $bundleDir "Tradingm5UI.exe")
if (Test-Path "accounts.example.json") { Copy-Item "accounts.example.json" -Destination (Join-Path $bundleDir "accounts.example.json") }
if (Test-Path ".env.example") { Copy-Item ".env.example" -Destination (Join-Path $bundleDir ".env.example") }

$zipPath = Join-Path $releaseDir ($bundleName + ".zip")
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path (Join-Path $bundleDir "*") -DestinationPath $zipPath
Write-Host "Hardened release zip created:" $zipPath
