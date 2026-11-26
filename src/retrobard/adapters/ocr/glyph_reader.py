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


# (x0, y0, scale_x, scale_y): where the game picture sits in the window and how
# many window pixels one native pixel covers
Geometry = tuple[float, float, float, float]

# Window pixels at or below this brightness count as the black border around the picture
BORDER_LEVEL = 16
# With text_color "white", letters must be nearly pure white: every colour channel
# at least this bright. Scenery showing through a see-through box is never that white.
WHITE_THRESHOLD = 224


def pixel_brightness(pixels: np.ndarray, cfg: OcrConfig) -> np.ndarray:
    """How bright each pixel is for finding letters (see OcrConfig.text_color)."""
    if pixels.ndim == 2:
        return pixels
    rgb = pixels[..., :3]
    return rgb.min(axis=2) if cfg.text_color == "white" else rgb.max(axis=2)


def text_threshold(cfg: OcrConfig) -> int:
    """Brightness (from pixel_brightness) at or above which a pixel is a letter pixel."""
    if cfg.text_color == "white":
        return max(cfg.glyph_threshold, WHITE_THRESHOLD)
    return cfg.glyph_threshold


def letter_pixels(pixels: np.ndarray, cfg: OcrConfig) -> np.ndarray:
    """Which pixels could belong to letters (boolean)."""
    return pixel_brightness(pixels, cfg) >= text_threshold(cfg)


def _centred(first: int, end: int, size: int) -> bool:
    return abs(first - (size - end)) <= max(2, 0.01 * size)


def _snap(scale: float, native: int) -> float:
    """A dark first or last row of the picture can hide its edge by a pixel or two:
    snap to a whole-number zoom when that close."""
    rounded = round(scale)
    return float(rounded) if rounded >= 1 and abs(scale - rounded) * native <= 2 else scale


def _hidden_edge_start(lit_first: int, lit_end: int, size: int, length: float) -> float | None:
    """Start of the picture along an axis where a dark scene may hide an edge.

    The picture is centred and `length` window pixels long: everything lit along
    this axis must fit inside it, and at least one of its two edges must be seen."""
    start = (size - length) / 2.0
    if lit_first < start - 2 or lit_end > start + length + 2:
        return None
    if abs(lit_first - start) <= 2:
        return float(lit_first)
    if abs(lit_end - (start + length)) <= 2:
        return float(lit_end - length)
    return None


def _pixel_shapes(native_w: int, native_h: int) -> tuple[float, float]:
    """Width over height of one native pixel: square, or the 4:3 stretch some
    emulators offer."""
    return 1.0, (4 / 3) * native_h / native_w


