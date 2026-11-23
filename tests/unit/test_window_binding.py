# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import numpy as np
import pytest

from retrobard.adapters.capture.win32_window import (
    AmbiguousMatch,
    WindowInfo,
    pick_window,
)
from retrobard.app.setup import binding_for, needs_title_filter
from retrobard.config import GameProfile, NormRect, WindowBinding
from retrobard.domain.models import AudioEffect


class TestNormRect:
    def test_crop_follows_window_size(self) -> None:
        rect = NormRect(x=0.25, y=0.5, w=0.5, h=0.25)

        small = np.zeros((200, 400, 4), dtype=np.uint8)
        large = np.zeros((800, 1600, 4), dtype=np.uint8)

        assert rect.to_pixels(400, 200) == (100, 100, 300, 150)
        assert rect.crop(small).shape == (50, 200, 4)
        assert rect.crop(large).shape == (200, 800, 4)

    def test_from_pixels_roundtrip(self) -> None:
        rect = NormRect.from_pixels(64, 300, 512, 120, frame_w=640, frame_h=480)
        assert rect.to_pixels(640, 480) == (64, 300, 576, 420)

    def test_crop_never_leaves_the_frame(self) -> None:
        rect = NormRect(x=0.9, y=0.9, w=1.0, h=1.0)
        frame = np.zeros((100, 100, 4), dtype=np.uint8)
        assert rect.crop(frame).shape == (10, 10, 4)


class TestWindowBinding:
    def test_matches_only_the_bound_executable(self) -> None:
        binding = WindowBinding(process_name="snes9x-x64.exe")
        assert binding.matches("Snes9x-x64.exe", "Chrono Trigger - Snes9x 1.63")
        assert not binding.matches("chrome.exe", "Chrono Trigger - Snes9x 1.63")

    def test_optional_title_filter(self) -> None:
        binding = WindowBinding(process_name="retroarch.exe", title_contains="Chrono")
        assert binding.matches("retroarch.exe", "RetroArch - chrono trigger")
        assert not binding.matches("retroarch.exe", "RetroArch - Settings")


def test_profile_yaml_roundtrip_keeps_window_and_region(
    sample_profile: GameProfile, tmp_path: Path
) -> None:
    sample_profile.window = WindowBinding(process_name="snes9x-x64.exe")
    path = tmp_path / "game.yaml"

    sample_profile.save_to_yaml(path)
    loaded = GameProfile.from_yaml(path)

    assert loaded.window == sample_profile.window
    assert loaded.ocr.region == sample_profile.ocr.region
    assert loaded.voices.resolve_character("ROBO").effect == AudioEffect.ROBOT


def test_legacy_screen_coordinates_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "old.yaml"
    path.write_text(
        "game_id: old\n"
        "ocr:\n"
        "  bounding_box: {top: 861, left: 481, width: 1594, height: 479}\n"
        "voices:\n"
        "  default_narrator: narrator\n",
        encoding="utf-8",
    )
    loaded = GameProfile.from_yaml(path)
    assert loaded.window is None
    assert loaded.ocr.region is None


def _win(hwnd: int, title: str, exe: str = "snes9x-x64.exe", **flags) -> WindowInfo:
    return WindowInfo(hwnd=hwnd, title=title, process_name=exe, **flags)


class TestPickWindow:
    def test_the_one_match_is_picked(self) -> None:
        game = _win(1, "Chrono Trigger - Snes9x")
        windows = [_win(2, "Inbox", "chrome.exe"), game]
        assert pick_window(windows, WindowBinding(process_name="snes9x-x64.exe")) == game

    def test_two_matches_without_a_filter_are_ambiguous(self) -> None:
        windows = [_win(1, "Chrono Trigger"), _win(2, "Secret of Mana")]
        result = pick_window(windows, WindowBinding(process_name="snes9x-x64.exe"))
        assert result == AmbiguousMatch(count=2)

    def test_a_filter_picks_one_of_two(self) -> None:
        windows = [_win(1, "Chrono Trigger"), _win(2, "Secret of Mana")]
        binding = WindowBinding(process_name="snes9x-x64.exe", title_contains="chrono")
        assert pick_window(windows, binding) == windows[0]

    def test_dialogs_and_tool_windows_are_never_picked(self) -> None:
        binding = WindowBinding(process_name="snes9x-x64.exe")
        dialog = _win(2, "Input Configuration", owned=True)
        tool = _win(3, "Cheats", tool=True)
        assert pick_window([dialog, tool], binding) is None
        game = _win(1, "Chrono Trigger")
        assert pick_window([dialog, game, tool], binding) == game

    def test_nothing_open(self) -> None:
        assert pick_window([], WindowBinding(process_name="snes9x-x64.exe")) is None


class TestTitleFilter:
    def test_a_browser_always_needs_a_filter(self) -> None:
        tab = _win(1, "Emulator - Chrono Trigger", "Chrome.exe")
        assert needs_title_filter(tab, [tab])

    def test_snes9x_alone_needs_none(self) -> None:
        game = _win(1, "Chrono Trigger")
        dialog = _win(2, "Input Configuration", owned=True)
        assert not needs_title_filter(game, [game, dialog])
        assert binding_for(game, [game, dialog]) == WindowBinding(process_name="snes9x-x64.exe")

    def test_two_windows_of_one_program_need_a_filter(self) -> None:
        windows = [_win(1, "Chrono Trigger"), _win(2, "Secret of Mana")]
        assert needs_title_filter(windows[0], windows)
        binding = binding_for(windows[0], windows, " Chrono ")
        assert binding.title_contains == "Chrono"

    def test_a_browser_without_a_filter_is_refused(self) -> None:
        tab = _win(1, "Inbox", "firefox.exe")
        with pytest.raises(ValueError):
            binding_for(tab, [tab], "")

