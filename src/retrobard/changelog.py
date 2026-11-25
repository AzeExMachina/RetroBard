# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads CHANGELOG.md: for the release notes on GitHub and the "What's new" window.

The changelog follows Keep a Changelog: one "## [version] - date" heading per
release, newest first, an "Unreleased" section at the top, and link references at
the bottom.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from retrobard.paths import is_frozen

FILE_NAME = "CHANGELOG.md"
UNRELEASED = "Unreleased"
HEADING_RE = re.compile(r"^## \[(?P<version>[^\]]+)\](?:\s*-\s*(?P<date>\S+))?", re.MULTILINE)
LINK_RE = re.compile(r"^\[[^\]]+\]: \S+\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Entry:
    version: str
    date: str
    notes: str


def changelog_path() -> Path:
    """CHANGELOG.md next to RetroBard.exe, or at the project root when run from source."""
    if is_frozen():
        return Path(sys.executable).parent / FILE_NAME
    return Path(__file__).resolve().parents[2] / FILE_NAME


def entries(text: str) -> list[Entry]:
    """Every section in order, with its body (without heading or link references)."""
    headings = list(HEADING_RE.finditer(text))
    result = []
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = LINK_RE.sub("", text[match.end():end].split("\n", 1)[-1])
        result.append(Entry(match["version"], match["date"] or "", body.strip()))
    return result


def versions(text: str) -> list[str]:
    """The section names in order, e.g. ["Unreleased", "0.2.2", "0.2.1"]."""
    return [entry.version for entry in entries(text)]


def section(text: str, version: str) -> str | None:
    """The body of one version's section (without its heading), or None."""
    for entry in entries(text):
        if entry.version == version:
            return entry.notes
    return None


def released(text: str) -> list[Entry]:
    """The released versions, newest first (no Unreleased section)."""
    return [entry for entry in entries(text) if entry.version != UNRELEASED]


def as_markdown(items: list[Entry], current: str = "") -> str:
    """The entries as one Markdown page, with the running version marked."""
    parts = []
    for entry in items:
        title = entry.version
        if entry.date:
            title += f" ({entry.date})"
        if entry.version == current:
            title += " - this version"
        parts.append(f"## {title}\n\n{entry.notes or 'No notes.'}")
    return "\n\n".join(parts)
