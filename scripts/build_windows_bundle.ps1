Param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

Write-Host "Installing dependencies..."
& $Python -m pip install -r requirements.txt
& $Python -m pip install pyinstaller

Write-Host "Building Tradingm5UI executable..."
& $Python -m PyInstaller --clean --noconfirm "packaging/tradingm5_ui.spec"

Write-Host "Build complete: dist/Tradingm5UI.exe"
