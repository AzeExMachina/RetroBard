# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Where RetroBard keeps its files.

Run from the source folder, everything stays next to the project as before
(./profiles, ./.retrobard_cache, .env). Run as the packaged app, the files shipped
with it are read-only, and everything the user creates goes to %APPDATA%\\RetroBard.
"""

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running as the packaged app (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Read-only files shipped with the app (default profiles and letters)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def appdata_dir() -> Path:
    """%APPDATA% (the user's home folder when it is not set)."""
    return Path(os.environ.get("APPDATA") or str(Path.home()))


def user_data_dir() -> Path:
    """The user's own files: profiles, taught letters, cache, settings and logs."""
    if not is_frozen():
        return Path(".")
    return appdata_dir() / "RetroBard"


def install_default_profiles(bundled: Path | None, profiles_dir: Path) -> list[str]:
    """Copies the shipped game profiles the user does not have yet.

    Only the .yaml profiles are copied: the shipped letters stay in the app and the
    user's own letters are stored on top of them, so a new build can bring new
    letters without touching what the user taught. Returns the games copied."""
    if bundled is None or not bundled.is_dir() or bundled.resolve() == profiles_dir.resolve():
        return []
    copied = []
    profiles_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(bundled.glob("*.yaml")):
        target = profiles_dir / source.name
        if not target.exists():
            shutil.copyfile(source, target)
            copied.append(source.stem)
    return copied
