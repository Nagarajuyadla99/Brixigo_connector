# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller — tray UI binary (onedir = single OS process, no bootloader child)."""

from pathlib import Path

block_cipher = None
root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "resources"), "resources")],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtNetwork",
        "win32timezone",
        "servicemanager",
        "win32serviceutil",
        "win32service",
        "win32event",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir layout: bootloader does NOT spawn a second BrixigoConnector.exe
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BrixigoConnector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BrixigoConnector",
)
