# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads dialogue by matching each letter against the game's own bitmap font.

A retro game draws every letter with exactly the same pixels each time. So instead
of guessing with a general OCR model, the picture is scaled back to the console's
native resolution, split into letters, and each letter is looked up in a small
per-game font that was taught once (`retrobard teach`).
"""

import itertools
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from retrobard.config import OcrConfig

# Anything bigger than this (in native pixels) is a box border or sprite, not a letter
MAX_GLYPH_SIZE = 20
# Tallest stretch of rows one line of text may cover (ascenders + descenders)
MAX_LINE_HEIGHT = 14
DEFAULT_LINE_PITCH = 16
DEFAULT_SPACE_GAP = 3
# If more than this share of letters is unknown, the text is not in the game's font
MAX_UNKNOWN_RATIO = 0.3


# --------------------------------------------------------------------------- geometry


def picture_box(client_w: int, client_h: int, cfg: OcrConfig) -> tuple[float, float, float, float]:
    """Returns (x0, y0, scale_x, scale_y): where the game picture sits in the window
    and how many window pixels one native pixel covers."""
    if cfg.picture:
        x0, y0 = cfg.picture.x * client_w, cfg.picture.y * client_h
        return (
            x0,
            y0,
            cfg.picture.w * client_w / cfg.native_width,
            cfg.picture.h * client_h / cfg.native_height,
        )
    # Aspect ratio kept, picture centred (letterboxed/pillarboxed)
    scale = min(client_w / cfg.native_width, client_h / cfg.native_height)
    x0 = (client_w - cfg.native_width * scale) / 2.0
    y0 = (client_h - cfg.native_height * scale) / 2.0
    return x0, y0, scale, scale


def sample_native(
    frame: np.ndarray,
    cfg: OcrConfig,
    box: tuple[int, int, int, int] | None = None,
    shift: tuple[float, float] = (0.0, 0.0),
) -> np.ndarray:
    """Scales (part of) a window frame back to native resolution as a brightness image.

    Only the centre of each native pixel is sampled: that spot keeps its original
    colour even when the emulator smooths the picture, so the result is crisp.
    `box` limits the result to native pixels (x0, y0, x1, y1); `shift` nudges the
    sampling points by a fraction of a native pixel (see GridTuner).
    """
    client_h, client_w = frame.shape[:2]
    x0, y0, sx, sy = picture_box(client_w, client_h, cfg)
    nx0, ny0, nx1, ny1 = box or (0, 0, cfg.native_width, cfg.native_height)
    # A point at continuous coordinate c lies inside window pixel floor(c)
    xs = np.floor(x0 + (np.arange(nx0, nx1) + 0.5 + shift[0]) * sx).astype(int)
    ys = np.floor(y0 + (np.arange(ny0, ny1) + 0.5 + shift[1]) * sy).astype(int)
    xs = np.clip(xs, 0, client_w - 1)
    ys = np.clip(ys, 0, client_h - 1)
    sampled = frame[np.ix_(ys, xs)]
    return sampled[..., :3].max(axis=2) if sampled.ndim == 3 else sampled


def to_native(frame: np.ndarray, cfg: OcrConfig) -> np.ndarray:
    """The whole game picture at native resolution (brightness)."""
    return sample_native(frame, cfg)


class GridTuner:
    """Finds the sub-pixel position of the native pixel grid inside the window.

    The picture's place in the window is only known to within about a pixel, and
    with a smoothed picture that is enough to flip the dimmer pixels of a letter.
    Sampling exactly at pixel centres gives the highest contrast between
    neighbouring pixels, so the tuner tries small nudges on real text and keeps
    the sharpest one. It settles over the first few dialogue boxes per window size.
    """

    CANDIDATES = np.round(np.arange(-0.45, 0.451, 0.05), 2)

    def __init__(self, cfg: OcrConfig, max_samples: int = 5):
        self.cfg = cfg
        self.max_samples = max_samples
        self._state: dict[tuple[int, int], dict] = {}

    @staticmethod
    def _sharpness(native: np.ndarray) -> float:
        values = native.astype(np.float32)
        return float(
            np.abs(np.diff(values, axis=0)).mean() + np.abs(np.diff(values, axis=1)).mean()
        )

    def _best(self, scores: np.ndarray) -> float:
        # On a tie (e.g. an unsmoothed picture) prefer the smallest nudge
        top = np.flatnonzero(scores >= scores.max() - 1e-6)
        return float(self.CANDIDATES[top[np.argmin(np.abs(self.CANDIDATES[top]))]])

    def shift_for(self, frame: np.ndarray, box: tuple[int, int, int, int]) -> tuple[float, float]:
        size = (frame.shape[1], frame.shape[0])
        state = self._state.setdefault(
            size,
            {
                "n": 0,
                "x": np.zeros(len(self.CANDIDATES)),
                "y": np.zeros(len(self.CANDIDATES)),
                "shift": (0.0, 0.0),
            },
        )
        if state["n"] >= self.max_samples:
            return state["shift"]

        dx, dy = state["shift"]
        bright = float((sample_native(frame, self.cfg, box, (dx, dy)) >= self.cfg.glyph_threshold).mean())
        if not (0.003 < bright < 0.25):
            return state["shift"]  # no text to measure on

        state["y"] += [
            self._sharpness(sample_native(frame, self.cfg, box, (dx, c))) for c in self.CANDIDATES
        ]
        dy = self._best(state["y"])
        state["x"] += [
            self._sharpness(sample_native(frame, self.cfg, box, (c, dy))) for c in self.CANDIDATES
        ]
        dx = self._best(state["x"])
        state["n"] += 1
        state["shift"] = (dx, dy)
        return state["shift"]

    def prime(self, frames: list[np.ndarray], box_for) -> None:
        """Settles the grid position up front from a batch of frames."""
        for frame in frames[: self.max_samples]:
            self.shift_for(frame, box_for(frame))


def native_region(cfg: OcrConfig, client_w: int, client_h: int) -> tuple[int, int, int, int]:
    """The calibrated dialogue region as (x0, y0, x1, y1) in native pixels."""
    if cfg.region is None:
        raise ValueError("Profile has no ocr.region. Run `retrobard calibrate` first.")
    rx0, ry0, rx1, ry1 = cfg.region.to_pixels(client_w, client_h)
    x0, y0, sx, sy = picture_box(client_w, client_h, cfg)
    # Keep the native pixels whose centre lies inside the region
    nx0 = max(0, math.ceil((rx0 - x0) / sx - 0.5))
    ny0 = max(0, math.ceil((ry0 - y0) / sy - 0.5))
    nx1 = min(cfg.native_width, math.floor((rx1 - x0) / sx - 0.5) + 1)
    ny1 = min(cfg.native_height, math.floor((ry1 - y0) / sy - 0.5) + 1)
    return nx0, ny0, max(nx0 + 1, nx1), max(ny0 + 1, ny1)


def text_mask(native_gray: np.ndarray, threshold: int) -> np.ndarray:
    """Bright pixels that could be letters; long lines and big shapes are removed."""
    mask = (native_gray >= threshold).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for label in range(1, count):
        if (
            stats[label, cv2.CC_STAT_WIDTH] > MAX_GLYPH_SIZE
            or stats[label, cv2.CC_STAT_HEIGHT] > MAX_GLYPH_SIZE
        ):
            mask[labels == label] = 0
    return mask.astype(bool)


def looks_like_text(mask: np.ndarray) -> bool:
    """True if the bright pixels form a few separate, letter-high rows.

    Dialogue is rows of small shapes with empty rows in between. A game scene or a
    menu with sprites in the same area has bright pixels on tall, unbroken stretches
    of rows, and must not be read (or offered for teaching) as letters.
    """
    rows = np.where(mask.any(axis=1))[0]
    if len(rows) == 0:
        return False
    runs = np.split(rows, np.where(np.diff(rows) > 1)[0] + 1)
    return all(len(run) <= MAX_LINE_HEIGHT for run in runs)


# ------------------------------------------------------------------------------- font


def glyph_key(dy: int, rows: list[str]) -> str:
    return f"{dy}:{'/'.join(rows)}"


@dataclass
class GlyphFont:
    """The taught letters of one game, plus how its text lines are laid out."""

    line_pitch: int = DEFAULT_LINE_PITCH
    # Native picture row (modulo line_pitch) where a text line's cell starts.
    # None until the first teaching session has seen some text.
    line_phase: int | None = None
    # A horizontal gap of at least this many native pixels is a space
    space_gap: int = DEFAULT_SPACE_GAP
    # Letters drawn identically to a digit, e.g. capital O and zero
    lookalikes: dict[str, str] = field(default_factory=lambda: {"O": "0"})
    # glyph key -> character(s); an empty string marks a shape to ignore (cursor, icon)
    glyphs: dict[str, str] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        return self.line_phase is not None and any(self.glyphs.values())

    @classmethod
    def load(cls, path: Path) -> "GlyphFont":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        font = cls(
            line_pitch=int(data.get("line_pitch", DEFAULT_LINE_PITCH)),
            line_phase=data.get("line_phase"),
            space_gap=int(data.get("space_gap", DEFAULT_SPACE_GAP)),
            lookalikes=dict(data.get("lookalikes", {"O": "0"})),
        )
        for entry in data.get("glyphs", []):
            font.glyphs[glyph_key(int(entry["dy"]), list(entry["rows"]))] = entry["char"]
        return font

    @classmethod
    def layered(cls, shipped: "GlyphFont | None", own: "GlyphFont") -> "GlyphFont":
        """The user's own letters on top of the letters shipped with the app."""
        if shipped is None:
            return own
        # The text layout comes from whichever font has seen text, preferring the user's
        layout = own if own.line_phase is not None else shipped
        return cls(
            line_pitch=layout.line_pitch,
            line_phase=layout.line_phase,
            space_gap=layout.space_gap,
            lookalikes={**shipped.lookalikes, **own.lookalikes},
            glyphs={**shipped.glyphs, **own.glyphs},
        )

    def without(self, shipped: "GlyphFont | None") -> "GlyphFont":
        """Only what differs from `shipped`: the letters the user taught themselves."""
        if shipped is None:
            return self
        return GlyphFont(
            line_pitch=self.line_pitch,
            line_phase=self.line_phase,
            space_gap=self.space_gap,
            lookalikes=dict(self.lookalikes),
            glyphs={k: v for k, v in self.glyphs.items() if shipped.glyphs.get(k) != v},
        )

    def save(self, path: Path, extra: dict | None = None) -> None:
        """Writes the font as JSON. `extra` adds fields that loading ignores, such as
        who exported it."""
        entries = []
        for key, char in sorted(self.glyphs.items(), key=lambda item: (item[1], item[0])):
            dy, rows = key.split(":", 1)
            entries.append({"char": char, "dy": int(dy), "rows": rows.split("/")})
        data = {
            "line_pitch": self.line_pitch,
            "line_phase": self.line_phase,
            "space_gap": self.space_gap,
            "lookalikes": self.lookalikes,
            **(extra or {}),
            "glyphs": entries,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)


