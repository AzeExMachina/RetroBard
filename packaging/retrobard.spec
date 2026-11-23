# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
# PyInstaller recipe for the RetroBard tester build. Run it through packaging/build.py.
# -*- mode: python -*-
from pathlib import Path

HERE = Path(SPECPATH)
ROOT = HERE.parent

# Shipped game profiles and letters; at runtime they are read from <app>/_internal/profiles
datas = [
    (str(path), "profiles")
    for pattern in ("*.yaml", "*.font.json")
    for path in sorted((ROOT / "profiles").glob(pattern))
]

a = Analysis(
    [str(HERE / "launch_gui.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    # keyring finds its Windows backend through entry points, which PyInstaller cannot see
    hiddenimports=["keyring.backends.Windows"],
    excludes=[
        # Optional or development-only; easyocr alone would add about 1 GB
        "easyocr", "torch", "torchvision", "pyttsx3", "pytest", "ruff",
        "tkinter", "matplotlib", "IPython",
        # Qt modules RetroBard never uses; Virtual Keyboard is GPLv3-only for open source
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtVirtualKeyboard",
    ],
    noarchive=False,
)

# Qt plugins and DLLs that PyInstaller's hooks still collect, plus OpenCV's FFmpeg
# video DLL (the app never reads or writes video)
DROPPED = (
    "qt6virtualkeyboard", "qtvirtualkeyboard", "platforminputcontexts",
    "qt6qml", "qt6quick", "qt6pdf", "imageformats/qpdf", "imageformats\\qpdf",
    "opencv_videoio_ffmpeg",
)


def _keep(entry) -> bool:
    destination = entry[0].lower()
    return not any(part in destination for part in DROPPED)


a.binaries = [entry for entry in a.binaries if _keep(entry)]
a.datas = [entry for entry in a.datas if _keep(entry)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RetroBard",
    console=False,  # a window app: output goes to %APPDATA%\RetroBard\logs
    icon=str(HERE / "build" / "retrobard.ico"),
    version=str(HERE / "build" / "version_info.txt"),  # written by build.py
    upx=False,  # compressed executables are flagged by antivirus more often
)
coll = COLLECT(exe, a.binaries, a.datas, name="RetroBard", upx=False)
