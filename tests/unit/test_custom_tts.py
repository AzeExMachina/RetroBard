# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""The custom (OpenAI-compatible) voice server."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no real window during tests

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from test_session import SilentPlayer

from retrobard.adapters.tts.openai_compat_tts import CustomTTSError, OpenAICompatibleTTS
from retrobard.app import session as session_module
from retrobard.app.session import (
    SessionError,
    TTSBackend,
    build_orchestrator,
    load_profile,
    profile_for_backend,
)
from retrobard.config import (
    AppSettings,
    CustomTTSConfig,
    CustomVoicesConfig,
    GameProfile,
)
from retrobard.credentials import (
    CUSTOM_ACCOUNT,
    custom_tts_api_key,
    store_api_key,
    stored_api_key,
)
from retrobard.domain.models import VoiceProfile
from retrobard.domain.parser import DialogueParser


def _tts(handler, config: CustomTTSConfig | None = None, key: str = "") -> OpenAICompatibleTTS:
    return OpenAICompatibleTTS(
        config or CustomTTSConfig(), api_key=key, transport=httpx.MockTransport(handler)
    )


@pytest.fixture
def settings(tmp_path: Path, sample_profile: GameProfile, monkeypatch) -> AppSettings:
    monkeypatch.setattr(session_module, "PcmStreamPlayer", SilentPlayer)
    app_settings = AppSettings(
        _env_file=None,
        cache_dir=tmp_path / "cache",
        profiles_dir=tmp_path / "profiles",
        custom_tts_file=tmp_path / "custom_tts.json",
    )
    sample_profile.save_to_yaml(app_settings.profiles_dir / "chrono_trigger.yaml")
    return app_settings


