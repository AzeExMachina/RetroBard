# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
from collections.abc import Iterator

from retrobard.config import GameProfile
from retrobard.domain.models import AudioEffect, DialogueEvent, EventType, VoiceProfile
from retrobard.domain.parser import DialogueParser
from retrobard.services.orchestrator import VoiceOrchestrator


class FakeTTS:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def synthesize_stream(self, text: str, voice: VoiceProfile) -> Iterator[bytes]:
        self.spoken.append(text)
        yield b"audio"


class FakeCache:
    def get(self, game_id: str, speaker: str, text: str, voice_id: str) -> bytes | None:
        return None

    def put(self, game_id: str, speaker: str, text: str, voice_id: str, data: bytes) -> None:
        pass


class FakePlayer:
    def play_bytes(
        self,
        audio_data: bytes,
        pitch_semitones: float = 0.0,
        effect: AudioEffect = AudioEffect.NONE,
    ) -> None:
        pass

    def stop(self) -> None:
        pass


def _make(profile: GameProfile) -> tuple[VoiceOrchestrator, FakeTTS]:
    tts = FakeTTS()
    orchestrator = VoiceOrchestrator(
        game_id=profile.game_id,
        parser=DialogueParser(profile),
        tts=tts,
        cache=FakeCache(),
        player=FakePlayer(),
    )
    return orchestrator, tts


def _speak(orchestrator: VoiceOrchestrator, text: str, source: str = "ocr") -> None:
    orchestrator._handle_speak(
        DialogueEvent(event_type=EventType.SPEAK, raw_text=text, source=source)
    )


def test_noisy_reread_of_the_same_box_is_spoken_once(sample_profile: GameProfile) -> None:
    orchestrator, tts = _make(sample_profile)

    _speak(orchestrator, "Welcome to the Millennial Fair, traveler!")
    _speak(orchestrator, "Welcome to the Millennial Fair, traveIer!")

    assert len(tts.spoken) == 1


def test_lines_differing_only_in_numbers_are_both_spoken(
    sample_profile: GameProfile,
) -> None:
    orchestrator, tts = _make(sample_profile)

    _speak(orchestrator, "Got 200 G!")
    _speak(orchestrator, "Got 300 G!")

    assert tts.spoken == ["Got 200 G!", "Got 300 G!"]


def test_same_line_is_spoken_again_after_the_box_closed(
    sample_profile: GameProfile,
) -> None:
    orchestrator, tts = _make(sample_profile)

    _speak(orchestrator, "Welcome to the Millennial Fair!")
    # The OCR watcher reports the dialogue box closed
    orchestrator.submit_event(DialogueEvent(event_type=EventType.CLEAR_CONTEXT, source="ocr"))
    orchestrator._handle_event(*orchestrator._event_queue.get_nowait())
    _speak(orchestrator, "Welcome to the Millennial Fair!")

    assert len(tts.spoken) == 2


def test_non_ocr_sources_are_never_deduplicated(sample_profile: GameProfile) -> None:
    orchestrator, tts = _make(sample_profile)

    _speak(orchestrator, "CRONO: Let's go!", source="udp")
    _speak(orchestrator, "CRONO: Let's go!", source="udp")

    assert len(tts.spoken) == 2


class SlowTTS:
    """Yields audio in pieces and lets the test act between them."""

    def __init__(self, pieces: list[bytes], between=None) -> None:
        self.pieces = pieces
        self.between = between
        self.requested: list[str] = []

    def synthesize_stream(self, text: str, voice: VoiceProfile) -> Iterator[bytes]:
        self.requested.append(text)
        for index, piece in enumerate(self.pieces):
            yield piece
            if self.between:
                self.between(index)


class RecordingCache(FakeCache):
    def __init__(self) -> None:
        self.stored: list[bytes] = []

    def put(self, game_id: str, speaker: str, text: str, voice_id: str, data: bytes) -> None:
        self.stored.append(data)


class StreamingPlayer(FakePlayer):
    def __init__(self) -> None:
        self.stream_id = 0
        self.fed: list[bytes] = []
        self.whole_clips: list[bytes] = []
        self.stops = 0

    def begin_stream(self) -> int:
        self.stream_id += 1
        return self.stream_id

    def feed(self, stream_id: int, chunk: bytes) -> None:
        if stream_id == self.stream_id:
            self.fed.append(chunk)

    def play_bytes(self, audio_data, pitch_semitones=0.0, effect=AudioEffect.NONE) -> None:
        self.whole_clips.append(audio_data)

    def stop(self) -> None:
        self.stream_id += 1
        self.stops += 1


