# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""packaging/build.py: what may never end up in a tester build."""

import importlib.util
from pathlib import Path

import pytest

BUILD_SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "build.py"


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("retrobard_build_script", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestForbiddenFiles:
    def test_roms_and_secrets_are_found(self, build, tmp_path: Path) -> None:
        (tmp_path / "cache").mkdir()
        for name in ("game.sfc", ".env", "cache/udp_token", "RetroBard.exe", "notes.md"):
            (tmp_path / name).write_bytes(b"x")

        found = build.find_forbidden_files(tmp_path)

        assert sorted(p.relative_to(tmp_path).as_posix() for p in found) == [
            ".env",
            "cache/udp_token",
            "game.sfc",
        ]

    def test_cache_files_are_found(self, build, tmp_path: Path) -> None:
        for name in ("index.sqlite3", "line.pcm", "game.font.json.tmp", "GAME.SMC"):
            (tmp_path / name).write_bytes(b"x")
        assert len(build.find_forbidden_files(tmp_path)) == 4

    def test_clean_folder(self, build, tmp_path: Path) -> None:
        (tmp_path / "_internal").mkdir()
        (tmp_path / "_internal" / "base_library.zip").write_bytes(b"x")
        (tmp_path / "RetroBard-0.2.1-windows.zip").write_bytes(b"x")
        (tmp_path / "README.txt").write_text("hi", encoding="utf-8")
        assert build.find_forbidden_files(tmp_path) == []


def test_third_party_notices(build, tmp_path: Path) -> None:
    notices = build.write_third_party_notices(tmp_path)

    text = notices.read_text(encoding="utf-8").lower()
    for name in ("pyside6", "opencv-python", "pygame", "numpy", "certifi"):
        assert f"\n{name} " in text, name
    assert "lgpl-3.0" in text
    assert (tmp_path / "licenses" / "Qt" / "LGPL-3.0.txt").exists()
    qt_source = (tmp_path / "licenses" / "Qt" / "QT-SOURCE.txt").read_text(encoding="utf-8")
    assert "{qt_version}" not in qt_source and "download.qt.io" in qt_source
    assert (tmp_path / "licenses" / "opencv-python" / "LICENSE-3RD-PARTY.txt").exists()


def test_version_info_names_the_author(build, tmp_path: Path) -> None:
    from retrobard import about

    path = build.write_version_info(tmp_path / "version_info.txt", "0.2.1")

    text = path.read_text(encoding="utf-8")
    assert f"© {about.COPYRIGHT_YEAR} {about.AUTHOR}" in text
    assert "'0.2.1.0'" in text and "(0, 2, 1, 0)" in text
    assert "'RetroBard.exe'" in text


def test_version_info_is_readable_by_pyinstaller(build, tmp_path: Path) -> None:
    versioninfo = pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    path = build.write_version_info(tmp_path / "version_info.txt", "0.2.1")
    info = versioninfo.load_version_info_from_text_file(str(path))
    assert "RetroBard" in str(info)
