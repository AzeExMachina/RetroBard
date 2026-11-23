# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import socket
import time
from pathlib import Path

import numpy as np
import pytest

from retrobard.app import session as session_module
from retrobard.app import setup
from retrobard.app.session import (
    InputSource,
    SessionError,
    SessionOptions,
    TTSBackend,
    VoiceSession,
)
from retrobard.config import AppSettings, GameProfile, WindowBinding
from retrobard.domain.models import AudioEffect
from retrobard.domain.status import StatusEvent, StatusKind

TOKEN = "session-test-token"


class SilentPlayer:
    """Stands in for the real audio output so tests never open a sound device."""

    def __init__(self, sample_rate: int = 24000) -> None:
        self.sample_rate = sample_rate
        self.closed = False

    def begin_stream(self) -> int:
        return 1

    def feed(self, stream_id: int, chunk: bytes) -> None:
        pass

    def play_bytes(self, audio_data, pitch_semitones=0.0, effect=AudioEffect.NONE) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_busy(self) -> bool:
        return False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def settings(tmp_path: Path, sample_profile: GameProfile, monkeypatch) -> AppSettings:
    monkeypatch.setattr(session_module, "PcmStreamPlayer", SilentPlayer)
    app_settings = AppSettings(
        _env_file=None,
        cache_dir=tmp_path / "cache",
        profiles_dir=tmp_path / "profiles",
        custom_tts_file=tmp_path / "custom_tts.json",
        udp_port=0,
        udp_token=TOKEN,
    )
    sample_profile.save_to_yaml(app_settings.profiles_dir / "chrono_trigger.yaml")
    return app_settings


def _wait_for(condition, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def _udp_session(settings: AppSettings, events: list[StatusEvent]) -> VoiceSession:
    return VoiceSession(
        settings,
        SessionOptions(game="chrono_trigger", tts=TTSBackend.MOCK, source=InputSource.UDP),
        status=events.append,
    )


def _send(session: VoiceSession, text: str) -> None:
    listener = session._udp_listener
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(
            json.dumps({"token": TOKEN, "text": text}).encode(), (listener.host, listener.port)
        )


class TestSessionLifecycle:
    def test_start_voice_a_line_and_stop(self, settings: AppSettings) -> None:
        events: list[StatusEvent] = []
        session = _udp_session(settings, events)

        session.start()
        assert session.running
        _send(session, "MARLE: Come on, this way!")

        assert _wait_for(lambda: any(e.kind == StatusKind.LINE for e in events))
        line = next(e for e in events if e.kind == StatusKind.LINE)
        assert line.data["speaker"] == "MARLE"
        assert line.data["text"] == "Come on, this way!"
        # Dialogue text is carried for a display, but not printed unless asked for
        assert "Come on" not in line.message

        worker = session._worker
        session.stop()
        assert not session.running
        assert not worker.is_alive()
        assert [e.data["running"] for e in events if e.kind == StatusKind.SESSION] == [True, False]

    def test_session_can_be_started_again_after_stopping(self, settings: AppSettings) -> None:
        events: list[StatusEvent] = []
        session = _udp_session(settings, events)

        session.start()
        session.stop()
        session.start()
        _send(session, "CRONO: Second run.")

        assert _wait_for(lambda: any(e.kind == StatusKind.LINE for e in events))
        session.stop()
        session.stop()  # stopping twice is harmless

    def test_stop_closes_the_audio_output(self, settings: AppSettings) -> None:
        session = _udp_session(settings, [])
        session.start()
        player = session._orchestrator.player
        session.stop()
        assert player.closed


class TestStartFailures:
    def test_unknown_game(self, settings: AppSettings) -> None:
        session = VoiceSession(settings, SessionOptions(game="no_such_game"), status=lambda _: None)
        with pytest.raises(SessionError, match="no profile"):
            session.start()
        assert not session.running

    def test_reading_the_screen_needs_a_linked_window(self, settings: AppSettings) -> None:
        session = VoiceSession(
            settings,
            SessionOptions(game="chrono_trigger", source=InputSource.OCR),
            status=lambda _: None,
        )
        with pytest.raises(SessionError, match="No game window is linked"):
            session.start()
        assert not session.running
        assert session._worker is None

    def test_missing_api_key(self, settings: AppSettings) -> None:
        session = VoiceSession(
            settings,
            SessionOptions(game="chrono_trigger", tts=TTSBackend.ELEVENLABS, source=InputSource.UDP),
            status=lambda _: None,
        )
        with pytest.raises(SessionError, match="ELEVENLABS_API_KEY"):
            session.start()

    def test_udp_port_already_in_use_leaves_nothing_running(self, settings: AppSettings) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            busy = settings.model_copy(update={"udp_port": occupied.getsockname()[1]})
            session = _udp_session(busy, [])

            with pytest.raises(SessionError, match="UDP listener"):
                session.start()

        assert not session.running
        assert session._worker is None
        assert session._orchestrator is None


class TestSetupFunctions:
    def test_games_are_listed_from_profiles(self, settings: AppSettings) -> None:
        assert setup.list_games(settings) == ["chrono_trigger"]

    def test_new_game_gets_a_default_profile(self, settings: AppSettings) -> None:
        profile = setup.load_or_create_profile(settings, "new_game")
        assert profile.game_id == "new_game"
        assert profile.window is None

        profile.window = WindowBinding(process_name="emulator.exe")
        setup.set_dialogue_region(profile, 64, 300, 512, 120, frame_w=640, frame_h=480)
        setup.save_profile(settings, "new_game", profile)

        reloaded = setup.load_or_create_profile(settings, "new_game")
        assert reloaded.window.process_name == "emulator.exe"
        assert reloaded.ocr.region.to_pixels(640, 480) == (64, 300, 576, 420)
        assert setup.list_games(settings) == ["chrono_trigger", "new_game"]

    def test_assigning_a_voice_saves_it_and_drops_cached_lines(
        self, settings: AppSettings
    ) -> None:
        from retrobard.adapters.cache.sqlite_disk import SqliteDiskCache

        cache = SqliteDiskCache(settings.cache_dir)
        cache.put("chrono_trigger", "FROG", "Line 1", "old_voice", b"audio")
        profile = setup.load_or_create_profile(settings, "chrono_trigger")

        removed = setup.assign_voice(settings, "chrono_trigger", profile, "frog", "new_voice")

        assert removed == 1
        reloaded = setup.load_or_create_profile(settings, "chrono_trigger")
        assert reloaded.voices.resolve_character("FROG").voice_id == "new_voice"

    def test_flat_colour_frame_counts_as_blank(self) -> None:
        blank = np.zeros((10, 10, 4), dtype=np.uint8)
        picture = blank.copy()
        picture[5, 5, :3] = 200
        assert setup.is_blank(blank)
        assert not setup.is_blank(picture)