class TestAdapter:
    def test_sends_the_openai_request_and_streams_the_audio(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, content=b"\x01\x02" * 100)

        config = CustomTTSConfig(base_url="http://localhost:8880/v1/", model="kokoro")
        audio = b"".join(
            _tts(handler, config, key="secret").synthesize_stream(
                "Hello", VoiceProfile(voice_id="am_adam", speed=1.1)
            )
        )

        assert audio == b"\x01\x02" * 100
        request = seen[0]
        assert str(request.url) == "http://localhost:8880/v1/audio/speech"
        assert request.headers["Authorization"] == "Bearer secret"
        assert json.loads(request.content) == {
            "model": "kokoro",
            "voice": "am_adam",
            "input": "Hello",
            "response_format": "pcm",
            "speed": 1.1,
        }

    def test_normal_speed_and_no_key_send_nothing_extra(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, content=b"ab")

        list(_tts(handler).synthesize_stream("Hi", VoiceProfile(voice_id="af_heart")))

        assert "Authorization" not in seen[0].headers
        assert "speed" not in json.loads(seen[0].content)

    def test_server_error_message_is_reported(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "Unknown voice 'zz'"}})

        with pytest.raises(CustomTTSError, match="400: Unknown voice 'zz'"):
            list(_tts(handler).synthesize_stream("Hi", VoiceProfile(voice_id="zz")))

    def test_unreachable_server(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(CustomTTSError, match="Could not reach"):
            list(_tts(handler).synthesize_stream("Hi", VoiceProfile(voice_id="v")))
        assert _tts(handler).warm_up() is None


class TestConfig:
    def test_rejects_addresses_that_are_not_http(self) -> None:
        with pytest.raises(ValidationError):
            CustomTTSConfig(base_url="file:///etc/passwd")
        with pytest.raises(ValidationError):
            CustomTTSConfig(default_voice="  ")

    def test_local_addresses(self) -> None:
        assert CustomTTSConfig(base_url="http://127.0.0.1:8880/v1").is_local()
        assert not CustomTTSConfig(base_url="https://api.openai.com/v1").is_local()

    def test_saves_and_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "custom.json"
        assert CustomTTSConfig.load(path) == CustomTTSConfig()
        config = CustomTTSConfig(model="tts-1", default_voice="nova", response_format="mp3")
        config.save(path)
        assert CustomTTSConfig.load(path) == config

    def test_key_is_kept_apart_from_the_elevenlabs_key(self, settings: AppSettings) -> None:
        store_api_key("el-key")
        store_api_key("custom-key", CUSTOM_ACCOUNT)
        assert stored_api_key() == "el-key"
        assert custom_tts_api_key(settings) == "custom-key"


class TestVoices:
    def test_without_custom_voices_everyone_uses_the_default_voice(
        self, sample_profile: GameProfile
    ) -> None:
        voices = sample_profile.voices_for_custom_server("af_heart")

        assert voices.default_narrator == "af_heart"
        # Characters stay known, so speaker names are still recognised
        assert set(voices.characters) == set(sample_profile.voices.characters)
        assert voices.resolve_character("CRONO").voice_id == "af_heart"
        assert voices.female_pool == [] and voices.male_pool == []

    def test_custom_voices_override(self, sample_profile: GameProfile) -> None:
        sample_profile.custom_voices = CustomVoicesConfig(
            default_narrator="bm_george", characters={"CRONO": "am_adam"}
        )
        voices = sample_profile.voices_for_custom_server("af_heart")

        assert voices.default_narrator == "bm_george"
        assert voices.resolve_character("CRONO").voice_id == "am_adam"
        assert voices.resolve_character("MARLE").voice_id == "bm_george"

    def test_custom_voices_load_from_yaml(self, tmp_path: Path, sample_profile: GameProfile) -> None:
        path = tmp_path / "game.yaml"
        sample_profile.save_to_yaml(path)
        assert "custom_voices" not in path.read_text(encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write("custom_voices:\n  characters:\n    crono: am_adam\n")

        loaded = GameProfile.from_yaml(path)
        assert loaded.custom_voices.characters == {"CRONO": "am_adam"}

    def test_parser_picks_the_custom_voice(self, settings: AppSettings) -> None:
        CustomTTSConfig(default_voice="af_heart").save(settings.custom_tts_file)
        profile = profile_for_backend(
            settings, load_profile(settings, "chrono_trigger"), TTSBackend.CUSTOM
        )
        parsed = DialogueParser(profile).parse("CRONO: Wait up!")
        assert parsed.speaker == "CRONO"
        assert parsed.voice_id == "af_heart"


class TestSession:
    def test_builds_a_custom_pipeline(self, settings: AppSettings) -> None:
        CustomTTSConfig(model="tts-1").save(settings.custom_tts_file)
        profile = profile_for_backend(
            settings, load_profile(settings, "chrono_trigger"), TTSBackend.CUSTOM
        )
        orchestrator = build_orchestrator(settings, profile, TTSBackend.CUSTOM)

        assert isinstance(orchestrator.tts, OpenAICompatibleTTS)
        assert orchestrator.audio_tag.startswith("|custom|http://localhost:8880/v1|tts-1|pcm")
        orchestrator.tts.close()

    def test_unreadable_settings_file(self, settings: AppSettings) -> None:
        settings.custom_tts_file.write_text("{not json", encoding="utf-8")
        with pytest.raises(SessionError, match="could not be read"):
            profile_for_backend(
                settings, load_profile(settings, "chrono_trigger"), TTSBackend.CUSTOM
            )


class TestDialog:
    def test_saves_settings_and_key(self, settings: AppSettings) -> None:
        from PySide6.QtWidgets import QApplication

        from retrobard.gui.custom_server_dialog import CustomServerDialog

        _app = QApplication.instance() or QApplication([])
        dialog = CustomServerDialog(settings)
        dialog.url_edit.setText("ftp://nope")
        dialog.save()
        assert "Server address" in dialog.message_label.text()
        assert not settings.custom_tts_file.exists()

        dialog.preset_combo.setCurrentIndex(dialog.preset_combo.findData("OpenAI"))
        dialog.key_edit.setText("sk-test")
        dialog.save()

        saved = CustomTTSConfig.load(settings.custom_tts_file)
        assert saved.base_url == "https://api.openai.com/v1"
        assert stored_api_key(CUSTOM_ACCOUNT) == "sk-test"

    def test_warns_before_sending_a_key_unencrypted(self, settings: AppSettings) -> None:
        from PySide6.QtWidgets import QApplication

        from retrobard.gui.custom_server_dialog import CustomServerDialog

        _app = QApplication.instance() or QApplication([])
        dialog = CustomServerDialog(settings)
        dialog.url_edit.setText("http://192.168.1.20:8880/v1")
        dialog.key_edit.setText("secret")
        dialog.save()
        assert "unencrypted" in dialog.message_label.text()
        assert not settings.custom_tts_file.exists()
        dialog.save()  # second press goes ahead
        assert settings.custom_tts_file.exists()
