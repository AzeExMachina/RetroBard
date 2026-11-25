# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prints one version's section of CHANGELOG.md, for the GitHub release notes.

    python packaging/release_notes.py 0.2.3

Exits with an error when CHANGELOG.md has no section for that version, so a
release cannot go out without its notes.
"""

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
HEADING_RE = re.compile(r"^## \[(?P<version>[^\]]+)\]", re.MULTILINE)
LINK_RE = re.compile(r"^\[[^\]]+\]: \S+\s*$", re.MULTILINE)


def versions(text: str) -> list[str]:
    """The section names in order, e.g. ["Unreleased", "0.2.2", "0.2.1"]."""
    return [match["version"] for match in HEADING_RE.finditer(text)]


def section(text: str, version: str) -> str | None:
    """The body of one version's section (without its heading), or None."""
    headings = list(HEADING_RE.finditer(text))
    for index, match in enumerate(headings):
        if match["version"] != version:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = LINK_RE.sub("", text[match.end():end].split("\n", 1)[-1])
        return body.strip()
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: release_notes.py <version>", file=sys.stderr)
        return 2
    version = argv[0].removeprefix("v")
    notes = section(CHANGELOG.read_text(encoding="utf-8"), version)
    if not notes:
        print(f"CHANGELOG.md has no notes for {version}. Add a '## [{version}]' section.",
              file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
