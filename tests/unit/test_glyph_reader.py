# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import cv2
import numpy as np
import pytest

from retrobard.adapters.ocr.glyph_reader import (
    GlyphFont,
    GlyphReader,
    fix_lookalikes,
    picture_box,
    text_mask,
    to_native,
)
from retrobard.adapters.ocr.teacher import teach_font
from retrobard.config import NormRect, OcrConfig

NATIVE_W, NATIVE_H = 256, 224

# A tiny made-up font: each letter is a few rows of pixels, drawn `dy` rows below the line top
SHAPES = {
    "A": (0, ["010", "101", "111", "101"]),
    "B": (0, ["110", "111", "101", "110"]),
    "C": (0, ["011", "100", "100", "011"]),
    "O": (0, ["111", "101", "101", "111"]),
    "1": (0, ["01", "11", "01", "01"]),
    ".": (3, ["1"]),
    "'": (0, ["1"]),
}
LINE_TOPS = (144, 160)


def _cfg() -> OcrConfig:
    return OcrConfig(
        region=NormRect(x=0.0, y=130 / NATIVE_H, w=1.0, h=80 / NATIVE_H),
        native_width=NATIVE_W,
        native_height=NATIVE_H,
    )


def _draw(lines: list[str], extra: dict[str, tuple[int, list[str]]] | None = None) -> np.ndarray:
    """Renders text lines into a native-size BGRA frame (1 window pixel = 1 native pixel)."""
    shapes = {**SHAPES, **(extra or {})}
    frame = np.zeros((NATIVE_H, NATIVE_W, 4), dtype=np.uint8)
    frame[..., :3] = 60  # dark dialogue background
    for top, text in zip(LINE_TOPS, lines):
        x = 12
        for char in text:
            if char == " ":
                x += 4
                continue
            dy, rows = shapes[char]
            for r, row in enumerate(rows):
                for c, bit in enumerate(row):
                    if bit == "1":
                        frame[top + dy + r, x + c, :3] = 255
            x += len(rows[0]) + 1
    return frame


def _taught_font(tmp_path: Path, frames: list[np.ndarray], answers: list[str]) -> GlyphFont:
    font = GlyphFont()
    replies = iter(answers)
    teach_font(_cfg(), font, tmp_path / "game.font.json", frames, ask=lambda _: next(replies), echo=lambda _: None)
    return font


def _teach_alphabet(tmp_path: Path) -> GlyphFont:
    # Shapes are asked most-frequent first, so teach one box per letter to keep the order known
    font = GlyphFont()
    path = tmp_path / "game.font.json"
    for char in SHAPES:
        frame = _draw([f"{char} {char}", f"{char}{char}"])
        teach_font(_cfg(), font, path, [frame], ask=lambda _, c=char: c, echo=lambda _: None)
    return font


class TestGeometry:
    def test_picture_is_centred_with_aspect_ratio_kept(self) -> None:
        x0, y0, sx, sy = picture_box(1415, 990, _cfg())
        assert sx == pytest.approx(990 / 224)
        assert sy == pytest.approx(990 / 224)
        assert x0 == pytest.approx((1415 - 256 * 990 / 224) / 2)
        assert y0 == pytest.approx(0.0)

    def test_native_pixels_survive_smoothed_non_integer_upscaling(self) -> None:
        native = _draw(["ABC", "CAB"])
        scale = 990 / 224
        picture = cv2.resize(native, (round(256 * scale), 990), interpolation=cv2.INTER_LINEAR)
        window = np.zeros((990, 1415, 4), dtype=np.uint8)
        left = (1415 - picture.shape[1]) // 2
        window[:, left : left + picture.shape[1]] = picture

        recovered = to_native(window, _cfg())

        assert np.array_equal(recovered >= 160, native[..., :3].max(axis=2) >= 160)

    def test_box_borders_are_not_treated_as_letters(self) -> None:
        gray = _draw(["AB"])[..., :3].max(axis=2)
        gray[132, 4:250] = 255  # top border line
        gray[132:205, 4] = 255  # left border line

        mask = text_mask(gray, 160)

        assert not mask[132].any()
        assert not mask[:, 4].any()
        assert mask[LINE_TOPS[0] : LINE_TOPS[0] + 4].any()


