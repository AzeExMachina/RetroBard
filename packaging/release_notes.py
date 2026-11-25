# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prints one version's section of CHANGELOG.md, for the GitHub release notes.

    python packaging/release_notes.py 0.2.3

Exits with an error when CHANGELOG.md has no section for that version, so a
release cannot go out without its notes.
"""

import sys
from pathlib import Path

from retrobard.changelog import section, versions

__all__ = ["main", "section", "versions"]

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


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
