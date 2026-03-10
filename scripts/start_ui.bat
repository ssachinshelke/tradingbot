@echo off
setlocal

if exist "dist\Tradingm5UI.exe" (
  start "" "dist\Tradingm5UI.exe"
) else (
  echo dist\Tradingm5UI.exe not found. Starting from source...
  python run_ui.py
)

endlocal
