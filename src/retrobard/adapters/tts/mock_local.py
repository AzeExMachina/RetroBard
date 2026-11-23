# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
from collections.abc import Iterator

from retrobard.domain.models import VoiceProfile
from retrobard.domain.status import StatusKind, StatusSink, console_status, report


class ConsoleMockTTS:
    """Zero-cost TTS adapter for testing OCR & pipeline wiring without spending credits."""

    def __init__(self, status: StatusSink = console_status):
        self.status = status

    def synthesize_stream(self, text: str, voice: VoiceProfile) -> Iterator[bytes]:
        report(
            self.status,
            StatusKind.DEBUG,
            f"  [MOCK TTS] Voice={voice.voice_id[:8]}.. "
            f"(spd={voice.speed}, stb={voice.stability}, sty={voice.style}): \"{text}\""
        )
        yield b""