def _make_streaming(profile: GameProfile, tts) -> tuple[VoiceOrchestrator, StreamingPlayer, RecordingCache]:
    player, cache = StreamingPlayer(), RecordingCache()
    orchestrator = VoiceOrchestrator(
        game_id=profile.game_id,
        parser=DialogueParser(profile),
        tts=tts,
        cache=cache,
        player=player,
        stop_grace_period_s=0.0,
    )
    return orchestrator, player, cache


def _drain(orchestrator: VoiceOrchestrator) -> None:
    while not orchestrator._event_queue.empty():
        orchestrator._handle_event(*orchestrator._event_queue.get_nowait())


def _speak_event(text: str) -> DialogueEvent:
    return DialogueEvent(event_type=EventType.SPEAK, raw_text=text, source="udp")


class TestSkipping:
    def test_line_replaced_before_it_started_is_never_requested(
        self, sample_profile: GameProfile
    ) -> None:
        tts = SlowTTS([b"aa", b"bb"])
        orchestrator, _, _ = _make_streaming(sample_profile, tts)

        # Two boxes arrive before the worker gets to the first one
        orchestrator.submit_event(_speak_event("CRONO: First line."))
        orchestrator.submit_event(_speak_event("CRONO: Second line."))
        _drain(orchestrator)

        assert tts.requested == ["Second line."]

    def test_skip_pressed_before_a_line_starts_cancels_it(
        self, sample_profile: GameProfile
    ) -> None:
        tts = SlowTTS([b"aa"])
        orchestrator, _, _ = _make_streaming(sample_profile, tts)

        orchestrator.submit_event(_speak_event("CRONO: Never mind."))
        orchestrator.submit_event(DialogueEvent(event_type=EventType.STOP, source="input"))
        _drain(orchestrator)

        assert tts.requested == []

    def test_skipped_line_still_sets_the_speaker_for_following_boxes(
        self, sample_profile: GameProfile
    ) -> None:
        tts = SlowTTS([b"aa"])
        orchestrator, _, _ = _make_streaming(sample_profile, tts)

        orchestrator.submit_event(_speak_event("MARLE: Come on!"))
        orchestrator.submit_event(_speak_event("This way!"))
        _drain(orchestrator)

        assert orchestrator.parser._active_speaker == "MARLE"
        assert tts.requested == ["This way!"]

    def test_skip_during_download_stops_and_does_not_cache_the_partial_clip(
        self, sample_profile: GameProfile
    ) -> None:
        holder: dict = {}

        def press_skip(index: int) -> None:
            if index == 0:
                holder["o"].submit_event(DialogueEvent(event_type=EventType.STOP, source="input"))

        tts = SlowTTS([b"aa", b"bb", b"cc"], between=press_skip)
        orchestrator, player, cache = _make_streaming(sample_profile, tts)
        holder["o"] = orchestrator

        orchestrator.submit_event(_speak_event("CRONO: A long line."))
        _drain(orchestrator)

        assert player.fed == [b"aa"]
        assert cache.stored == []


class TestStreaming:
    def test_plain_voice_plays_while_audio_is_still_arriving(
        self, sample_profile: GameProfile
    ) -> None:
        heard_so_far: list[int] = []
        holder: dict = {}
        tts = SlowTTS([b"aa", b"bb", b"cc"], between=lambda _: heard_so_far.append(len(holder["p"].fed)))
        orchestrator, player, cache = _make_streaming(sample_profile, tts)
        holder["p"] = player

        orchestrator.submit_event(_speak_event("CRONO: Wait up!"))
        _drain(orchestrator)

        assert heard_so_far == [1, 2, 3]  # each piece reached the player as it arrived
        assert player.whole_clips == []
        assert cache.stored == [b"aabbcc"]

    def test_voice_with_pitch_or_effect_waits_for_the_whole_clip(
        self, sample_profile: GameProfile
    ) -> None:
        tts = SlowTTS([b"aa", b"bb"])
        orchestrator, player, cache = _make_streaming(sample_profile, tts)

        orchestrator.submit_event(_speak_event("ROBO: Systems operational."))
        _drain(orchestrator)

        assert player.fed == []
        assert player.whole_clips == [b"aabb"]
        assert cache.stored == [b"aabb"]

    def test_audio_format_is_part_of_the_cache_key(self, sample_profile: GameProfile) -> None:
        keys: list[str] = []

        class KeyCache(FakeCache):
            def get(self, game_id, speaker, text, voice_id):
                keys.append(voice_id)

        orchestrator = VoiceOrchestrator(
            game_id="g",
            parser=DialogueParser(sample_profile),
            tts=SlowTTS([b"aa"]),
            cache=KeyCache(),
            player=StreamingPlayer(),
            audio_tag="|pcm_24000",
        )
        orchestrator._handle_speak(_speak_event("CRONO: Hi there."))

        assert keys[0].endswith("|pcm_24000")
