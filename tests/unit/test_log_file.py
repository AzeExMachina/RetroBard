# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""The packaged app's log file never contains the user's home folder."""

import io
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from retrobard.gui.main import HomeRedactingWriter


def _write(text: str, **kwargs) -> str:
    raw = io.StringIO()
    writer = HomeRedactingWriter(raw, **kwargs)
    writer.write(text)
    writer.flush()
    return raw.getvalue()


def test_windows_home_becomes_tilde(monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(r"C:\Users\Someone")))
    text = r"Saved C:\Users\Someone\AppData\Roaming\RetroBard\profiles\ct.yaml" + "\n"

    assert _write(text, windows=True) == r"Saved ~\AppData\Roaming\RetroBard\profiles\ct.yaml" + "\n"


def test_windows_matching_ignores_case_and_slashes() -> None:
    home = r"C:\Users\Someone"
    out = _write("c:/users/SOMEONE/AppData and C:\\Users\\Someone", home=home, windows=True)
    assert out == "~/AppData and ~"


def test_other_users_and_prefixes_are_left_alone() -> None:
    home = r"C:\Users\Someone"
    assert _write(r"C:\Users\SomeoneElse\x", home=home, windows=True) == r"C:\Users\SomeoneElse\x"


def test_posix_home() -> None:
    assert _write("/home/ann/.cache/x", home="/home/ann", windows=False) == "~/.cache/x"
