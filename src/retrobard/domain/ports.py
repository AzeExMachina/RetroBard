# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from retrobard.domain.models import AudioEffect, DialogueEvent, VoiceProfile


class EventProducer(Protocol):
    """Any source that emits DialogueEvents (OCR loop, UDP socket, skip input)."""
    def start(self, emit: Callable[[DialogueEvent], None]) -> None: ...
    def stop(self) -> None: ...


class FrameSource(Protocol):
    """Hands out frames of the one bound game window, never the desktop."""
    def grab(self) -> Any | None: ...  # BGRA array of the client area; None if unavailable
    def is_foreground(self) -> bool: ...


class TTSProvider(Protocol):
    """Any engine that turns text + a voice profile into MP3 bytes."""
    def synthesize_stream(self, text: str, voice: VoiceProfile) -> Iterator[bytes]: ...


class AudioCache(Protocol):
    """Stores and retrieves synthesized audio by content signature."""
    def get(self, game_id: str, speaker: str, text: str, voice_id: str) -> bytes | None: ...
    def put(self, game_id: str, speaker: str, text: str, voice_id: str, data: bytes) -> None: ...


class AudioPlayer(Protocol):
    """Plays audio non-blocking and supports immediate interruption."""
    def play_bytes(
        self,
        audio_data: bytes,
        pitch_semitones: float = 0.0,
        effect: AudioEffect = AudioEffect.NONE,
    ) -> None: ...
    def stop(self) -> None: ...
