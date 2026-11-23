# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import queue
import re
import threading
import time
import traceback
from difflib import SequenceMatcher

from retrobard.domain.models import AudioEffect, DialogueEvent, EventType
from retrobard.domain.parser import DialogueParser
from retrobard.domain.ports import AudioCache, AudioPlayer, TTSProvider
from retrobard.domain.status import StatusKind, StatusSink, console_status, report

_NUMBER_RE = re.compile(r"\d+")


class VoiceOrchestrator:
    def __init__(
        self,
        game_id: str,
        parser: DialogueParser,
        tts: TTSProvider,
        cache: AudioCache,
        player: AudioPlayer,
        stop_grace_period_s: float = 0.25,
        log_text: bool = False,
        audio_tag: str = "",
        status: StatusSink = console_status,
    ):
        # Dialogue text is only written to the console when explicitly asked for
        self.log_text = log_text
        self.status = status
        self.game_id = game_id
        self.parser = parser
        self.tts = tts
        self.cache = cache
        self.player = player
        self.stop_grace_period_s = stop_grace_period_s
        # Added to cache keys so audio in one format is never played as another
        self.audio_tag = audio_tag

        self._event_queue: queue.Queue[tuple[DialogueEvent | None, int | None]] = queue.Queue()
        self._last_spoken_text = ""
        self._last_speak_start = 0.0

        # Every spoken line gets a number. A line is cancelled once its number is
        # at or below `_cancelled_upto`; nothing ever "un-cancels" a line, so a
        # skipped line can never be synthesized by mistake.
        self._lock = threading.Lock()
        self._line_number = 0
        self._cancelled_upto = 0

    def submit_event(self, event: DialogueEvent) -> None:
        if event.event_type == EventType.SPEAK:
            with self._lock:
                self._line_number += 1
                line = self._line_number
                self._cancelled_upto = line - 1  # a new line replaces all earlier ones
                self._last_speak_start = time.monotonic()
            self.player.stop()
            self._event_queue.put((event, line))

        elif event.event_type == EventType.STOP:
            with self._lock:
                if (time.monotonic() - self._last_speak_start) < self.stop_grace_period_s:
                    return
                self._cancelled_upto = self._line_number
            self.player.stop()

        elif event.event_type == EventType.CLEAR_CONTEXT:
            self._event_queue.put((event, None))

    def _is_cancelled(self, line: int | None) -> bool:
        if line is None:
            return False
        with self._lock:
            return line <= self._cancelled_upto

    def run_forever(self) -> None:
        """Handles queued events until `shutdown` is called."""
        while True:
            event, line = self._event_queue.get()
            if event is None:
                return
            try:
                self._handle_event(event, line)
            except Exception:  # noqa: BLE001 - one bad line must not stop the queue
                report(
                    self.status,
                    StatusKind.ERROR,
                    "[ORCHESTRATOR ERROR] Failed handling a line:\n" + traceback.format_exc(),
                )

    def shutdown(self) -> None:
        """Cancels whatever is playing or queued and ends `run_forever`."""
        with self._lock:
            self._cancelled_upto = self._line_number
        self.player.stop()
        self._event_queue.put((None, None))

    def _handle_event(self, event: DialogueEvent, line: int | None = None) -> None:
        if event.event_type == EventType.SPEAK:
            self._handle_speak(event, line)
        elif event.event_type == EventType.CLEAR_CONTEXT:
            # The dialogue box closed, so the same line may legitimately recur
            self.parser.reset_context()
            self._last_spoken_text = ""

    def _handle_speak(self, event: DialogueEvent, line: int | None = None) -> None:
        # Always parse, even a line that was already skipped: who is speaking
        # carries over to the following boxes.
        parsed = self.parser.parse(event.raw_text, event.explicit_speaker)
        if not parsed:
            if self.log_text:
                report(
                    self.status,
                    StatusKind.DEBUG,
                    f"[PARSER] Ignored empty/short text: {event.raw_text!r}",
                )
            return

        # Only OCR re-reads the same box; other sources send each line on purpose
        if event.source == "ocr" and self._is_duplicate(parsed.clean_text):
            return
        self._last_spoken_text = parsed.clean_text

        if self._is_cancelled(line):
            report(
                self.status,
                StatusKind.LINE_SKIPPED,
                "  -> [SKIPPED] Line was skipped before it started; nothing requested",
                reason="before_start",
            )
            return

        voice = parsed.voice
        voice_sig = voice.signature() + self.audio_tag
        text_part = (
            f"Text={parsed.clean_text!r}" if self.log_text else f"{len(parsed.clean_text)} chars"
        )
        cached_audio = self.cache.get(self.game_id, parsed.speaker, parsed.clean_text, voice_sig)
        # The text travels in the event for a display to show; the printed message
        # only contains it when text logging was asked for.
        report(
            self.status,
            StatusKind.LINE,
            f"[VOICE] Speaker={parsed.speaker} | Voice={parsed.voice_id[:8]}.. "
            f"(spd={voice.speed}, pitch={voice.pitch_semitones:+.1f}st, "
            f"fx={voice.effect.value}) | {text_part}"
            + ("\n  -> [CACHE HIT] Playing from SQLite/Disk cache" if cached_audio else ""),
            speaker=parsed.speaker,
            voice_id=parsed.voice_id,
            text=parsed.clean_text,
            chars=len(parsed.clean_text),
            cached=bool(cached_audio),
        )
        if cached_audio:
            if not self._is_cancelled(line):
                self.player.play_bytes(
                    cached_audio, pitch_semitones=voice.pitch_semitones, effect=voice.effect
                )
            return

        # Pitch shifting and effects need the whole clip, so only plain voices can
        # start playing while the audio is still arriving.
        needs_whole_clip = abs(voice.pitch_semitones) >= 0.05 or voice.effect != AudioEffect.NONE
        begin_stream = getattr(self.player, "begin_stream", None)
        stream_id = begin_stream() if begin_stream and not needs_whole_clip else None

        audio = bytearray()
        for chunk in self.tts.synthesize_stream(parsed.clean_text, voice):
            if self._is_cancelled(line):
                report(
                    self.status,
                    StatusKind.LINE_SKIPPED,
                    "  -> [CANCELLED] Stream aborted by user skip",
                    reason="during_download",
                )
                return
            audio += chunk
            if stream_id is not None:
                self.player.feed(stream_id, chunk)

        if not audio or self._is_cancelled(line):
            return
        self.cache.put(self.game_id, parsed.speaker, parsed.clean_text, voice_sig, bytes(audio))
        if stream_id is None:
            self.player.play_bytes(
                bytes(audio), pitch_semitones=voice.pitch_semitones, effect=voice.effect
            )

    def _is_duplicate(self, text: str) -> bool:
        """True if `text` is a noisy re-read of the box that was just spoken."""
        if not self._last_spoken_text:
            return False
        # "Got 200 G!" and "Got 300 G!" are different lines, not OCR jitter
        if _NUMBER_RE.findall(text) != _NUMBER_RE.findall(self._last_spoken_text):
            return False
        return SequenceMatcher(None, text, self._last_spoken_text).ratio() > 0.88
