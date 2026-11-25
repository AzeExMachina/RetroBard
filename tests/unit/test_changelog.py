# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""CHANGELOG.md and packaging/release_notes.py, which turns it into release notes."""

import importlib.util
from pathlib import Path

import pytest

from retrobard import __version__

ROOT = Path(__file__).resolve().parents[2]
NOTES_SCRIPT = ROOT / "packaging" / "release_notes.py"

SAMPLE = """# Changelog

Intro text.

## [Unreleased]

### Added
- Something new.

## [1.1.0] - 2026-12-01

### Fixed
- A bug.

## [1.0.0] - 2026-11-01

First version.

[Unreleased]: https://example.com/compare/v1.1.0...HEAD
[1.1.0]: https://example.com/compare/v1.0.0...v1.1.0
[1.0.0]: https://example.com/tag/v1.0.0
"""


@pytest.fixture(scope="module")
def notes():
    spec = importlib.util.spec_from_file_location("retrobard_release_notes", NOTES_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lists_the_sections(notes) -> None:
    assert notes.versions(SAMPLE) == ["Unreleased", "1.1.0", "1.0.0"]


def test_takes_one_section_without_its_heading(notes) -> None:
    assert notes.section(SAMPLE, "1.1.0") == "### Fixed\n- A bug."


def test_last_section_drops_the_link_list(notes) -> None:
    assert notes.section(SAMPLE, "1.0.0") == "First version."


def test_missing_version(notes) -> None:
    assert notes.section(SAMPLE, "2.0.0") is None


def test_main_fails_without_notes(notes, capsys) -> None:
    assert notes.main(["v99.0.0"]) == 1
    assert "no notes for 99.0.0" in capsys.readouterr().err


def test_changelog_has_the_current_version(notes) -> None:
    """Bumping the version needs its CHANGELOG.md section, or the release would fail."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert notes.versions(text)[0] == "Unreleased"
    assert notes.section(text, __version__), f"CHANGELOG.md has no [{__version__}] section"