class TestReading:
    def test_reads_words_spaces_and_lines_exactly(self, tmp_path: Path) -> None:
        font = _teach_alphabet(tmp_path)
        result = GlyphReader(_cfg(), font).read(_draw(["ABC CAB", "BA.C"]))

        assert result.text == "ABC CAB\nBA.C"
        assert result.unknown == 0

    def test_same_shape_at_different_height_is_a_different_letter(self, tmp_path: Path) -> None:
        font = _teach_alphabet(tmp_path)
        assert GlyphReader(_cfg(), font).read(_draw(["A'B.", ""])).text == "A'B."

    def test_reading_follows_the_window_size(self, tmp_path: Path) -> None:
        font = _teach_alphabet(tmp_path)
        big = cv2.resize(_draw(["CAB", "ABC"]), (256 * 3, 224 * 3), interpolation=cv2.INTER_LINEAR)

        assert GlyphReader(_cfg(), font).read(big).text == "CAB\nABC"

    def test_unknown_letters_are_dropped_and_counted(self, tmp_path: Path) -> None:
        font = _teach_alphabet(tmp_path)
        frame = _draw(["ABCX ABC", ""], extra={"X": (0, ["101", "010", "010", "101"])})

        result = GlyphReader(_cfg(), font).read(frame)

        assert result.text == "ABC ABC"
        assert result.unknown == 1

    def test_text_that_is_mostly_not_the_game_font_is_rejected(self, tmp_path: Path) -> None:
        font = _teach_alphabet(tmp_path)
        frame = _draw(["XXXA", ""], extra={"X": (0, ["101", "010", "010", "101"])})

        assert GlyphReader(_cfg(), font).read(frame).text == ""

    def test_shapes_marked_ignore_are_never_read(self, tmp_path: Path) -> None:
        cursor = {">": (0, ["10", "11", "11", "10"])}
        font = _teach_alphabet(tmp_path)
        frame = _draw(["> AB", ""], extra=cursor)
        teach_font(_cfg(), font, tmp_path / "game.font.json", [frame], ask=lambda _: "!ignore", echo=lambda _: None)

        result = GlyphReader(_cfg(), font).read(frame)

        assert result.text == "AB"
        assert result.unknown == 0

    def test_a_game_scene_in_the_dialogue_area_is_not_read(self, tmp_path: Path) -> None:
        font = _teach_alphabet(tmp_path)
        frame = _draw(["ABC", "CAB"])
        # Sprite-like speckle: small bright bits on a tall unbroken stretch of rows
        for row in range(140, 190):
            frame[row, 100 + 4 * (row % 5), :3] = 255
        reader = GlyphReader(_cfg(), font)

        assert not reader.is_text(frame)
        assert reader.read(frame).text == ""

    def test_untaught_font_reads_nothing(self) -> None:
        assert GlyphReader(_cfg(), GlyphFont()).read(_draw(["ABC", ""])).text == ""


class TestTeaching:
    def test_skipped_and_quit_answers_leave_shapes_unnamed(self, tmp_path: Path) -> None:
        font = GlyphFont()
        answers = iter(["", "!quit"])
        taught, remaining = teach_font(
            _cfg(), font, tmp_path / "f.json", [_draw(["ABC", "CAB"])], ask=lambda _: next(answers), echo=lambda _: None
        )
        assert (taught, remaining) == (0, 3)

    def test_prompt_marks_the_letter_in_question(self, tmp_path: Path) -> None:
        shown: list[str] = []
        teach_font(
            _cfg(), GlyphFont(), tmp_path / "f.json", [_draw(["A", ""])], ask=lambda _: "A", echo=shown.append
        )
        art = next(text for text in shown if set(text) <= set("#: \n") and "#" in text)
        assert [line.strip() for line in art.splitlines()] == ["#", "# #", "###", "# #"]

    def test_font_file_roundtrip(self, tmp_path: Path) -> None:
        font = _teach_alphabet(tmp_path)
        path = tmp_path / "saved.font.json"
        font.save(path)
        loaded = GlyphFont.load(path)

        assert loaded.glyphs == font.glyphs
        assert (loaded.line_pitch, loaded.line_phase, loaded.space_gap) == (
            font.line_pitch,
            font.line_phase,
            font.space_gap,
        )
        assert loaded.is_usable


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1O Silver Points", "10 Silver Points"),
        ("for 5O G?", "for 50 G?"),
        ("1OO G", "100 G"),
        ("You have O Silver Points.", "You have 0 Silver Points."),
        ("GIRL: Ouch, that hurt!", "GIRL: Ouch, that hurt!"),
        ("MELCHIOR: GO!", "MELCHIOR: GO!"),
    ],
)
def test_capital_o_becomes_zero_only_inside_numbers(raw: str, expected: str) -> None:
    assert fix_lookalikes(raw, {"O": "0"}) == expected


class TestTeachingPlan:
    """The pieces a graphical interface uses instead of terminal prompts."""

    def test_unknown_shapes_are_listed_most_frequent_first(self) -> None:
        from retrobard.adapters.ocr.teacher import prepare_teaching

        font = GlyphFont()
        plan = prepare_teaching(_cfg(), font, [_draw(["AAB", "A"])])

        assert plan.frames_used == 1
        assert [shape.count for shape in plan.shapes] == [3, 1]
        assert font.line_phase is not None  # layout was worked out along the way

    def test_naming_a_shape_makes_it_readable(self) -> None:
        from retrobard.adapters.ocr.teacher import name_shape, prepare_teaching

        font = GlyphFont()
        frame = _draw(["AAB", "A"])
        plan = prepare_teaching(_cfg(), font, [frame])
        name_shape(font, plan.shapes[0].key, "A")
        name_shape(font, plan.shapes[1].key, None)  # not a letter

        assert GlyphReader(_cfg(), font).read(frame).text == "AA\nA"
        assert prepare_teaching(_cfg(), font, [frame]).shapes == []

    def test_shape_picture_marks_where_the_letter_is(self) -> None:
        from retrobard.adapters.ocr.teacher import prepare_teaching

        plan = prepare_teaching(_cfg(), GlyphFont(), [_draw(["AB", ""])])
        letter_b = plan.shapes[1]
        picture, (start, end) = letter_b.image()

        assert picture.dtype == np.uint8
        assert set(np.unique(picture)) == {0, 255}
        assert end - start == 3  # the letter is three pixels wide
        assert picture[:, start:end].any()

    def test_frames_without_text_are_counted_as_left_out(self) -> None:
        from retrobard.adapters.ocr.teacher import prepare_teaching

        scene = _draw(["ABC", "CAB"])
        for row in range(140, 190):
            scene[row, 100 + 4 * (row % 5), :3] = 255
        plan = prepare_teaching(_cfg(), GlyphFont(), [_draw(["AB", ""]), scene])

        assert (plan.frames_used, plan.frames_left_out) == (1, 1)
