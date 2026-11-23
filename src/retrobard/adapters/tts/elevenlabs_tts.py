# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import time
from collections.abc import Iterator

from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError

from retrobard.domain.models import VoiceProfile


class ElevenLabsTTS:
    """Streams speech from ElevenLabs.

    With a `pcm_*` output format the bytes are raw 16-bit mono samples that can be
    played the moment they arrive, instead of waiting for a complete MP3.
    """

    def __init__(
        self,
        api_key: str,
        model_id: str = "eleven_flash_v2_5",
        output_format: str = "mp3_44100_128",
    ):
        if not api_key:
            raise ValueError(
                "No ElevenLabs API key. Add yours with the 'ElevenLabs key' button on the Play tab "
                "(or set ELEVENLABS_API_KEY)."
            )
        self.client = ElevenLabs(api_key=api_key)
        self.model_id = model_id
        self.output_format = output_format

    def warm_up(self) -> float | None:
        """Opens the connection to ElevenLabs ahead of the first line, so that line
        does not pay for the connection setup. Costs no credits.
        Returns how long it took, or None if it failed (the first line will then
        simply connect as before)."""
        start = time.perf_counter()
        try:
            self.client.models.list()
        except ApiError:
            # The service answered (e.g. a key without the models_read permission):
            # the connection is open, which is all this call is for.
            pass
        except Exception:  # noqa: BLE001 - could not reach the service at all
            return None
        return time.perf_counter() - start

    def synthesize_stream(self, text: str, voice: VoiceProfile) -> Iterator[bytes]:
        settings = VoiceSettings(
            stability=voice.stability,
            similarity_boost=voice.similarity_boost,
            style=voice.style,
            speed=voice.speed,
            use_speaker_boost=voice.use_speaker_boost,
        )
        stream = self.client.text_to_speech.stream(
            text=text,
            voice_id=voice.voice_id,
            model_id=self.model_id,
            output_format=self.output_format,
            voice_settings=settings,
        )
        for chunk in stream:
            if isinstance(chunk, bytes) and chunk:
                yield chunk
