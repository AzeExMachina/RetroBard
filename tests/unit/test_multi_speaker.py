# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""One dialogue box where two characters talk in turn ("RANDI:...\\nPOPOI:...")."""

from collections.abc import Iterator

import pytest

from retrobard.domain.models import DialogueEvent, EventType, VoiceProfile
from retrobard.domain.parser import DialogueParser
from retrobard.services import orchestrator as orchestrator_module
from retrobard.services.orchestrator import VoiceOrchestrator


class TestSplitting:
    @pytest.mark.parametrize(
        "box, parts",
        [
            ("RANDI:Look out!\nPOPOI:Where?", ["RANDI:Look out!", "POPOI:Where?"]),
            (
                "RANDI:Look out!\nIt's coming!\nPOPOI:Where?\nI can't see it.",
                ["RANDI:Look out!\nIt's coming!", "POPOI:Where?\nI can't see it."],
            ),
            # One character over several lines
            ("MAN:Hey! Can you hear me?\nWatch how the monster\nmoves!", None),
            # A wrapped sentence with a colon in it is not a new speaker
            ("RANDI:I need these things:\nherbs and candy.", None),
            ("MERCHANT:Open from\n10:30 to dusk.", None),
            # A name on its own line, then the text (dropped colon)
            ("MARLE\nWhere are we going?", None),
        ],
    )
    def test_a_box_splits_where_another_character_starts(self, sample_profile, box, parts) -> None:
        assert DialogueParser(sample_profile).split_speakers(box) == (parts or [box])

    def test_each_part_gets_its_own_speaker(self, sample_profile) -> None:
        parsed = DialogueParser(sample_profile).parse_all("CRONO:Let's go!\nMARLE:Wait for me!")

        assert [(p.speaker, p.clean_text) for p in parsed] == [
            ("CRONO", "Let's go!"),
            ("MARLE", "Wait for me!"),
        ]
        assert parsed[0].voice_id != parsed[1].voice_id


class RecordingTTS:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def synthesize_stream(self, text: str, voice: VoiceProfile) -> Iterator[bytes]:
        self.requests.append((voice.voice_id, text))
        yield f"audio:{text}".encode()


class NoCache:
    def get(self, game_id, speaker, text, voice_id):
        return None

    def put(self, game_id, speaker, text, voice_id, data) -> None:
        pass


class BusyPlayer:
    """Says it is still playing for a few checks after each clip starts."""

    def __init__(self, busy_checks: int = 3, on_busy=None) -> None:
        self.busy_checks = busy_checks
        self.on_busy = on_busy
        self.log: list[str] = []
        self._left = 0

    def play_bytes(self, audio_data: bytes, pitch_semitones: float = 0.0, effect=None) -> None:
        self.log.append("play " + audio_data.decode())
        self._left = self.busy_checks

    def is_busy(self) -> bool:
        if self._left:
            self._left -= 1
            self.log.append("busy")
            if self.on_busy:
                self.on_busy()
            return True
        return False

    def stop(self) -> None:
        self._left = 0


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(orchestrator_module, "SPEAKER_PAUSE_S", 0.0)
    monkeypatch.setattr(orchestrator_module.time, "sleep", lambda _s: None)


def _orchestrator(sample_profile, player) -> tuple[VoiceOrchestrator, RecordingTTS]:
    tts = RecordingTTS()
    return (
        VoiceOrchestrator(
            game_id=sample_profile.game_id,
            parser=DialogueParser(sample_profile),
            tts=tts,
            cache=NoCache(),
            player=player,
        ),
        tts,
    )


def _speak(orchestrator: VoiceOrchestrator, text: str, line: int | None = None) -> None:
    orchestrator._handle_speak(
        DialogueEvent(event_type=EventType.SPEAK, raw_text=text, source="ocr"), line
    )


def test_two_characters_are_spoken_in_turn(sample_profile) -> None:
    player = BusyPlayer()
    orchestrator, tts = _orchestrator(sample_profile, player)

    _speak(orchestrator, "CRONO:Let's go!\nMARLE:Wait for me!")

    assert [text for _, text in tts.requests] == ["Let's go!", "Wait for me!"]
    assert tts.requests[0][0] != tts.requests[1][0]  # two voices
    # Marle starts only once Crono has finished
    assert player.log == ["play audio:Let's go!", "busy", "busy", "busy", "play audio:Wait for me!"]


def test_skipping_while_the_first_talks_drops_the_second(sample_profile) -> None:
    orchestrator = None

    def skip() -> None:
        orchestrator.submit_event(DialogueEvent(event_type=EventType.STOP))

    player = BusyPlayer(on_busy=skip)
    orchestrator, _tts = _orchestrator(sample_profile, player)
    orchestrator._line_number = 1
    orchestrator._last_speak_start = 0.0  # past the grace period for skips

    _speak(orchestrator, "CRONO:Let's go!\nMARLE:Wait for me!", line=1)

    assert player.log[0] == "play audio:Let's go!"
    assert "play audio:Wait for me!" not in player.log


def test_a_box_that_grows_only_says_what_was_added(sample_profile) -> None:
    player = BusyPlayer(busy_checks=0)
    orchestrator, tts = _orchestrator(sample_profile, player)

    _speak(orchestrator, "CRONO:Let's go!")
    _speak(orchestrator, "CRONO:Let's go!\nMARLE:Wait for me!")
    _speak(orchestrator, "CRONO:Let's go!\nMARLE:Wait for me!")  # read again

    assert [text for _, text in tts.requests] == ["Let's go!", "Wait for me!"]


def test_a_new_box_with_the_same_speaker_is_spoken(sample_profile) -> None:
    player = BusyPlayer(busy_checks=0)
    orchestrator, tts = _orchestrator(sample_profile, player)

    _speak(orchestrator, "CRONO:Let's go!\nMARLE:Wait for me!")
    _speak(orchestrator, "CRONO:Come on, hurry!")

    assert [text for _, text in tts.requests] == ["Let's go!", "Wait for me!", "Come on, hurry!"]
