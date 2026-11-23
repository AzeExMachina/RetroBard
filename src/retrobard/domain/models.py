# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import dataclass
from enum import Enum


class EventType(Enum):
    SPEAK = "speak"
    STOP = "stop"
    CLEAR_CONTEXT = "clear_context"

class AudioEffect(str, Enum):
    NONE = "none"
    ROBOT = "robot"
    MONSTER = "monster"
    CAVE = "cave"

@dataclass(frozen=True)
class DialogueEvent:
    event_type: EventType
    raw_text: str = ""
    explicit_speaker: str | None = None
    source: str = "unknown"  # "ocr" or "lua_ram"

@dataclass(frozen=True)
class VoiceProfile:
    voice_id: str
    stability: float = 0.50
    similarity_boost: float = 0.75
    style: float = 0.00
    speed: float = 1.00
    pitch_semitones: float = 0.0
    effect: AudioEffect = AudioEffect.NONE
    use_speaker_boost: bool = True

    def signature(self) -> str:
        """Cache key for ElevenLabs API parameters (pitch & DSP effects run locally)."""
        return (
            f"{self.voice_id}|st={self.stability:.2f}|sb={self.similarity_boost:.2f}"
            f"|sy={self.style:.2f}|sp={self.speed:.2f}"
        )