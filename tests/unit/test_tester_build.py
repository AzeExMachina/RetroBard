# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shipping the app to testers: data folders, shipped vs own letters, export/merge, API key."""

import tomllib
from pathlib import Path

import pytest

from retrobard import __version__
from retrobard.adapters.ocr.font_merge import (
    SHIPPED_SOURCE,
    Contribution,
    merge_letters,
)
from retrobard.adapters.ocr.glyph_reader import GlyphFont
from retrobard.app import setup
from retrobard.app.session import load_font
from retrobard.config import AppSettings
from retrobard.credentials import (
    elevenlabs_api_key,
    forget_api_key,
    store_api_key,
    stored_api_key,
)
from retrobard.paths import install_default_profiles

A = "3:0110/1001/1111/1001"
B = "3:1110/1001/1110/1001/1110"
C = "3:0111/1000/1000/0111"


def _font(**glyphs: str) -> GlyphFont:
    return GlyphFont(line_phase=10, glyphs=dict(glyphs))


def _settings(tmp_path: Path, shipped: Path | None) -> AppSettings:
    return AppSettings(
        _env_file=None,
        cache_dir=tmp_path / "cache",
        profiles_dir=tmp_path / "user",
        bundled_profiles_dir=shipped,
    )


@pytest.fixture
def shipped_dir(tmp_path: Path) -> Path:
    shipped = tmp_path / "shipped"
    shipped.mkdir()
    (shipped / "chrono_trigger.yaml").write_text("game_id: chrono_trigger\n", encoding="utf-8")
    GlyphFont(line_phase=10, glyphs={A: "A", B: "B"}).save(shipped / "chrono_trigger.font.json")
    return shipped


def test_version_matches_pyproject() -> None:
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__


class TestDefaultProfiles:
    def test_copies_profiles_the_user_does_not_have(self, tmp_path: Path, shipped_dir: Path) -> None:
        user = tmp_path / "user"
        assert install_default_profiles(shipped_dir, user) == ["chrono_trigger"]
        assert (user / "chrono_trigger.yaml").exists()
        # Letters stay in the app; the user's file only holds what they teach
        assert not (user / "chrono_trigger.font.json").exists()

    def test_never_overwrites_the_users_profile(self, tmp_path: Path, shipped_dir: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        (user / "chrono_trigger.yaml").write_text("mine", encoding="utf-8")

        assert install_default_profiles(shipped_dir, user) == []
        assert (user / "chrono_trigger.yaml").read_text(encoding="utf-8") == "mine"

    def test_does_nothing_when_running_from_source(self, tmp_path: Path) -> None:
        assert install_default_profiles(None, tmp_path) == []


class TestShippedAndOwnLetters:
    def test_reading_uses_shipped_and_own_letters(self, tmp_path: Path, shipped_dir: Path) -> None:
        settings = _settings(tmp_path, shipped_dir)
        _font(**{C: "C"}).save(settings.profiles_dir / "chrono_trigger.font.json")

        assert load_font(settings, "chrono_trigger").glyphs == {A: "A", B: "B", C: "C"}

    def test_saving_writes_only_the_users_letters(self, tmp_path: Path, shipped_dir: Path) -> None:
        settings = _settings(tmp_path, shipped_dir)
        game_font = setup.load_font(settings, "chrono_trigger")
        game_font.font.glyphs[C] = "C"
        game_font.font.glyphs[A] = "4"  # the user corrects a shipped letter
        game_font.save()

        own = GlyphFont.load(settings.profiles_dir / "chrono_trigger.font.json")
        assert own.glyphs == {C: "C", A: "4"}
        assert GlyphFont.load(shipped_dir / "chrono_trigger.font.json").glyphs == {A: "A", B: "B"}

    def test_export_holds_own_letters_and_who_made_them(self, tmp_path: Path, shipped_dir: Path) -> None:
        settings = _settings(tmp_path, shipped_dir)
        _font(**{C: "C"}).save(settings.profiles_dir / "chrono_trigger.font.json")
        destination = tmp_path / "out" / "letters.json"

        count = setup.export_letters(settings, "chrono_trigger", destination, "9.9", "snes9x.exe")

        assert count == 1
        assert GlyphFont.load(destination).glyphs == {C: "C"}
        text = destination.read_text(encoding="utf-8")
        assert "RetroBard 9.9" in text and "snes9x.exe" in text


class TestMergeLetters:
    def test_agreed_new_shapes_are_added(self) -> None:
        result = merge_letters(
            _font(**{A: "A"}),
            [Contribution("ann", _font(**{B: "B", A: "A"})), Contribution("bob", _font(**{B: "B"}))],
        )
        assert result.added == {B: "B"}
        assert result.already_known == 1
        assert result.merged.glyphs == {A: "A", B: "B"}
        assert not result.conflicts

    def test_disagreements_are_left_out(self) -> None:
        result = merge_letters(
            _font(),
            [Contribution("ann", _font(**{C: "C"})), Contribution("bob", _font(**{C: "G"}))],
        )
        assert C not in result.merged.glyphs
        assert result.conflicts == {C: {"C": ["ann"], "G": ["bob"]}}

    def test_contradicting_the_shipped_font_is_a_conflict(self) -> None:
        result = merge_letters(_font(**{A: "A"}), [Contribution("ann", _font(**{A: "4"}))])
        assert result.merged.glyphs[A] == "A"
        assert result.conflicts == {A: {"A": [SHIPPED_SOURCE], "4": ["ann"]}}

    def test_warns_about_a_different_line_layout(self) -> None:
        odd = GlyphFont(line_pitch=18, line_phase=4, glyphs={B: "B"})
        result = merge_letters(_font(), [Contribution("ann", odd)])
        assert result.warnings and "ann" in result.warnings[0]


class TestApiKey:
    def test_stored_key_is_used_when_no_environment_key(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, None)
        assert elevenlabs_api_key(settings) == ""

        store_api_key("  sk_test  ")
        assert stored_api_key() == "sk_test"
        assert elevenlabs_api_key(settings) == "sk_test"

        forget_api_key()
        forget_api_key()  # removing twice is fine
        assert elevenlabs_api_key(settings) == ""

    def test_environment_key_wins(self, tmp_path: Path) -> None:
        store_api_key("sk_stored")
        settings = AppSettings(_env_file=None, ELEVENLABS_API_KEY="sk_env")
        assert elevenlabs_api_key(settings) == "sk_env"
