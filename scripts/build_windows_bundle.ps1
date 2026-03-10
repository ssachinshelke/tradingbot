Param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

Write-Host "Installing dependencies..."
& $Python -m pip install -r requirements.txt
& $Python -m pip install pyinstaller
Write-Host "Validating numpy/MetaTrader5 compatibility..."
& $Python -c "import numpy as n; import sys; v=n.__version__.split('.'); print('numpy='+n.__version__); sys.exit(0 if int(v[0]) < 2 else 1)"

Write-Host "Building Tradingm5UI executable..."
& $Python -m PyInstaller --clean --noconfirm "packaging/tradingm5_ui.spec"

Write-Host "Build complete: dist/Tradingm5UI.exe"
