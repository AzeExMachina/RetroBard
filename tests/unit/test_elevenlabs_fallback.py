# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Free ElevenLabs plans cannot use Voice Library voices through the API."""

import pytest
from elevenlabs.core.api_error import ApiError

from retrobard.adapters.tts.elevenlabs_tts import ElevenLabsTTS
from retrobard.app.profile_updates import RETIRED_VOICES, original_default_voices
from retrobard.domain.models import DialogueEvent, EventType, VoiceProfile
from retrobard.domain.parser import DialogueParser
from retrobard.domain.ports import VoiceUnavailableError
from retrobard.domain.status import StatusKind
from retrobard.services.orchestrator import VoiceOrchestrator

LIBRARY = "10NkTYmU7tSz3Kkl3Lex"  # Kaelen, a replacement voice
DEFAULT = "SOYHLrjzK2X1ezoPC6cr"  # Harry, the Default voice Kaelen replaced


def _paid_plan_required() -> ApiError:
    return ApiError(
        status_code=402,
        body={
            "detail": {
                "type": "payment_required",
                "code": "paid_plan_required",
                "message": "Free users cannot use library voices via the API.",
            }
        },
    )


class FreePlan:
    """Stands in for client.text_to_speech: refuses the given voices before any audio."""

    def __init__(self, refused: set[str]):
        self.refused = refused
        self.asked: list[str] = []

    def stream(self, *, voice_id: str, **_kwargs):
        self.asked.append(voice_id)
        if voice_id in self.refused:
            raise _paid_plan_required()
        return iter([b"audio-" + voice_id[:4].encode()])


def _tts(refused: set[str], events: list) -> tuple[ElevenLabsTTS, FreePlan]:
    tts = ElevenLabsTTS(
        api_key="test-key",
        fallback_voices=original_default_voices(),
        status=events.append,
    )
    plan = FreePlan(refused)
    tts.client = type("Client", (), {"text_to_speech": plan})()
    return tts, plan


def test_every_replacement_voice_falls_back_to_a_default_voice() -> None:
    originals = original_default_voices()

    assert set(originals) == set(RETIRED_VOICES.values())
    assert originals[LIBRARY] == DEFAULT
    # Charlotte is gone for good, so her shared replacement goes back to River
    assert originals["dvbL7qkNGZY1IqPGZAjM"] == "SAz9YHcvj6GT2YYXdXww"


def test_a_refused_library_voice_is_spoken_by_its_default_voice() -> None:
    events: list = []
    tts, plan = _tts({LIBRARY}, events)

    first = b"".join(tts.synthesize_stream("Hello!", VoiceProfile(voice_id=LIBRARY)))
    second = b"".join(tts.synthesize_stream("Again!", VoiceProfile(voice_id=LIBRARY)))

    assert first == second == b"audio-SOYH"
    # The refusal is learned once; later lines go straight to the Default voice
    assert plan.asked == [LIBRARY, DEFAULT, DEFAULT]
    assert tts.actual_voice_id(LIBRARY) == DEFAULT
    assert [e.kind for e in events] == [StatusKind.WARNING]
    assert "31 December 2026" in events[0].message


def test_a_voice_with_nothing_to_fall_back_on_gives_a_clear_error() -> None:
    tts, _plan = _tts({"someLibraryVoice"}, [])

    with pytest.raises(VoiceUnavailableError, match="Free users cannot use library voices"):
        b"".join(tts.synthesize_stream("Hi", VoiceProfile(voice_id="someLibraryVoice")))


def test_once_default_voices_are_gone_the_error_is_clear() -> None:
    tts, _plan = _tts({LIBRARY, DEFAULT}, [])

    with pytest.raises(VoiceUnavailableError, match="nor its Default voice"):
        b"".join(tts.synthesize_stream("Hi", VoiceProfile(voice_id=LIBRARY)))


class _Cache:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def get(self, game_id, speaker, text, voice_id):
        return None

    def put(self, game_id, speaker, text, voice_id, data):
        self.keys.append(voice_id)


class _Player:
    def play_bytes(self, audio_data, pitch_semitones=0.0, effect=None) -> None:
        pass

    def stop(self) -> None:
        pass


def _orchestrator(sample_profile, tts, cache, events) -> VoiceOrchestrator:
    sample_profile.voices.characters["CRONO"] = LIBRARY
    return VoiceOrchestrator(
        game_id=sample_profile.game_id,
        parser=DialogueParser(sample_profile),
        tts=tts,
        cache=cache,
        player=_Player(),
        status=events.append,
    )


def test_stand_in_audio_is_cached_under_the_voice_that_spoke_it(sample_profile) -> None:
    events: list = []
    tts, _plan = _tts({LIBRARY}, events)
    cache = _Cache()
    orchestrator = _orchestrator(sample_profile, tts, cache, events)

    orchestrator._handle_speak(DialogueEvent(event_type=EventType.SPEAK, raw_text="CRONO: Hi there!"))

    assert len(cache.keys) == 1
    assert cache.keys[0].startswith(DEFAULT)


def test_a_refused_voice_is_reported_once_without_a_traceback(sample_profile) -> None:
    events: list = []
    tts, _plan = _tts({LIBRARY, DEFAULT}, events)
    orchestrator = _orchestrator(sample_profile, tts, _Cache(), events)

    for text in ("CRONO: Hi there!", "CRONO: Over here!"):
        orchestrator._event_queue.put((DialogueEvent(event_type=EventType.SPEAK, raw_text=text), None))
    orchestrator._event_queue.put((None, None))  # ends run_forever once both are handled
    orchestrator.run_forever()

    errors = [e.message for e in events if e.kind == StatusKind.ERROR]
    assert len(errors) == 1
    assert "Traceback" not in errors[0]
    assert errors[0].startswith("[VOICE]")
