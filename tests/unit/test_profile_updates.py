# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bringing the user's own profiles up to date after an upgrade."""

from pathlib import Path

from retrobard.app.profile_updates import (
    RETIRED_VOICES,
    add_shipped_characters,
    replace_retired_voices,
    update_profiles,
)
from retrobard.config import CharacterVoiceConfig, GameProfile, VoicesConfig

SARAH, TALIA = "EXAVITQu4vr4xnSDxMaL", "OZ0L6eISlOejga3XjDFt"
GEORGE, ELDRIN = "JBFqnCBsd6RMkjVDRZzb", "6WwXjDDEMyNmFG95zycZ"
MATILDA, MAISIE = "XrExE9yKIg1WjnnlVkGX", "QtY3JBOUKEB5xzrRfOKc"
OWN = "myOwnClonedVoice0001"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


USER_PROFILE = f"""\
game_id: chrono_trigger
ocr:
  binary_threshold: 123
voices:
  default_narrator: {GEORGE}
  characters:
    crono: {GEORGE}
    MARLE:
      voice_id: {SARAH}
      speed: 1.08
    FROG: {OWN}
  female_pool: [{SARAH}, {MATILDA}]
  male_pool: [{OWN}]
"""

SHIPPED_PROFILE = f"""\
game_id: chrono_trigger
voices:
  default_narrator: {ELDRIN}
  characters:
    CRONO: {ELDRIN}
    FROG: {ELDRIN}
    MAGUS:
      voice_id: magusVoice0000000001
      pitch_semitones: -1.0
"""


class TestReplaceRetiredVoices:
    def test_swaps_characters_narrator_and_pools(self) -> None:
        voices = VoicesConfig(
            default_narrator=GEORGE,
            characters={
                "CRONO": GEORGE,
                "MARLE": CharacterVoiceConfig(voice_id=SARAH, speed=1.08),
                "FROG": OWN,
            },
            female_pool=[SARAH, MATILDA],
            male_pool=[OWN],
        )

        assert replace_retired_voices(voices) == 5
        assert voices.default_narrator == ELDRIN
        assert voices.characters["CRONO"] == ELDRIN
        marle = voices.characters["MARLE"]
        assert isinstance(marle, CharacterVoiceConfig)
        # Only the voice changes; the user's tuning stays
        assert marle.voice_id == TALIA and marle.speed == 1.08
        assert voices.characters["FROG"] == OWN
        assert voices.female_pool == [TALIA, MAISIE]
        assert voices.male_pool == [OWN]

    def test_leaves_current_voices_alone(self) -> None:
        voices = VoicesConfig(default_narrator=OWN, female_pool=[TALIA], male_pool=[ELDRIN])
        assert replace_retired_voices(voices) == 0
        assert voices.female_pool == [TALIA]

    def test_pool_does_not_hold_a_voice_twice(self) -> None:
        voices = VoicesConfig(default_narrator=OWN, female_pool=[SARAH, TALIA], male_pool=[])
        replace_retired_voices(voices)
        assert voices.female_pool == [TALIA]

    def test_no_replacement_is_itself_retired(self) -> None:
        assert not set(RETIRED_VOICES.values()) & set(RETIRED_VOICES)


class TestAddShippedCharacters:
    def test_adds_only_missing_characters(self) -> None:
        voices = VoicesConfig(default_narrator=OWN, characters={"FROG": OWN})
        shipped = VoicesConfig(
            default_narrator=ELDRIN,
            characters={"FROG": ELDRIN, "MAGUS": CharacterVoiceConfig(voice_id="m" * 20)},
        )

        assert add_shipped_characters(voices, shipped) == ["MAGUS"]
        assert voices.characters["FROG"] == OWN
        assert voices.characters["MAGUS"] == shipped.characters["MAGUS"]
        # A copy, so changing the user's profile never touches the shipped one
        assert voices.characters["MAGUS"] is not shipped.characters["MAGUS"]


class TestUpdateProfiles:
    def test_updates_the_users_profile_from_the_shipped_one(self, tmp_path: Path) -> None:
        shipped_dir, user_dir = tmp_path / "shipped", tmp_path / "user"
        _write(shipped_dir / "chrono_trigger.yaml", SHIPPED_PROFILE)
        user_path = _write(user_dir / "chrono_trigger.yaml", USER_PROFILE)

        assert update_profiles(shipped_dir, user_dir) == ["chrono_trigger"]

        profile = GameProfile.from_yaml(user_path)
        voices = profile.voices
        assert voices.default_narrator == ELDRIN
        assert voices.characters["CRONO"] == ELDRIN
        assert voices.characters["FROG"] == OWN
        assert set(voices.characters) == {"CRONO", "MARLE", "FROG", "MAGUS"}
        magus = voices.characters["MAGUS"]
        assert isinstance(magus, CharacterVoiceConfig) and magus.pitch_semitones == -1.0
        # Everything outside the voices is kept
        assert profile.ocr.binary_threshold == 123

    def test_second_start_changes_nothing(self, tmp_path: Path) -> None:
        shipped_dir, user_dir = tmp_path / "shipped", tmp_path / "user"
        _write(shipped_dir / "chrono_trigger.yaml", SHIPPED_PROFILE)
        user_path = _write(user_dir / "chrono_trigger.yaml", USER_PROFILE)
        update_profiles(shipped_dir, user_dir)
        saved = user_path.read_text(encoding="utf-8")

        assert update_profiles(shipped_dir, user_dir) == []
        assert user_path.read_text(encoding="utf-8") == saved

    def test_up_to_date_profile_is_not_rewritten(self, tmp_path: Path) -> None:
        text = f"game_id: other_game\nvoices:\n  default_narrator: {OWN}\n"
        user_path = _write(tmp_path / "user" / "other_game.yaml", text)

        assert update_profiles(tmp_path / "shipped", tmp_path / "user") == []
        assert user_path.read_text(encoding="utf-8") == text

    def test_from_source_only_replaces_retired_voices(self, tmp_path: Path) -> None:
        user_path = _write(tmp_path / "profiles" / "chrono_trigger.yaml", USER_PROFILE)

        assert update_profiles(None, tmp_path / "profiles") == ["chrono_trigger"]
        voices = GameProfile.from_yaml(user_path).voices
        assert voices.characters["CRONO"] == ELDRIN
        assert "MAGUS" not in voices.characters

    def test_broken_profile_does_not_stop_the_others(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        broken = _write(user_dir / "aaa_broken.yaml", "voices: [not: valid")
        _write(user_dir / "chrono_trigger.yaml", USER_PROFILE)

        assert update_profiles(None, user_dir) == ["chrono_trigger"]
        assert broken.read_text(encoding="utf-8") == "voices: [not: valid"

    def test_missing_folder(self, tmp_path: Path) -> None:
        assert update_profiles(None, tmp_path / "nowhere") == []
