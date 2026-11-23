# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Teaches RetroBard a game's letters: collect dialogue boxes, then name each new shape once.

The work is split so that any front end can drive it:

- `collect_dialogue_frames` gathers frames while the user plays,
- `prepare_teaching` finds the letter shapes the font does not know yet,
- `name_shape` records the user's answer for one shape.

`teach_font` strings those together for the terminal.
"""

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from retrobard.adapters.ocr.glyph_reader import (
    GlyphFont,
    GlyphReader,
    TextLine,
    learn_layout,
    learn_space_gap,
)
from retrobard.config import OcrConfig
from retrobard.domain.ports import FrameSource

IGNORE_COMMAND = "!ignore"
QUIT_COMMAND = "!quit"
CONTEXT_COLUMNS = 36


def collect_dialogue_frames(
    source: FrameSource,
    cfg: OcrConfig,
    max_boxes: int,
    echo: Callable[[str], None] = print,
    should_stop: Callable[[], bool] | None = None,
) -> list[np.ndarray]:
    """Grabs one frame of the game window per settled dialogue box until `max_boxes`
    are collected, `should_stop()` returns True, or the user presses Ctrl+C."""
    probe = GlyphReader(cfg, GlyphFont())
    frames: list[np.ndarray] = []
    prev: np.ndarray | None = None
    last_saved: np.ndarray | None = None
    stable_since: float | None = None
    try:
        while len(frames) < max_boxes and not (should_stop and should_stop()):
            frame = source.grab()
            if frame is None:
                time.sleep(0.5)
                continue
            crop = cfg.region.crop(frame)[..., :3].astype(np.int16)
            if prev is not None and prev.shape == crop.shape and np.abs(crop - prev).mean() < 0.4:
                stable_since = stable_since or time.monotonic()
            else:
                stable_since = None
            prev = crop

            if stable_since and (time.monotonic() - stable_since) > 0.45:
                bright = float((crop.max(axis=2) >= cfg.glyph_threshold).mean())
                is_new = (
                    last_saved is None
                    or last_saved.shape != crop.shape
                    or np.abs(crop - last_saved).mean() > 1.5
                )
                if 0.003 < bright < 0.2 and is_new and probe.is_text(frame):
                    frames.append(frame)
                    last_saved = crop
                    echo(f"  collected box {len(frames)}/{max_boxes}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    return frames


@dataclass
class UnknownShape:
    """A letter shape the font cannot name yet, with one place it was seen."""

    key: str
    count: int
    line: TextLine
    x0: int
    x1: int

    def render(self) -> str:
        """The surrounding text as characters: this shape as '#', its neighbours
        as ':', so it can be shown in any terminal."""
        band = self.line.band
        left = max(0, self.x0 - CONTEXT_COLUMNS)
        right = min(band.shape[1], self.x1 + CONTEXT_COLUMNS)
        used = np.where(band[:, left:right].any(axis=1))[0]
        rows = range(int(used[0]), int(used[-1]) + 1) if len(used) else range(band.shape[0])
        out = []
        for r in rows:
            chars = []
            for c in range(left, right):
                if not band[r, c]:
                    chars.append(" ")
                else:
                    chars.append("#" if self.x0 <= c < self.x1 else ":")
            out.append("    " + "".join(chars).rstrip())
        return "\n".join(out)

    def image(self, context_columns: int = CONTEXT_COLUMNS) -> tuple[np.ndarray, tuple[int, int]]:
        """The surrounding text as a small black-and-white picture (rows x columns,
        255 = letter pixel), plus the (start, end) columns of this shape inside it,
        for a graphical interface to draw and highlight."""
        band = self.line.band
        left = max(0, self.x0 - context_columns)
        right = min(band.shape[1], self.x1 + context_columns)
        picture = band[:, left:right].astype(np.uint8) * 255
        return picture, (self.x0 - left, self.x1 - left)


@dataclass
class TeachingPlan:
    frames_used: int
    frames_left_out: int  # frames that did not show lines of text
    shapes: list[UnknownShape]  # most frequently seen first


def prepare_teaching(cfg: OcrConfig, font: GlyphFont, frames: list[np.ndarray]) -> TeachingPlan:
    """Finds every letter shape in `frames` the font does not know yet.

    Also works out the text layout (first time only) and the width of a space,
    which updates `font` in memory; saving is up to the caller.
    """
    reader = GlyphReader(cfg, font)
    reader.prime(frames)

    # Game scenes or menus caught in the dialogue area are not letters: leave them out
    text_frames = [frame for frame in frames if reader.is_text(frame)]
    left_out = len(frames) - len(text_frames)

    if font.line_phase is None:
        learn_layout(font, [reader.region_mask(frame) for frame in text_frames])
        if font.line_phase is None:
            return TeachingPlan(frames_used=0, frames_left_out=left_out, shapes=[])

    all_lines = [line for frame in text_frames for line in reader.lines(frame)]
    learn_space_gap(font, all_lines)

    counts: Counter[str] = Counter()
    example: dict[str, tuple[TextLine, int, int]] = {}
    for line in all_lines:
        for seg in line.segments:
            if seg.key not in font.glyphs:
                counts[seg.key] += 1
                example.setdefault(seg.key, (line, seg.x0, seg.x1))

    shapes = [
        UnknownShape(key=key, count=count, line=example[key][0], x0=example[key][1], x1=example[key][2])
        for key, count in counts.most_common()
    ]
    return TeachingPlan(frames_used=len(text_frames), frames_left_out=left_out, shapes=shapes)


def name_shape(font: GlyphFont, key: str, characters: str | None) -> None:
    """Records what a shape is. `characters=None` marks it as not a letter
    (cursor, icon), so it is never read."""
    font.glyphs[key] = "" if characters is None else characters


def teach_font(
    cfg: OcrConfig,
    font: GlyphFont,
    font_path: Path,
    frames: list[np.ndarray],
    ask: Callable[[str], str] = input,
    echo: Callable[[str], None] = print,
) -> tuple[int, int]:
    """Terminal version: asks the user to name every unknown letter shape in `frames`.
    Returns (newly taught, still unknown)."""
    plan = prepare_teaching(cfg, font, frames)
    if plan.frames_left_out:
        echo(f"Left out {plan.frames_left_out} frame(s) that did not show lines of text.")
    if font.line_phase is None:
        echo("No text was found in the dialogue region, so there is nothing to teach.")
        return 0, 0

    font.save(font_path)
    if not plan.shapes:
        return 0, 0

    echo(
        f"\n{len(plan.shapes)} letter shape(s) to name. For each one, type the character drawn "
        f"with # and press Enter.\n"
        f"  Enter alone = skip for now\n"
        f"  {IGNORE_COMMAND} = not a letter (cursor, icon), never read it\n"
        f"  {QUIT_COMMAND} = save and stop\n"
    )

    taught = 0
    for number, shape in enumerate(plan.shapes, start=1):
        echo(f"\nShape {number} of {len(plan.shapes)} (seen {shape.count} time(s)):\n")
        echo(shape.render())
        try:
            answer = ask("\n  character> ").strip()
        except EOFError:
            break
        if answer == QUIT_COMMAND:
            break
        if answer == "":
            continue
        name_shape(font, shape.key, None if answer == IGNORE_COMMAND else answer)
        font.save(font_path)
        taught += 1

    remaining = sum(1 for shape in plan.shapes if shape.key not in font.glyphs)
    return taught, remaining