def _whole_zoom_picture(
    lit: tuple[int, int, int, int], w: int, h: int, nw: int, nh: int
) -> Geometry | None:
    """The picture at a whole-number zoom, when a dark scene hides edges on two sides.

    An emulator drawing at a whole-number zoom, centred, has only a few places to
    put the picture in a window of a given size. One is taken when everything lit
    (x0, x1, y0, y1) fits inside it, and at least one of its left/right edges and
    one of its top/bottom edges are seen where they should be."""
    x0, x1, y0, y1 = lit
    for shape in _pixel_shapes(nw, nh):
        for zoom in range(1, h // nh + 1):
            # With the 4:3 stretch the height keeps the whole-number zoom
            sx, sy = zoom * shape, float(zoom)
            bw, bh = sx * nw, sy * nh
            bx0, by0 = (w - bw) / 2.0, (h - bh) / 2.0
            if bx0 < -1 or by0 < -1:
                continue
            if x0 < bx0 - 2 or x1 > bx0 + bw + 2 or y0 < by0 - 2 or y1 > by0 + bh + 2:
                continue
            left, right = abs(x0 - bx0) <= 2, abs(x1 - (bx0 + bw)) <= 2
            top, bottom = abs(y0 - by0) <= 2, abs(y1 - (by0 + bh)) <= 2
            if (left or right) and (top or bottom):
                # The edges that were seen give the exact position
                px = float(x0) if left else float(x1 - bw)
                py = float(y0) if top else float(y1 - bh)
                return px, py, sx, sy
    return None


def find_picture(frame: np.ndarray, cfg: OcrConfig) -> Geometry | None:
    """Where the game picture sits, found from the black border around it.

    Emulators often draw the picture at a whole-number zoom with a black border,
    rather than filling the window. A dark scene can hide one pair of the
    picture's edges (a black cave floor along the bottom); the other pair then
    gives its size. With edges hidden on two sides (a room ending at the right
    above a black floor), a whole-number zoom that fits the edges still seen is
    used. A picture filling the window gives the usual position (picture_box).
    Returns None when the frame does not tell (a dark scene, or no border at all)."""
    lit = (frame[..., :3].max(axis=2) if frame.ndim == 3 else frame) > BORDER_LEVEL
    # A small icon in the border (the emulator's pause sign) is not the picture
    cols = np.flatnonzero(lit.mean(axis=0) > 0.02)
    rows = np.flatnonzero(lit.mean(axis=1) > 0.02)
    if len(cols) == 0 or len(rows) == 0:
        return None
    h, w = frame.shape[:2]
    nw, nh = cfg.native_width, cfg.native_height
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    # Emulators centre the picture; a dark scene lit on one side only is off-centre
    across, down = _centred(x0, x1, w), _centred(y0, y1, h)
    found: Geometry | None = None
    if across and down:
        found = float(x0), float(y0), _snap((x1 - x0) / nw, nw), _snap((y1 - y0) / nh, nh)
    elif across or down:
        for shape in _pixel_shapes(nw, nh):
            if across:
                px, sx = float(x0), _snap((x1 - x0) / nw, nw)
                sy = sx / shape
                py = _hidden_edge_start(y0, y1, h, sy * nh)
            else:
                py, sy = float(y0), _snap((y1 - y0) / nh, nh)
                sx = sy * shape
                px = _hidden_edge_start(x0, x1, w, sx * nw)
            if px is not None and py is not None:
                found = px, py, sx, sy
                break
    if found is None:
        # Dark on two sides (a room that ends at the right, a black floor below)
        found = _whole_zoom_picture((x0, x1, y0, y1), w, h, nw, nh)
    if found is None:
        return None
    px, py, sx, sy = found
    # Square pixels, or the 4:3 stretch
    if sx < 1 or sy < 1 or not 0.97 <= sx / sy <= 1.2:
        return None
    # Where the usual guess (the picture filling the window) is right, keep it
    # exactly. It is returned rather than None so that PictureLocator, which keeps
    # the largest position seen, replaces any odd guess from an earlier dark frame.
    fit = picture_box(w, h, cfg)
    fx0, fy0, fsx, fsy = fit
    if (
        abs(px - fx0) <= 2
        and abs(py - fy0) <= 2
        and abs(sx - fsx) * nw <= 4
        and abs(sy - fsy) * nh <= 4
    ):
        return fit
    return px, py, sx, sy


def picture_box(
    client_w: int, client_h: int, cfg: OcrConfig, found: Geometry | None = None
) -> Geometry:
    """Returns (x0, y0, scale_x, scale_y): where the game picture sits in the window
    and how many window pixels one native pixel covers. `found` is a position
    seen in the frames themselves (see PictureLocator)."""
    if cfg.picture:
        x0, y0 = cfg.picture.x * client_w, cfg.picture.y * client_h
        return (
            x0,
            y0,
            cfg.picture.w * client_w / cfg.native_width,
            cfg.picture.h * client_h / cfg.native_height,
        )
    if found:
        return found
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
    geometry: Geometry | None = None,
) -> np.ndarray:
    """Scales (part of) a window frame back to native resolution as a brightness image.

    Only the centre of each native pixel is sampled: that spot keeps its original
    colour even when the emulator smooths the picture, so the result is crisp.
    `box` limits the result to native pixels (x0, y0, x1, y1); `shift` nudges the
    sampling points by a fraction of a native pixel (see GridTuner); `geometry`
    is where the picture sits (from PictureLocator), if known.
    """
    client_h, client_w = frame.shape[:2]
    x0, y0, sx, sy = geometry or picture_box(client_w, client_h, cfg)
    nx0, ny0, nx1, ny1 = box or (0, 0, cfg.native_width, cfg.native_height)
    # A point at continuous coordinate c lies inside window pixel floor(c)
    xs = np.floor(x0 + (np.arange(nx0, nx1) + 0.5 + shift[0]) * sx).astype(int)
    ys = np.floor(y0 + (np.arange(ny0, ny1) + 0.5 + shift[1]) * sy).astype(int)
    xs = np.clip(xs, 0, client_w - 1)
    ys = np.clip(ys, 0, client_h - 1)
    return pixel_brightness(frame[np.ix_(ys, xs)], cfg)


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

    def shift_for(
        self,
        frame: np.ndarray,
        box: tuple[int, int, int, int],
        geometry: Geometry | None = None,
    ) -> tuple[float, float]:
        # A nudge only holds for one window size and picture position
        size = (frame.shape[1], frame.shape[0], geometry)
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
        def sample(shift: tuple[float, float]) -> np.ndarray:
            return sample_native(frame, self.cfg, box, shift, geometry)

        bright = float((sample((dx, dy)) >= text_threshold(self.cfg)).mean())
        if not (0.003 < bright < 0.25):
            return state["shift"]  # no text to measure on

        state["y"] += [
            self._sharpness(sample((dx, c))) for c in self.CANDIDATES
        ]
        dy = self._best(state["y"])
        state["x"] += [
            self._sharpness(sample((c, dy))) for c in self.CANDIDATES
        ]
        dx = self._best(state["x"])
        state["n"] += 1
        state["shift"] = (dx, dy)
        return state["shift"]

    def prime(self, frames: list[np.ndarray], place_for) -> None:
        """Settles the grid position up front from a batch of frames.
        `place_for(frame)` returns the frame's (box, geometry)."""
        for frame in frames[: self.max_samples]:
            self.shift_for(frame, *place_for(frame))


