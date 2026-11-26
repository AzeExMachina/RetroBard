# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import cv2
import numpy as np
import pytest

from retrobard.adapters.ocr.glyph_reader import (
    GlyphFont,
    GlyphReader,
    find_picture,
    fix_lookalikes,
    fix_shared_i_and_l,
    picture_box,
    text_mask,
    to_native,
)
from retrobard.adapters.ocr.teacher import (
    collect_dialogue_frames,
    prepare_teaching,
    teach_font,
)
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


def _bordered(native: np.ndarray, zoom: int, size: tuple[int, int]) -> np.ndarray:
    """The native frame drawn at a whole-number zoom, centred in a black window."""
    width, height = size
    picture = cv2.resize(native, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    window = np.zeros((height, width, 4), dtype=np.uint8)
    top, left = (height - picture.shape[0]) // 2, (width - picture.shape[1]) // 2
    window[top : top + picture.shape[0], left : left + picture.shape[1]] = picture
    return window


def _see_through(frame: np.ndarray, phase: int = 0) -> np.ndarray:
    """A dithered see-through box: every other pixel is bright scenery (pale
    green, lighter than the box but not white), touching the white letters.
    `phase` animates the scenery, like water moving behind the box."""
    frame = frame.copy()
    rows, cols = np.indices(frame.shape[:2])
    scenery = ((rows + cols) % 2 == 0) & (frame[..., 0] < 255)
    colours = np.array([(165, 182, 148), (140, 215, 231), (156, 255, 255)], dtype=np.uint8)
    frame[scenery, :3] = colours[(rows[scenery] // 3 + cols[scenery] // 5 + phase) % 3]
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

    def test_picture_drawn_at_whole_zoom_inside_a_black_border_is_found(self, tmp_path: Path) -> None:
        font = _teach_alphabet(tmp_path)
        window = _bordered(_draw(["ABC CAB", "BA.C"]), zoom=4, size=(1415, 1010))

        assert find_picture(window, _cfg()) == ((1415 - 1024) // 2, (1010 - 896) // 2, 4.0, 4.0)
        assert GlyphReader(_cfg(), font).read(window).text == "ABC CAB\nBA.C"

    def test_picture_is_found_when_its_bottom_edge_is_dark(self, tmp_path: Path) -> None:
        font = _teach_alphabet(tmp_path)
        native = _draw(["ABC CAB", "BA.C"])
        native[200:, :, :3] = 0  # a black cave floor along the bottom of the scene
        window = _bordered(native, zoom=4, size=(1415, 991))

        assert find_picture(window, _cfg()) == ((1415 - 1024) // 2, (991 - 896) // 2, 4.0, 4.0)
        assert GlyphReader(_cfg(), font).read(window).text == "ABC CAB\nBA.C"

    def test_picture_is_found_when_dark_on_two_sides(self, tmp_path: Path) -> None:
        font = _teach_alphabet(tmp_path)
        native = _draw(["ABC CAB", "BA.C"])
        native[:, 220:, :3] = 0  # the room ends before the right edge
        native[200:, :, :3] = 0  # and the floor below is black
        window = _bordered(native, zoom=4, size=(1415, 991))

        assert find_picture(window, _cfg()) == ((1415 - 1024) // 2, (991 - 896) // 2, 4.0, 4.0)
        assert GlyphReader(_cfg(), font).read(window).text == "ABC CAB" + chr(10) + "BA.C"

    def test_a_lone_box_on_a_black_screen_is_not_taken_for_the_picture(self) -> None:
        window = np.zeros((991, 1415, 4), dtype=np.uint8)
        window[400:600, 300:1115, :3] = 90  # centred left to right, edges far from the picture's

        assert find_picture(window, _cfg()) is None

    def test_a_dark_scene_does_not_move_the_picture(self) -> None:
        window = np.zeros((1010, 1415, 4), dtype=np.uint8)
        window[600:700, 900:1300, :3] = 200  # something lit on one side only

        assert find_picture(window, _cfg()) is None

    def test_a_picture_filling_the_window_keeps_the_usual_position(self) -> None:
        # 256x224 fitted into 1415x990 is 1131 pixels wide, with 142-pixel bars
        window = np.full((990, 1415, 4), 60, dtype=np.uint8)
        window[:, :142] = 0
        window[:, 1415 - 142 :] = 0

        assert find_picture(window, _cfg()) == picture_box(1415, 990, _cfg())

    def test_a_wrong_guess_from_a_dark_frame_is_corrected(self) -> None:
        from retrobard.adapters.ocr.glyph_reader import PictureLocator

        window = np.full((990, 1415, 4), 60, dtype=np.uint8)
        window[:, :142] = 0
        window[:, 1415 - 142 :] = 0
        locator = PictureLocator(_cfg())
        locator._found[(1415, 990)] = (195.0, 47.0, 4.0, 4.0)  # an odd earlier guess

        assert locator.locate(window) == picture_box(1415, 990, _cfg())

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


class TestSeeThroughBox:
    def test_letters_over_scenery_are_found_only_as_white_pixels(self) -> None:
        frame = _see_through(_draw(["ABC CAB", "BA.C"]))
        white = _cfg().model_copy(update={"text_color": "white"})

        assert not GlyphReader(_cfg(), GlyphFont()).is_text(frame)
        assert GlyphReader(white, GlyphFont()).is_text(frame)

    def test_collecting_switches_to_white_letters_before_any_is_taught(self) -> None:
        frame = _see_through(_draw(["ABC CAB", "BA.C"]))
        cfg = _cfg()

        frames = collect_dialogue_frames(
            _StillSource(frame), cfg, 1, echo=lambda _: None, choose_text_color=True
        )

        assert len(frames) == 1
        assert cfg.text_color == "white"

    def test_collecting_keeps_the_colour_once_letters_are_taught(self) -> None:
        frame = _see_through(_draw(["ABC CAB", "BA.C"]))
        cfg = _cfg()
        polls = iter(range(30))

        frames = collect_dialogue_frames(
            _StillSource(frame),
            cfg,
            1,
            echo=lambda _: None,
            should_stop=lambda: next(polls, None) is None,
        )

        assert frames == []
        assert cfg.text_color == "bright"


    def test_scenery_moving_behind_the_box_does_not_stop_collecting(self) -> None:
        native = _draw(["ABC CAB", "BA.C"])
        cfg = _cfg()

        frames = collect_dialogue_frames(
            _AnimatedSource([_see_through(native, phase) for phase in range(3)]),
            cfg,
            1,
            echo=lambda _: None,
            choose_text_color=True,
        )

        assert len(frames) == 1
        assert cfg.text_color == "white"

    def test_pale_scenery_specks_are_not_letters(self) -> None:
        frame = _see_through(_draw(["ABC CAB", "BA.C"]))
        white = _cfg().model_copy(update={"text_color": "white"})
        font = GlyphFont()

        plan = prepare_teaching(white, font, [frame])

        assert font.line_pitch == LINE_TOPS[1] - LINE_TOPS[0]
        assert len(plan.shapes) == 4  # A, B, C and the full stop, no scenery


    def test_reading_aloud_works_while_scenery_moves_behind_the_box(self, tmp_path: Path) -> None:
        import time

        from retrobard.adapters.producers.ocr_watcher import OcrWatcher

        white = _cfg().model_copy(update={"text_color": "white", "cursor_mask_ratio": 0.0})
        font = GlyphFont()
        for char in SHAPES:
            frame = _draw([f"{char} {char}", f"{char}{char}"])
            teach_font(white, font, tmp_path / "f.json", [frame], ask=lambda _, c=char: c, echo=lambda _: None)
        native = _draw(["ABC CAB", "BA.C"])
        source = _AnimatedSource([_see_through(native, phase) for phase in range(3)])
        events = []
        watcher = OcrWatcher(white, source, glyph_reader=GlyphReader(white, font), status=lambda *a, **k: None)

        watcher.start(events.append)
        deadline = time.monotonic() + 5
        while not events and time.monotonic() < deadline:
            time.sleep(0.05)
        watcher.stop()

        assert [e.raw_text for e in events][:1] == ["ABC CAB\nBA.C"]


class _AnimatedSource:
    def __init__(self, frames: list[np.ndarray]):
        self.frames = frames
        self.n = 0

    def grab(self) -> np.ndarray:
        self.n += 1
        return self.frames[self.n % len(self.frames)]


class _StillSource:
    def __init__(self, frame: np.ndarray):
        self.frame = frame

    def grab(self) -> np.ndarray:
        return self.frame


class TestSharedIAndL:
    @pytest.mark.parametrize(
        "read, expected",
        [
            ("I'II heIp you!", "I'll help you!"),
            ("The viIIage is this way", "The village is this way"),
            ("RANDI:Hey!", "RANDI:Hey!"),
            ("It is I.", "It is I."),
            # A word starting with l, mid-sentence and after a wrapped line
            ("Read the Ietter.", "Read the letter."),
            ("Watch out for the\nIittle ones!", "Watch out for the\nlittle ones!"),
            # At the start of a sentence it is a capital I
            ("Stop! It is here. If you\nIook, run.", "Stop! It is here. If you\nlook, run."),
            ("MAN:It's Iate.", "MAN:It's late."),
        ],
    )
    def test_one_shape_taught_as_capital_i(self, read: str, expected: str) -> None:
        assert fix_shared_i_and_l(read, {"I", "e", "h", "p"}) == expected

    @pytest.mark.parametrize(
        "read, expected",
        [
            ("l'm here", "I'm here"),
            ("RANDl:Hey!", "RANDI:Hey!"),
            ("all well", "all well"),
            ("lt is late. lf you look", "It is late. If you look"),
        ],
    )
    def test_one_shape_taught_as_lowercase_l(self, read: str, expected: str) -> None:
        assert fix_shared_i_and_l(read, {"l", "e", "h"}) == expected

    def test_fonts_with_both_shapes_are_left_alone(self) -> None:
        assert fix_shared_i_and_l("McIntyre", {"I", "l"}) == "McIntyre"