# ----------------------------------------------------------------------- segmentation


@dataclass
class Segment:
    key: str
    x0: int
    x1: int
    gap: int | None  # native pixels since the previous segment; None for the first


@dataclass
class TextLine:
    band: np.ndarray  # boolean mask of this line's rows, full region width
    segments: list[Segment]


def segment_lines(mask: np.ndarray, region_top: int, font: GlyphFont) -> list[TextLine]:
    """Splits a text mask into lines (fixed cells) and letters (column gaps).

    `region_top` is the native picture row of the mask's first row, so that line
    cells stay anchored to the game picture rather than to the calibrated region.
    """
    if font.line_phase is None:
        return []
    pitch = font.line_pitch
    n_rows = mask.shape[0]
    # First cell start (relative to the mask) at or before row 0
    start = -((region_top - font.line_phase) % pitch)

    lines: list[TextLine] = []
    for cell in range(start, n_rows, pitch):
        a, b = max(cell, 0), min(cell + pitch, n_rows)
        band = mask[a:b]
        cols = np.where(band.any(axis=0))[0]
        if len(cols) == 0:
            continue
        segments: list[Segment] = []
        prev_end: int | None = None
        for run in np.split(cols, np.where(np.diff(cols) > 1)[0] + 1):
            x0, x1 = int(run[0]), int(run[-1]) + 1
            glyph = band[:, x0:x1]
            used = np.where(glyph.any(axis=1))[0]
            r0, r1 = int(used[0]), int(used[-1]) + 1
            rows = ["".join("1" if v else "0" for v in row) for row in glyph[r0:r1]]
            dy = (a + r0) - cell
            segments.append(
                Segment(
                    key=glyph_key(dy, rows),
                    x0=x0,
                    x1=x1,
                    gap=None if prev_end is None else x0 - prev_end,
                )
            )
            prev_end = x1
        lines.append(TextLine(band=band, segments=segments))
    return lines


