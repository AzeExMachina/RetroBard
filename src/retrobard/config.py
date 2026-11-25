# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from retrobard.domain.models import AudioEffect, VoiceProfile
from retrobard.paths import bundle_dir, is_frozen, user_data_dir

# Game names: also file names of the profile, letters and cache folder
GAME_ID_PATTERN = r"^[a-z0-9_]{1,64}$"
GAME_ID_HINT = "Game names may only use a-z, 0-9 and _."
MAX_STRIP_PATTERN_LENGTH = 200


def is_valid_game_id(game: str) -> bool:
    return re.fullmatch(GAME_ID_PATTERN, game) is not None


class NormRect(BaseModel):
    """A rectangle expressed as fractions (0-1) of the game window's client area.

    Storing fractions instead of screen pixels means the region follows the game
    window when it is moved, resized, or put on another monitor.
    """

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)

    @classmethod
    def from_pixels(
        cls, x: int, y: int, w: int, h: int, frame_w: int, frame_h: int
    ) -> "NormRect":
        return cls(
            x=round(min(1.0, max(0.0, x / frame_w)), 4),
            y=round(min(1.0, max(0.0, y / frame_h)), 4),
            w=round(min(1.0, max(1.0 / frame_w, w / frame_w)), 4),
            h=round(min(1.0, max(1.0 / frame_h, h / frame_h)), 4),
        )

    def to_pixels(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        """Returns (x0, y0, x1, y1) clamped inside a frame of the given size."""
        x0 = min(frame_w - 1, max(0, round(self.x * frame_w)))
        y0 = min(frame_h - 1, max(0, round(self.y * frame_h)))
        x1 = max(x0 + 1, min(frame_w, round((self.x + self.w) * frame_w)))
        y1 = max(y0 + 1, min(frame_h, round((self.y + self.h) * frame_h)))
        return x0, y0, x1, y1

    def crop(self, frame):
        """Crops a (height, width, channels) frame array to this rectangle."""
        x0, y0, x1, y1 = self.to_pixels(frame.shape[1], frame.shape[0])
        return frame[y0:y1, x0:x1]


class WindowBinding(BaseModel):
    """Identifies the one game window RetroBard is allowed to look at."""

    # Executable file name of the emulator/game, e.g. "snes9x-x64.exe"
    process_name: str
    # Optional extra filter when the process owns several windows
    title_contains: str | None = None

    def matches(self, process_name: str, title: str) -> bool:
        if process_name.lower() != self.process_name.strip().lower():
            return False
        if self.title_contains:
            return self.title_contains.strip().lower() in title.lower()
        return True


class OcrConfig(BaseModel):
    # Dialogue box and optional speaker nameplate, relative to the game window
    region: NormRect | None = None
    speaker_region: NormRect | None = None
    upscale_factor: int = Field(default=3, ge=1, le=6)
    binary_threshold: int = Field(default=180, ge=0, le=255)
    speaker_binary_threshold: int | None = Field(default=None, ge=0, le=255)
    cursor_mask_ratio: float = Field(default=0.12, ge=0.0, le=0.5)
    debounce_ms: int = Field(default=200, ge=50)

    # Text engine: "glyph" matches the game's own bitmap letters (exact and fast,
    # needs `retrobard teach`), "easyocr" is the generic fallback, "auto" uses
    # glyph matching whenever a taught font exists for the game.
    engine: Literal["auto", "glyph", "easyocr"] = "auto"
    # The console's native resolution, used to undo the emulator's scaling
    native_width: int = Field(default=256, ge=32)
    native_height: int = Field(default=224, ge=32)
    # Where the game picture sits inside the window. Leave unset when the emulator
    # keeps the aspect ratio and centres the picture (the usual case).
    picture: NormRect | None = None
    # Brightness (0-255) at or above which a native pixel counts as text
    glyph_threshold: int = Field(default=160, ge=0, le=255)


class DialogueRules(BaseModel):
    speaker_delimiter: str = ":"
    strip_patterns: list[str] = Field(default_factory=lambda: [r"^[\*\-\|\s]+"])
    min_length: int = 2
    # Drop the answer lines of an in-box choice ("Stay the night?" / "Yes" / "No")
    skip_choices: bool = True

    @field_validator("strip_patterns")
    @classmethod
    def _check_patterns(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            if len(pattern) > MAX_STRIP_PATTERN_LENGTH:
                raise ValueError(
                    f"strip pattern {pattern[:40]!r}... is longer than "
                    f"{MAX_STRIP_PATTERN_LENGTH} characters"
                )
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"strip pattern {pattern!r} is not a valid regex: {exc}") from exc
        return patterns


class CharacterVoiceConfig(BaseModel):
    voice_id: str
    stability: float = Field(default=0.50, ge=0.0, le=1.0)
    similarity_boost: float = Field(default=0.75, ge=0.0, le=1.0)
    style: float = Field(default=0.00, ge=0.0, le=1.0)
    speed: float = Field(default=1.00, ge=0.7, le=1.2)
    pitch_semitones: float = Field(default=0.0, ge=-12.0, le=12.0)
    effect: AudioEffect = Field(default=AudioEffect.NONE)
    use_speaker_boost: bool = True

    def to_domain(self) -> VoiceProfile:
        return VoiceProfile(
            voice_id=self.voice_id,
            stability=self.stability,
            similarity_boost=self.similarity_boost,
            style=self.style,
            speed=self.speed,
            pitch_semitones=self.pitch_semitones,
            effect=self.effect,
            use_speaker_boost=self.use_speaker_boost,
        )


class VoicesConfig(BaseModel):
    default_narrator: str
    # Read lines with no speaker name (unnamed NPCs, item messages) in the narrator
    # voice. When off they are not spoken at all, since there is no way to tell
    # whether a kid, a woman or a man is talking.
    narrate_unnamed: bool = False
    # Supports both "CRONO: voice_id" and "FROG: {voice_id: ..., speed: 0.88}"
    characters: dict[str, str | CharacterVoiceConfig] = Field(default_factory=dict)
    female_pool: list[str] = Field(
        default_factory=lambda: [
            "OZ0L6eISlOejga3XjDFt",  # Talia
            "dvbL7qkNGZY1IqPGZAjM",  # Elowen
            "QtY3JBOUKEB5xzrRfOKc",  # Maisie
            "BFd5oBc2DDna33pSi4Gf",  # Alicia
            "M6N6IdXhi5YNZyZSDe7k",  # Amanda
        ]
    )
    male_pool: list[str] = Field(
        default_factory=lambda: [
            "6WwXjDDEMyNmFG95zycZ",  # Eldrin
            "fs2OqxduwXgp9foh2xjK",  # Callum (Husky Trickster)
            "ktkP7Nsj67dw2zcplQYt",  # Lawrence
            "10NkTYmU7tSz3Kkl3Lex",  # Kaelen
            "FrS6cKLB1wg4WYgPa9GW",  # Wyatt
        ]
    )

    def resolve_character(self, speaker: str) -> VoiceProfile | None:
        entry = self.characters.get(speaker)
        if entry is None:
            return None
        if isinstance(entry, str):
            return VoiceProfile(voice_id=entry)
        return entry.to_domain()
    

class CustomVoicesConfig(VoicesConfig):
    """Voices for the custom (OpenAI-compatible) server, whose voice names differ
    from ElevenLabs IDs. Anything left out falls back to the server's default voice."""

    default_narrator: str = ""
    female_pool: list[str] = Field(default_factory=list)
    male_pool: list[str] = Field(default_factory=list)


class InputConfig(BaseModel):
    enabled: bool = True
    # Common emulator Confirm/Cancel keys (normalized to lowercase)
    skip_keys: list[str] = Field(
        default_factory=lambda: ["z", "x", "space", "enter"]
    )
    # Standard SDL gamepad button indices (0 = A/Cross, 1 = B/Circle)
    skip_gamepad_buttons: list[int] = Field(default_factory=lambda: [0, 1])
    cooldown_ms: int = Field(default=250, ge=50)


class GameProfile(BaseModel):
    game_id: str = Field(pattern=GAME_ID_PATTERN)
    window: WindowBinding | None = None
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    dialogue_rules: DialogueRules = Field(default_factory=DialogueRules)
    controls: InputConfig = Field(default_factory=InputConfig)
    voices: VoicesConfig
    # Voice names for the custom server (optional; see CustomVoicesConfig)
    custom_voices: CustomVoicesConfig | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> "GameProfile":
        if not path.exists():
            raise FileNotFoundError(f"Game profile not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        # Normalize character keys to uppercase for case-insensitive matching
        for section in ("voices", "custom_voices"):
            voices = data.get(section)
            if isinstance(voices, dict) and isinstance(voices.get("characters"), dict):
                voices["characters"] = {
                    k.strip().upper(): v for k, v in voices["characters"].items()
                }
        return cls.model_validate(data)

    def voices_for_custom_server(self, default_voice: str) -> VoicesConfig:
        """The voices to use with the custom server.

        Every character known from `voices` stays known (the parser needs the names
        to recognise who is speaking); those without a custom voice use the narrator."""
        custom = self.custom_voices or CustomVoicesConfig()
        narrator = custom.default_narrator.strip() or default_voice
        characters: dict[str, str | CharacterVoiceConfig] = {
            name: narrator for name in self.voices.characters
        }
        characters.update(custom.characters)
        return VoicesConfig(
            default_narrator=narrator,
            narrate_unnamed=self.voices.narrate_unnamed,
            characters=characters,
            female_pool=list(custom.female_pool),
            male_pool=list(custom.male_pool),
        )

    def save_to_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                self.model_dump(mode="json", exclude_none=True),
                f,
                sort_keys=False,
            )


class CustomTTSConfig(BaseModel):
    """A text-to-speech server that speaks the OpenAI speech API
    (POST <base_url>/audio/speech): OpenAI itself, or a server running on this
    computer such as Kokoro-FastAPI, Speaches or LocalAI. The defaults match
    Kokoro-FastAPI started with its usual settings."""

    base_url: str = "http://localhost:8880/v1"
    model: str = "kokoro"
    default_voice: str = "af_heart"
    # "pcm" plays while the audio is still arriving (16-bit mono at sample_rate);
    # "wav" and "mp3" wait for the whole clip
    response_format: Literal["pcm", "wav", "mp3"] = "pcm"
    sample_rate: int = Field(default=24000, ge=8000, le=48000)

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, url: str) -> str:
        url = url.strip().rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("the server address must start with http:// or https://")
        return url

    @field_validator("model", "default_voice")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    def is_local(self) -> bool:
        host = (urlparse(self.base_url).hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")

    @classmethod
    def load(cls, path: Path) -> "CustomTTSConfig":
        """The saved server settings, or the defaults when none were saved yet."""
        if not path.exists():
            return cls()
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.model_dump(), indent=2), encoding="utf-8")


