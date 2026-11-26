# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Starts and stops a RetroBard session.

This is the one place that wires the pieces together (window capture, text reader,
voice service, audio output, skip input). The command line and a graphical
interface both drive it the same way: build a VoiceSession, `start()`, `stop()`,
and listen to status events.
"""

import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from retrobard.adapters.audio.pygame_out import PygameAudioPlayer
from retrobard.adapters.audio.stream_out import PcmStreamPlayer
from retrobard.adapters.cache.sqlite_disk import SqliteDiskCache
from retrobard.adapters.capture.win32_window import Win32WindowSource
from retrobard.adapters.ocr.glyph_reader import GlyphFont, GlyphReader
from retrobard.adapters.producers.ocr_watcher import OcrWatcher
from retrobard.adapters.producers.retroarch_memory import (
    RetroArchMemoryWatcher,
    rom_mismatch,
)
from retrobard.adapters.producers.skip_input import SkipInputWatcher
from retrobard.adapters.producers.udp_listener import UdpListener, load_or_create_token
from retrobard.adapters.producers.win32_input import make_key_reader, make_pad_reader
from retrobard.adapters.tts.elevenlabs_tts import ElevenLabsTTS
from retrobard.adapters.tts.mock_local import ConsoleMockTTS
from retrobard.adapters.tts.openai_compat_tts import OpenAICompatibleTTS
from retrobard.app.profile_updates import original_default_voices
from retrobard.config import (
    GAME_ID_HINT,
    AppSettings,
    CustomTTSConfig,
    GameProfile,
    RomPaths,
    is_valid_game_id,
)
from retrobard.credentials import custom_tts_api_key, elevenlabs_api_key
from retrobard.domain.parser import DialogueParser
from retrobard.domain.status import StatusKind, StatusSink, console_status, report
from retrobard.services.orchestrator import VoiceOrchestrator


class TTSBackend(str, Enum):
    MOCK = "mock"
    ELEVENLABS = "elevenlabs"
    # Any server speaking the OpenAI speech API (OpenAI, Kokoro-FastAPI, LocalAI...)
    CUSTOM = "custom"


class InputSource(str, Enum):
    OCR = "ocr"
    UDP = "udp"
    BOTH = "both"
    # The game's memory, read through RetroArch (needs a `memory:` profile section)
    MEMORY = "memory"


MB = 1_000_000


class SessionError(Exception):
    """Something the user can fix (no window linked, missing API key, port in use...)."""


def _checked_game(game: str) -> str:
    if not is_valid_game_id(game):
        raise SessionError(GAME_ID_HINT)
    return game


def profile_path(settings: AppSettings, game: str) -> Path:
    return settings.profiles_dir / f"{_checked_game(game)}.yaml"


def font_path(settings: AppSettings, game: str) -> Path:
    return settings.profiles_dir / f"{_checked_game(game)}.font.json"


def shipped_font_path(settings: AppSettings, game: str) -> Path | None:
    """The letters shipped with the packaged app, if any (read-only)."""
    if settings.bundled_profiles_dir is None:
        return None
    path = settings.bundled_profiles_dir / f"{game}.font.json"
    if not path.exists() or path.resolve() == font_path(settings, game).resolve():
        return None
    return path


def load_font_layers(settings: AppSettings, game: str) -> tuple[GlyphFont | None, GlyphFont]:
    """(shipped letters or None, the user's own letters)."""
    shipped_path = shipped_font_path(settings, game)
    shipped = GlyphFont.load(shipped_path) if shipped_path else None
    return shipped, GlyphFont.load(font_path(settings, game))


def load_font(settings: AppSettings, game: str) -> GlyphFont:
    """Every letter known for the game: shipped ones plus the user's own."""
    return GlyphFont.layered(*load_font_layers(settings, game))


def load_profile(settings: AppSettings, game: str) -> GameProfile:
    try:
        return GameProfile.from_yaml(profile_path(settings, game))
    except FileNotFoundError as exc:
        raise SessionError(f"There is no profile for the game '{game}' yet.") from exc


def needs_rom(profile: GameProfile) -> bool:
    """Whether the game's text is read from its ROM (memory source only)."""
    return profile.memory is not None and profile.memory.text.rom is not None


def saved_rom_path(settings: AppSettings, game: str) -> Path | None:
    """The ROM the user chose for this game, if it is still there."""
    try:
        path = RomPaths.load(settings.rom_paths_file).get(game)
    except (OSError, ValueError):
        return None
    return path if path is not None and path.is_file() else None


def choose_rom(settings: AppSettings, game: str, path: Path) -> None:
    """Remembers `path` as this user's ROM for `game`, after checking it is the
    exact game version the profile was made for."""
    profile = load_profile(settings, game)
    if not needs_rom(profile):
        raise SessionError(f"The profile for '{game}' does not read text from a ROM.")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SessionError(f"Could not read {path}: {exc}") from exc
    problem = rom_mismatch(data, profile.memory.text.rom, path.name)
    if problem:
        raise SessionError(problem)
    try:
        paths = RomPaths.load(settings.rom_paths_file)
    except (OSError, ValueError):
        paths = RomPaths()  # unreadable: start again rather than refuse
    paths.set(game, path.resolve())
    paths.save(settings.rom_paths_file)


def load_custom_tts(settings: AppSettings) -> CustomTTSConfig:
    try:
        return CustomTTSConfig.load(settings.custom_tts_file)
    except (OSError, ValueError) as exc:
        raise SessionError(
            f"The custom voice server settings in {settings.custom_tts_file} could not be "
            f"read ({exc}). Open them again from the Play tab."
        ) from exc


def profile_for_backend(
    settings: AppSettings, profile: GameProfile, tts_backend: TTSBackend
) -> GameProfile:
    """The profile with the voices of the chosen service: ElevenLabs voice IDs mean
    nothing to a custom server, which uses `custom_voices` instead."""
    if tts_backend != TTSBackend.CUSTOM:
        return profile
    server = load_custom_tts(settings)
    return profile.model_copy(
        update={"voices": profile.voices_for_custom_server(server.default_voice)}
    )


def _audio_output(fmt: str, sample_rate: int | None, settings: AppSettings, tag: str):
    """(player, cache, cache key tag) for clips in the given format."""
    max_bytes = settings.cache_max_mb * MB
    if sample_rate is not None:
        # Raw samples can be played while they are still arriving
        player = PcmStreamPlayer(sample_rate=sample_rate)
        return player, SqliteDiskCache(settings.cache_dir, file_ext="pcm", max_bytes=max_bytes), tag
    cache = SqliteDiskCache(settings.cache_dir, file_ext=fmt, max_bytes=max_bytes)
    return PygameAudioPlayer(), cache, tag


def build_orchestrator(
    settings: AppSettings,
    profile: GameProfile,
    tts_backend: TTSBackend,
    log_text: bool = False,
    status: StatusSink = console_status,
) -> VoiceOrchestrator:
    """Builds the parser -> cache -> voice -> audio pipeline for one game.

    For the custom server, pass a profile from `profile_for_backend` so the
    server's own voice names are used."""
    if tts_backend == TTSBackend.CUSTOM:
        server = load_custom_tts(settings)
        tts = OpenAICompatibleTTS(server, api_key=custom_tts_api_key(settings))
        pcm_rate = server.sample_rate if server.response_format == "pcm" else None
        # Lines from different servers or models must never be mixed up in the cache
        tag = f"|custom|{server.base_url}|{server.model}|{server.response_format}"
        if pcm_rate is not None:
            tag += f"_{pcm_rate}"
        player, cache, audio_tag = _audio_output(server.response_format, pcm_rate, settings, tag)
    else:
        audio_format = settings.elevenlabs_output_format
        if audio_format.startswith("pcm_"):
            pcm_rate = int(audio_format.split("_", 1)[1])
            player, cache, audio_tag = _audio_output("pcm", pcm_rate, settings, f"|{audio_format}")
        else:
            player, cache, audio_tag = _audio_output("mp3", None, settings, "")

        if tts_backend == TTSBackend.ELEVENLABS:
            try:
                tts = ElevenLabsTTS(
                    api_key=elevenlabs_api_key(settings),
                    model_id=settings.elevenlabs_model_id,
                    output_format=audio_format,
                    fallback_voices=original_default_voices(),
                    status=status,
                )
            except ValueError as exc:
                raise SessionError(str(exc)) from exc
        else:
            tts = ConsoleMockTTS(status=status)

    return VoiceOrchestrator(
        game_id=profile.game_id,
        parser=DialogueParser(profile, status=status),
        tts=tts,
        cache=cache,
        player=player,
        log_text=log_text,
        audio_tag=audio_tag,
        status=status,
    )


@dataclass(frozen=True)
class SessionOptions:
    game: str
    tts: TTSBackend = TTSBackend.MOCK
    source: InputSource = InputSource.OCR
    # Also report the dialogue text and troubleshooting details
    verbose: bool = False


class VoiceSession:
    def __init__(
        self,
        settings: AppSettings,
        options: SessionOptions,
        status: StatusSink = console_status,
    ):
        self.settings = settings
        self.options = options
        self.status = status
        self.profile: GameProfile | None = None

        self._running = False
        self._orchestrator: VoiceOrchestrator | None = None
        self._worker: threading.Thread | None = None
        self._game_window: Win32WindowSource | None = None
        self._ocr_watcher: OcrWatcher | None = None
        self._udp_listener: UdpListener | None = None
        self._memory_watcher: RetroArchMemoryWatcher | None = None
        self._skip_input: SkipInputWatcher | None = None

    @property
    def running(self) -> bool:
        return self._running

    def _info(self, message: str) -> None:
        report(self.status, StatusKind.INFO, message)

    def start(self) -> None:
        """Starts everything. Raises SessionError, leaving nothing running, if the
        session cannot start for a reason the user can fix."""
        if self._running:
            return
        try:
            self._start()
        except BaseException:
            self.stop()
            raise
        self._running = True
        report(
            self.status,
            StatusKind.SESSION,
            f"RetroBard active for [{self.profile.game_id}] "
            f"(TTS={self.options.tts.value}, Source={self.options.source.value})",
            running=True,
        )

    def _start(self) -> None:
        options, settings = self.options, self.settings
        game = options.game
        profile = self.profile = profile_for_backend(
            settings, load_profile(settings, game), options.tts
        )
        orchestrator = self._orchestrator = build_orchestrator(
            settings, profile, options.tts, log_text=options.verbose, status=self.status
        )

        # The only thing RetroBard may look at / take skip input from is this window
        if profile.window:
            self._game_window = Win32WindowSource(profile.window, status=self.status)

        if options.source in (InputSource.OCR, InputSource.BOTH):
            self._ocr_watcher = self._build_watcher(profile, game)

        # Open the connection to the voice service now, so the first line is not delayed by it
        warm_up = getattr(orchestrator.tts, "warm_up", None)
        if warm_up:
            threading.Thread(target=warm_up, daemon=True).start()

        self._worker = threading.Thread(target=orchestrator.run_forever, daemon=True)
        self._worker.start()
        if self._ocr_watcher:
            self._ocr_watcher.start(orchestrator.submit_event)

        if options.source in (InputSource.UDP, InputSource.BOTH):
            self._start_udp(orchestrator)

        if options.source == InputSource.MEMORY:
            self._start_memory(profile, orchestrator)

        if self._game_window and profile.controls.enabled:
            self._skip_input = SkipInputWatcher(
                profile.controls,
                is_game_focused=self._game_window.is_foreground,
                read_keys=make_key_reader(profile.controls.skip_keys, status=self.status),
                read_pad_buttons=make_pad_reader(profile.controls.skip_gamepad_buttons),
                debug=options.verbose,
                status=self.status,
            )
            self._skip_input.start(orchestrator.submit_event)
            self._info(
                f"Skip input (only while the game is focused): keys={profile.controls.skip_keys}, "
                f"gamepad_buttons={profile.controls.skip_gamepad_buttons}"
            )
        elif profile.controls.enabled:
            self._info("Skip input disabled: no game window is linked (run calibrate).")

    def _build_watcher(self, profile: GameProfile, game: str) -> OcrWatcher:
        if self._game_window is None or profile.ocr.region is None:
            raise SessionError(
                f"No game window is linked for '{game}' yet. "
                f"Run: retrobard calibrate --game {game}"
            )
        self._info(f"Reading only the game window: {self._game_window.describe()}")
        if not self._game_window.is_available():
            self._info("  (window not open yet - waiting for it to appear)")

        font = load_font(self.settings, game)
        use_glyphs = profile.ocr.engine == "glyph" or (
            profile.ocr.engine == "auto" and font.is_usable
        )
        if use_glyphs and not font.is_usable:
            raise SessionError(
                f"No letters have been taught for '{game}' yet. "
                f"Run: retrobard teach --game {game}"
            )
        if use_glyphs:
            self._info(f"Text engine: taught game font ({len(font.glyphs)} letter shapes)")
            glyph_reader = GlyphReader(profile.ocr, font)
        else:
            self._info("Text engine: EasyOCR (run `retrobard teach` for exact, faster reading)")
            glyph_reader = None
        try:
            return OcrWatcher(
                profile.ocr,
                self._game_window,
                glyph_reader=glyph_reader,
                debug=self.options.verbose,
                status=self.status,
            )
        except ImportError as exc:
            raise SessionError(
                f"No letters have been taught for '{game}' and the EasyOCR fallback is not "
                f"installed. Run: retrobard teach --game {game} "
                f"(or install the 'easyocr' extra)."
            ) from exc

    def _start_udp(self, orchestrator: VoiceOrchestrator) -> None:
        settings = self.settings
        token_path = settings.cache_dir / "udp_token"
        token = settings.udp_token or load_or_create_token(token_path)
        try:
            self._udp_listener = UdpListener(
                settings.udp_host, settings.udp_port, token, status=self.status
            )
        except (ValueError, OSError) as exc:
            raise SessionError(f"Could not start the UDP listener: {exc}") from exc
        self._udp_listener.start(orchestrator.submit_event)
        token_source = "UDP_TOKEN in .env" if settings.udp_token else f"file {token_path}"
        self._info(
            f"Listening for UDP packets on udp://{self._udp_listener.host}:{self._udp_listener.port} "
            f"(this computer only; packets must carry the token from {token_source})"
        )

    def _start_memory(self, profile: GameProfile, orchestrator: VoiceOrchestrator) -> None:
        if profile.memory is None:
            raise SessionError(
                f"The profile for '{profile.game_id}' has no `memory:` section, so its "
                f"text cannot be read from memory. Read it from the game window instead."
            )
        rom_path = None
        if needs_rom(profile):
            rom_path = saved_rom_path(self.settings, profile.game_id)
            if rom_path is None:
                raise SessionError(
                    f"'{profile.game_id}' reads its text from the game's ROM, but no ROM "
                    f"has been chosen yet (or it was moved). Press Start in the window to "
                    f"choose it, or run: retrobard set-rom --game {profile.game_id} <file>"
                )
        # A character table shipped with the app is used unless the user has their own
        table_dir = self.settings.profiles_dir
        table = profile.memory.text.table_file
        bundled = self.settings.bundled_profiles_dir
        if table and bundled and not (table_dir / table).exists():
            table_dir = bundled
        try:
            self._memory_watcher = RetroArchMemoryWatcher(
                profile.memory,
                profile_dir=table_dir,
                rom_path=rom_path,
                debug=self.options.verbose,
                status=self.status,
            )
        except (ValueError, OSError) as exc:
            raise SessionError(f"Could not set up reading from RetroArch: {exc}") from exc
        self._memory_watcher.start(orchestrator.submit_event)
        self._info(
            f"Reading dialogue from RetroArch's memory on udp://{profile.memory.host}:"
            f"{profile.memory.port} (read only)"
        )

    def stop(self) -> None:
        """Stops everything that was started. Safe to call more than once."""
        was_running = self._running
        self._running = False

        if self._skip_input:
            self._skip_input.stop()
        if self._ocr_watcher:
            self._ocr_watcher.stop()
        if self._udp_listener:
            self._udp_listener.stop()
        if self._memory_watcher:
            self._memory_watcher.stop()
        if self._orchestrator:
            self._orchestrator.shutdown()
            if self._worker and self._worker.is_alive():
                self._worker.join(timeout=2.0)
            close_player = getattr(self._orchestrator.player, "close", None)
            if close_player:
                close_player()
            close_tts = getattr(self._orchestrator.tts, "close", None)
            if close_tts:
                close_tts()
        if self._game_window:
            self._game_window.close()

        self._skip_input = self._ocr_watcher = self._udp_listener = None
        self._memory_watcher = None
        self._orchestrator = self._worker = self._game_window = None
        if was_running:
            report(self.status, StatusKind.SESSION, "RetroBard stopped.", running=False)
