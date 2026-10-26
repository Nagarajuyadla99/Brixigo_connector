# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller — Windows service binary (onedir = single OS process)."""

from pathlib import Path

block_cipher = None
root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "run_service.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "win32timezone",
        "servicemanager",
        "win32serviceutil",
        "win32service",
        "win32event",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BrixigoConnectorService",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BrixigoConnectorService",
)
