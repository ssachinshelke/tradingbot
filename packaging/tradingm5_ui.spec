# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

# In PyInstaller spec execution, __file__ may be unset on some CI runners.
# Use cwd from build scripts and absolute script path so PyInstaller does not
# try to resolve run_ui.py under packaging/.
project_root = Path.cwd().resolve()
entry_script = str(project_root / "run_ui.py")
web_dir = project_root / "ui_backend" / "web"

datas = [
    (str(web_dir), "ui_backend/web"),
]

hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.websockets",
    "fastapi",
    "starlette",
]


a = Analysis(
    [entry_script],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Tradingm5UI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
)