class AppSettings(BaseSettings):
    # The packaged app reads optional overrides from %APPDATA%\RetroBard\settings.env
    model_config = SettingsConfigDict(
        env_file=user_data_dir() / "settings.env" if is_frozen() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Only names starting with RETROBARD_ are read (RETROBARD_CACHE_DIR,
        # RETROBARD_PROFILES_DIR, RETROBARD_BUNDLED_PROFILES_DIR, RETROBARD_UDP_HOST,
        # RETROBARD_UDP_PORT, RETROBARD_CACHE_MAX_MB), so an unrelated variable such
        # as a CACHE_DIR set for another program is never picked up
        env_prefix="RETROBARD_",
    )

    # These keep the names the README documents (an alias skips the prefix)
    elevenlabs_api_key: str = Field(default="", validation_alias="ELEVENLABS_API_KEY")
    elevenlabs_model_id: str = Field(
        default="eleven_flash_v2_5", validation_alias="ELEVENLABS_MODEL_ID"
    )
    # "pcm_<rate>" plays while the audio is still arriving; "mp3_44100_128" waits for the full clip
    elevenlabs_output_format: str = Field(
        default="pcm_24000", validation_alias="ELEVENLABS_OUTPUT_FORMAT"
    )
    # Shared secret every UDP packet must carry. Leave empty to have one generated
    # and stored in <cache_dir>/udp_token for local helper scripts to read.
    udp_token: str = Field(default="", validation_alias="UDP_TOKEN")

    udp_host: str = "127.0.0.1"
    udp_port: int = 5005
    cache_dir: Path = Field(
        default_factory=lambda: user_data_dir() / "cache" if is_frozen() else Path("./.retrobard_cache")
    )
    profiles_dir: Path = Field(
        default_factory=lambda: user_data_dir() / "profiles" if is_frozen() else Path("./profiles")
    )
    # Profiles and letters shipped with the packaged app. The user's taught letters
    # are stored in profiles_dir on top of these. None when running from source,
    # where profiles_dir is the shipped folder itself.
    bundled_profiles_dir: Path | None = Field(
        default_factory=lambda: bundle_dir() / "profiles" if is_frozen() else None
    )
    # The custom (OpenAI-compatible) voice server, edited from the Play tab
    custom_tts_file: Path = Field(
        default_factory=lambda: user_data_dir() / "custom_tts.json"
        if is_frozen()
        else Path("./custom_tts.json")
    )
    # Overrides the saved key for the custom server (development)
    custom_tts_api_key: str = Field(default="", validation_alias="CUSTOM_TTS_API_KEY")
    # Spoken lines kept on disk (all games together); the least recently used go first
    cache_max_mb: int = Field(default=500, ge=1)
