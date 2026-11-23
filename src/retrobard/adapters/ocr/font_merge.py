# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Combining letters taught by testers into the font shipped with the app.

A shape is only added when everyone who taught it agrees on the character. When
testers disagree, or a tester disagrees with the shipped font, the shape is left
out and reported so the developer can decide.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from retrobard.adapters.ocr.glyph_reader import GlyphFont

SHIPPED_SOURCE = "your font"


@dataclass
class Contribution:
    name: str
    font: GlyphFont


@dataclass
class MergeResult:
    merged: GlyphFont
    # glyph key -> character, for shapes that were new and everyone agreed on
    added: dict[str, str] = field(default_factory=dict)
    already_known: int = 0
    # glyph key -> {character: [who said so]}; not added to `merged`
    conflicts: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def describe_char(char: str) -> str:
    return "(not a letter)" if char == "" else repr(char)


def render_shape(key: str) -> str:
    """The shape drawn with # for set pixels, one line per row."""
    _dy, rows = key.split(":", 1)
    return "\n".join(row.replace("1", "#").replace("0", ".") for row in rows.split("/"))


def merge_letters(shipped: GlyphFont, contributions: list[Contribution]) -> MergeResult:
    merged = GlyphFont(
        line_pitch=shipped.line_pitch,
        line_phase=shipped.line_phase,
        space_gap=shipped.space_gap,
        lookalikes=dict(shipped.lookalikes),
        glyphs=dict(shipped.glyphs),
    )
    result = MergeResult(merged=merged)

    votes: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for contribution in contributions:
        font = contribution.font
        if (
            shipped.line_phase is not None
            and font.line_phase is not None
            and font.line_pitch != shipped.line_pitch
        ):
            result.warnings.append(
                f"{contribution.name}: text lines are {font.line_pitch} pixels apart instead "
                f"of {shipped.line_pitch}. The emulator may be stretching or filtering the picture."
            )
        if merged.line_phase is None and font.line_phase is not None:
            merged.line_pitch = font.line_pitch
            merged.line_phase = font.line_phase
            merged.space_gap = font.space_gap
        for key, char in font.glyphs.items():
            votes[key][char].append(contribution.name)

    for key, by_char in votes.items():
        if key in shipped.glyphs:
            known = shipped.glyphs[key]
            if set(by_char) == {known}:
                result.already_known += 1
            else:
                result.conflicts[key] = {
                    known: [SHIPPED_SOURCE, *by_char.get(known, [])],
                    **{char: names for char, names in by_char.items() if char != known},
                }
        elif len(by_char) == 1:
            char = next(iter(by_char))
            merged.glyphs[key] = char
            result.added[key] = char
        else:
            result.conflicts[key] = dict(by_char)

    return result