class PictureLocator:
    """Remembers where the game picture sits in the window, per window size.

    Each frame with a clear black border around the picture gives its position
    (find_picture). The largest position seen wins, since a dark scene can only
    make the picture look smaller than it is. Without a border it falls back to
    the usual guess (picture_box)."""

    def __init__(self, cfg: OcrConfig):
        self.cfg = cfg
        self._found: dict[tuple[int, int], Geometry] = {}

    def locate(self, frame: np.ndarray) -> Geometry:
        client_h, client_w = frame.shape[:2]
        best = None
        if self.cfg.picture is None:
            best = self._found.get((client_w, client_h))
            found = find_picture(frame, self.cfg)
            if found and (best is None or found[2] * found[3] > best[2] * best[3]):
                self._found[(client_w, client_h)] = best = found
        return picture_box(client_w, client_h, self.cfg, best)


def native_region(
    cfg: OcrConfig, client_w: int, client_h: int, geometry: Geometry | None = None
) -> tuple[int, int, int, int]:
    """The calibrated dialogue region as (x0, y0, x1, y1) in native pixels."""
    if cfg.region is None:
        raise ValueError("Profile has no ocr.region. Run `retrobard calibrate` first.")
    rx0, ry0, rx1, ry1 = cfg.region.to_pixels(client_w, client_h)
    x0, y0, sx, sy = geometry or picture_box(client_w, client_h, cfg)
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


def _starts_sentence(before: list[str]) -> bool:
    """True if what follows `before` begins a sentence: nothing but spaces, line
    breaks and quotes since the start, a . ! or ?, or the speaker's colon.
    A line break on its own is not enough, since boxes wrap mid-sentence."""
    for char in reversed(before):
        if char.isspace() or char in "\"'":
            continue
        return char in ".!?:"
    return True


def fix_shared_i_and_l(text: str, taught: set[str]) -> str:
    """Some games draw capital I and lowercase l with the same pixels (Secret of
    Mana), so that one shape was taught as just one of them. This picks the
    letter the context calls for.

    A capital I begins a word only at the start of a sentence or in a name; a
    lowercase l never begins a sentence (that would be a capital L). So, for a
    shape taught as "I": "viIIage", "heIp", "I'II" and "a Ietter" become
    "village", "help", "I'll" and "a letter", while "It is I." stays. For a
    shape taught as "l": "l'm", "RANDl" and "lt is" become "I'm", "RANDI" and
    "It is". `text` is a whole box, line breaks included."""
    out: list[str] = []
    if "I" in taught and "l" not in taught:
        for i, char in enumerate(text):
            if char == "I":
                prev = out[-1] if out else ""
                nxt = text[i + 1] if i + 1 < len(text) else ""
                after_apostrophe = prev == "'" and len(out) > 1 and out[-2].isalpha()
                word_start = not prev.isalpha() and not after_apostrophe
                if prev.islower() or after_apostrophe or word_start and nxt.islower() and not _starts_sentence(out):
                    char = "l"
            out.append(char)
        return "".join(out)
    if "l" in taught and "I" not in taught:
        for i, char in enumerate(text):
            if char == "l":
                prev = out[-1] if out else ""
                nxt = text[i + 1] if i + 1 < len(text) else ""
                if not nxt.islower() and (not prev.isalpha() or prev.isupper()) or not prev.isalpha() and nxt.islower() and _starts_sentence(out):
                    char = "I"
            out.append(char)
        return "".join(out)
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
        self.locator = PictureLocator(cfg)

    def _place(self, window_frame: np.ndarray) -> tuple[tuple[int, int, int, int], Geometry]:
        """The dialogue region in native pixels, and where the picture sits."""
        client_h, client_w = window_frame.shape[:2]
        geometry = self.locator.locate(window_frame)
        return native_region(self.cfg, client_w, client_h, geometry), geometry

    def prime(self, frames: list[np.ndarray]) -> None:
        """Settles the picture position and pixel grid from a batch of frames
        before reading them."""
        for frame in frames:
            self.locator.locate(frame)
        self.tuner.prime(frames, self._place)

    def region_mask(self, window_frame: np.ndarray) -> tuple[np.ndarray, int]:
        """Returns (text mask of the dialogue region, native row of its first line)."""
        box, geometry = self._place(window_frame)
        shift = self.tuner.shift_for(window_frame, box, geometry)
        native = sample_native(window_frame, self.cfg, box, shift, geometry)
        return text_mask(native, text_threshold(self.cfg)), box[1]

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
        taught = set(self.font.glyphs.values())
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
        # Needs the whole box: a line break is not the start of a sentence
        text = fix_shared_i_and_l("\n".join(out_lines), taught)
        return ReadResult(text=text, known=known, unknown=unknown)