def learn_layout(font: GlyphFont, samples: list[tuple[np.ndarray, int]]) -> None:
    """Works out line pitch and where line cells start, from (mask, region_top) samples."""
    tops_diffs: Counter[int] = Counter()
    used_rows: set[int] = set()
    for mask, region_top in samples:
        rows = np.where(mask.any(axis=1))[0]
        if len(rows) == 0:
            continue
        used_rows.update(int(r) + region_top for r in rows)
        runs = np.split(rows, np.where(np.diff(rows) > 1)[0] + 1)
        tops = [int(r[0]) for r in runs]
        for a, b in itertools.pairwise(tops):
            if 8 <= b - a <= 24:
                tops_diffs[b - a] += 1
    if not used_rows:
        return

    pitch = tops_diffs.most_common(1)[0][0] if tops_diffs else DEFAULT_LINE_PITCH
    used = np.zeros(pitch, dtype=bool)
    for row in used_rows:
        used[row % pitch] = True

    # Put the cell boundary in the middle of the widest run of always-empty rows
    best_len, best_start = 0, 0
    for start in range(pitch):
        length = 0
        while length < pitch and not used[(start + length) % pitch]:
            length += 1
        if length > best_len:
            best_len, best_start = length, start
    font.line_pitch = pitch
    font.line_phase = (best_start + best_len // 2) % pitch


def learn_space_gap(font: GlyphFont, lines: list[TextLine]) -> None:
    """Letters in a word sit close together; a clearly wider gap is a space."""
    gaps = sorted({s.gap for line in lines for s in line.segments if s.gap is not None and s.gap <= 12})
    if len(gaps) < 2:
        return
    jumps = [(b - a, a, b) for a, b in itertools.pairwise(gaps)]
    _, lo, hi = max(jumps)
    font.space_gap = math.ceil((lo + hi) / 2)


# ---------------------------------------------------------------------------- reading


def fix_lookalikes(text: str, lookalikes: dict[str, str]) -> str:
    """Turns e.g. 'O' into '0' where it is clearly part of a number."""
    for letter, digit in lookalikes.items():
        esc = re.escape(letter)
        previous = None
        while previous != text:
            previous = text
            text = re.sub(rf"(?<=\d){esc}", digit, text)
            text = re.sub(rf"{esc}(?=\d)", digit, text)
        # A lone lookalike standing as its own word ("You have O points")
        text = re.sub(rf"(?<![A-Za-z0-9']){esc}(?![A-Za-z0-9'])", digit, text)
    return text


@dataclass(frozen=True)
class ReadResult:
    text: str
    known: int
    unknown: int


class GlyphReader:
    def __init__(self, cfg: OcrConfig, font: GlyphFont):
        self.cfg = cfg
        self.font = font
        self.tuner = GridTuner(cfg)

    def _box(self, window_frame: np.ndarray) -> tuple[int, int, int, int]:
        client_h, client_w = window_frame.shape[:2]
        return native_region(self.cfg, client_w, client_h)

    def prime(self, frames: list[np.ndarray]) -> None:
        """Settles the pixel grid from a batch of frames before reading them."""
        self.tuner.prime(frames, self._box)

    def region_mask(self, window_frame: np.ndarray) -> tuple[np.ndarray, int]:
        """Returns (text mask of the dialogue region, native row of its first line)."""
        box = self._box(window_frame)
        shift = self.tuner.shift_for(window_frame, box)
        native = sample_native(window_frame, self.cfg, box, shift)
        return text_mask(native, self.cfg.glyph_threshold), box[1]

    def is_text(self, window_frame: np.ndarray) -> bool:
        """Whether the dialogue region currently shows lines of text at all."""
        return looks_like_text(self.region_mask(window_frame)[0])

    def lines(self, window_frame: np.ndarray) -> list[TextLine]:
        mask, region_top = self.region_mask(window_frame)
        if not looks_like_text(mask):
            return []
        return segment_lines(mask, region_top, self.font)

    def read(self, window_frame: np.ndarray) -> ReadResult:
        known = unknown = 0
        out_lines: list[str] = []
        for line in self.lines(window_frame):
            text = ""
            space_pending = False
            for seg in line.segments:
                if seg.gap is not None and seg.gap >= self.font.space_gap:
                    space_pending = True
                char = self.font.glyphs.get(seg.key)
                if char is None:
                    unknown += 1
                    continue
                if char == "":
                    continue  # taught as "not a letter"
                known += 1
                if text and space_pending:
                    text += " "
                space_pending = False
                text += char
            if text:
                out_lines.append(fix_lookalikes(text, self.font.lookalikes))

        total = known + unknown
        if total == 0 or (unknown / total) > MAX_UNKNOWN_RATIO:
            return ReadResult(text="", known=known, unknown=unknown)
        return ReadResult(text="\n".join(out_lines), known=known, unknown=unknown)
