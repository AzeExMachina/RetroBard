# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Names, paths, patterns and settings read from files and the environment."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from retrobard.adapters.cache.sqlite_disk import SqliteDiskCache
from retrobard.app.session import SessionError, font_path, profile_path
from retrobard.config import AppSettings, DialogueRules, GameProfile, VoicesConfig


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(_env_file=None, cache_dir=tmp_path / "cache", profiles_dir=tmp_path)


class TestGameNames:
    @pytest.mark.parametrize("game", ["../x", "Chrono", "a b", "", "x" * 65, "c:\\evil"])
    def test_bad_game_id_is_rejected(self, game: str) -> None:
        with pytest.raises(ValidationError):
            GameProfile(game_id=game, voices=VoicesConfig(default_narrator="n"))

    def test_good_game_id(self) -> None:
        assert GameProfile(game_id="chrono_trigger2", voices=VoicesConfig(default_narrator="n"))

    def test_paths_refuse_bad_names(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        with pytest.raises(SessionError, match="a-z, 0-9 and _"):
            profile_path(settings, "../x")
        with pytest.raises(SessionError):
            font_path(settings, "..\\x")
        assert profile_path(settings, "chrono_trigger") == tmp_path / "chrono_trigger.yaml"


class TestStripPatterns:
    def test_catastrophic_long_pattern_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="longer than 200"):
            DialogueRules(strip_patterns=["(a+)+" * 50])

    def test_invalid_pattern_is_named(self) -> None:
        with pytest.raises(ValidationError, match=r"'\[unclosed'"):
            DialogueRules(strip_patterns=[r"^\*", "[unclosed"])

    def test_good_patterns_are_kept(self) -> None:
        assert DialogueRules(strip_patterns=[r"^[\*\-]+"]).strip_patterns == [r"^[\*\-]+"]


class TestEnvironment:
    def test_generic_names_are_ignored(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("CACHE_DIR", str(tmp_path / "generic"))
        monkeypatch.setenv("UDP_PORT", "1234")
        monkeypatch.setenv("RETROBARD_CACHE_DIR", str(tmp_path / "ours"))
        monkeypatch.setenv("RETROBARD_CACHE_MAX_MB", "42")

        settings = AppSettings(_env_file=None)

        assert settings.cache_dir == tmp_path / "ours"
        assert settings.udp_port == 5005
        assert settings.cache_max_mb == 42

    def test_documented_names_still_work(self, monkeypatch) -> None:
        monkeypatch.setenv("UDP_TOKEN", "from-env")
        monkeypatch.setenv("ELEVENLABS_MODEL_ID", "model-x")
        settings = AppSettings(_env_file=None)
        assert settings.udp_token == "from-env"
        assert settings.elevenlabs_model_id == "model-x"

    def test_fields_can_still_be_set_in_code(self, tmp_path: Path) -> None:
        assert _settings(tmp_path).cache_dir == tmp_path / "cache"


def test_cache_refuses_to_write_outside_its_folder(tmp_path: Path) -> None:
    cache = SqliteDiskCache(tmp_path / "cache")
    with pytest.raises(ValueError):
        cache.put("../outside", "CRONO", "Hi", "v1", b"x")
    assert not (tmp_path / "outside").exists()


def test_documented_fields_can_be_set_in_code(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None, udp_token="t", elevenlabs_api_key="k", elevenlabs_model_id="m"
    )
    assert (settings.udp_token, settings.elevenlabs_api_key, settings.elevenlabs_model_id) == (
        "t",
        "k",
        "m",
    )
