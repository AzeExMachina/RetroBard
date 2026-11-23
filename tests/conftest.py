# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import keyring
import pytest
from keyring.backend import KeyringBackend

from retrobard.config import (
    CharacterVoiceConfig,
    DialogueRules,
    GameProfile,
    NormRect,
    OcrConfig,
    VoicesConfig,
)
from retrobard.domain.models import AudioEffect


@pytest.fixture
def sample_profile() -> GameProfile:
    return GameProfile(
        game_id="chrono_trigger",
        ocr=OcrConfig(
            region=NormRect(x=0.1, y=0.6, w=0.8, h=0.3),
            upscale_factor=3,
            binary_threshold=180,
            cursor_mask_ratio=0.12,
            debounce_ms=200,
        ),
        dialogue_rules=DialogueRules(
            speaker_delimiter=":",
            strip_patterns=[r"^[\*\-\|\s]+"],
            min_length=2,
        ),
        voices=VoicesConfig(
            default_narrator="voice_narrator_default",
            narrate_unnamed=True,  # most tests parse lines without a speaker name
            characters={
                "CRONO": "voice_crono_123",
                "MARLE": "voice_marle_456",
                "FROG": CharacterVoiceConfig(
                    voice_id="voice_callum_789",
                    speed=0.88,
                    stability=0.55,
                    pitch_semitones=-1.5,
                    effect=AudioEffect.NONE,
                ),
                "ROBO": CharacterVoiceConfig(
                    voice_id="voice_callum_789",
                    speed=0.95,
                    stability=0.95,
                    pitch_semitones=-0.5,
                    effect=AudioEffect.ROBOT,
                ),
            },
            female_pool=["female_npc_1", "female_npc_2"],
            male_pool=["male_npc_1", "male_npc_2"],
        ),
    )

class MemoryKeyring(KeyringBackend):
    """Keeps passwords in memory, so tests never read or write the real credential store."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password

    def delete_password(self, service, username):
        if self.passwords.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError("not set")


@pytest.fixture(autouse=True)
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)
