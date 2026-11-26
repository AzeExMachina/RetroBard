# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import dataclasses
import threading
import time
from collections.abc import Iterator

from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError

from retrobard.domain.models import VoiceProfile
from retrobard.domain.ports import VoiceUnavailableError
from retrobard.domain.status import StatusKind, StatusSink, null_status, report


def _refusal_message(error: ApiError) -> str:
    """ElevenLabs' own explanation of a refused request, or the status code."""
    body = error.body if isinstance(error.body, dict) else {}
    detail = body.get("detail")
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])
    if isinstance(detail, str) and detail:
        return detail
    return f"HTTP {error.status_code}"


class ElevenLabsTTS:
    """Streams speech from ElevenLabs.

    With a `pcm_*` output format the bytes are raw 16-bit mono samples that can be
    played the moment they arrive, instead of waiting for a complete MP3.

    `fallback_voices` maps a voice to one to use instead when the user's plan
    does not include it (HTTP 402). Free plans cannot use Voice Library voices
    through the API, but can use ElevenLabs' Default voices until 2026-12-31, so
    each replacement voice falls back to the Default voice it replaced.
    """

    def __init__(
        self,
        api_key: str,
        model_id: str = "eleven_flash_v2_5",
        output_format: str = "mp3_44100_128",
        fallback_voices: dict[str, str] | None = None,
        status: StatusSink = null_status,
    ):
        if not api_key:
            raise ValueError(
                "No ElevenLabs API key. Add yours with the 'ElevenLabs key' button on the Play tab "
                "(or set ELEVENLABS_API_KEY)."
            )
        self.client = ElevenLabs(api_key=api_key)
        self.model_id = model_id
        self.output_format = output_format
        self.fallback_voices = dict(fallback_voices or {})
        self.status = status
        # Voices the plan refused this session -> the voice used instead
        self._substitutes: dict[str, str] = {}
        # Voices that cannot be used at all this session -> what to tell the user
        self._refused: dict[str, str] = {}
        self._lock = threading.Lock()

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

    def actual_voice_id(self, voice_id: str) -> str:
        """The voice really used for `voice_id` (differs once the plan refused it),
        so cached audio is filed under the voice that spoke it."""
        with self._lock:
            return self._substitutes.get(voice_id, voice_id)

    def synthesize_stream(self, text: str, voice: VoiceProfile) -> Iterator[bytes]:
        with self._lock:
            refused = self._refused.get(voice.voice_id)
        if refused:
            raise VoiceUnavailableError(refused)
        voice_id = self.actual_voice_id(voice.voice_id)
        sent_audio = False
        try:
            for chunk in self._stream(text, dataclasses.replace(voice, voice_id=voice_id)):
                sent_audio = True
                yield chunk
            return
        except ApiError as error:
            # ElevenLabs refuses a voice before sending any audio; only then can
            # another voice say the line without repeating part of it
            if error.status_code != 402 or sent_audio:
                raise
            fallback = self.fallback_voices.get(voice_id)
            if fallback is None:
                self._refuse(
                    voice.voice_id,
                    f"ElevenLabs would not use voice {voice_id[:8]}..: {_refusal_message(error)} "
                    "Pick another voice for this character, or upgrade your ElevenLabs plan.",
                    error,
                )
        try:
            first = True
            for chunk in self._stream(text, dataclasses.replace(voice, voice_id=fallback)):
                if first:
                    # The stand-in works: use it for this voice from now on
                    first = False
                    with self._lock:
                        self._substitutes[voice.voice_id] = fallback
                    report(
                        self.status,
                        StatusKind.WARNING,
                        f"[VOICE] Your ElevenLabs plan cannot use voice {voice_id[:8]}.. from the "
                        f"Voice Library, so the Default voice it replaced ({fallback[:8]}..) "
                        "speaks instead. ElevenLabs removes Default voices on 31 December 2026; "
                        "a paid plan keeps every voice working.",
                        voice_id=voice_id,
                        fallback=fallback,
                    )
                yield chunk
        except ApiError as error:
            if not first:
                raise
            self._refuse(
                voice.voice_id,
                f"ElevenLabs would not use voice {voice_id[:8]}.. nor its Default voice "
                f"{fallback[:8]}..: {_refusal_message(error)} Pick another voice for this "
                "character, or upgrade your ElevenLabs plan.",
                error,
            )

    def _refuse(self, voice_id: str, message: str, cause: Exception) -> None:
        """Remembers that `voice_id` cannot be used this session, then raises."""
        with self._lock:
            self._refused[voice_id] = message
        raise VoiceUnavailableError(message) from cause

    def _stream(self, text: str, voice: VoiceProfile) -> Iterator[bytes]:
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
