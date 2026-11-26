# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Setting up a game, as plain functions any front end can call.

Nothing here asks questions or draws windows: the caller shows the list of
windows, lets the user drag a box on the snapshot, and passes the result in.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from retrobard.adapters.cache.sqlite_disk import SqliteDiskCache
from retrobard.adapters.capture.win32_window import (
    Win32WindowSource,
    WindowInfo,
    list_windows,
)
from retrobard.adapters.ocr.glyph_reader import GlyphFont, GlyphReader, ReadResult
from retrobard.app.session import font_path, load_font_layers, profile_path
from retrobard.config import (
    AppSettings,
    GameProfile,
    NormRect,
    VoicesConfig,
    WindowBinding,
)

DEFAULT_NARRATOR_VOICE = "eGSd0uV8dNQnZuEEX9lu"  # Adam (Infinity: never retired)


def list_games(settings: AppSettings) -> list[str]:
    """Names of the games that have a profile."""
    if not settings.profiles_dir.exists():
        return []
    return sorted(path.stem for path in settings.profiles_dir.glob("*.yaml"))


def load_or_create_profile(settings: AppSettings, game: str) -> GameProfile:
    path = profile_path(settings, game)
    if path.exists():
        return GameProfile.from_yaml(path)
    return GameProfile(game_id=game, voices=VoicesConfig(default_narrator=DEFAULT_NARRATOR_VOICE))


def save_profile(settings: AppSettings, game: str, profile: GameProfile) -> None:
    profile.save_to_yaml(profile_path(settings, game))


def update_profile(
    settings: AppSettings, game: str, change: Callable[[GameProfile], None]
) -> GameProfile:
    """Loads the game's profile from disk, applies `change` and saves it.

    Pages each edit a few fields, so they start from the saved file instead of an
    older copy of their own and never undo what another page saved."""
    profile = load_or_create_profile(settings, game)
    change(profile)
    save_profile(settings, game, profile)
    return profile


@dataclass
class GameFont:
    """A game's letters together with the file the user's own letters go to.

    `font` holds every known letter (the shipped ones plus the user's); saving
    writes only the user's own, so the shipped letters stay untouched."""

    font: GlyphFont
    path: Path
    shipped: GlyphFont | None = None

    @property
    def own(self) -> GlyphFont:
        return self.font.without(self.shipped)

    def save(self) -> None:
        self.own.save(self.path)


def load_font(settings: AppSettings, game: str) -> GameFont:
    shipped, own = load_font_layers(settings, game)
    return GameFont(
        font=GlyphFont.layered(shipped, own), path=font_path(settings, game), shipped=shipped
    )


def export_letters(
    settings: AppSettings, game: str, destination: Path, app_version: str, emulator: str | None
) -> int:
    """Writes the letters this user taught to `destination`, to send to the developer.

    The file holds letter shapes only (no screenshots, no dialogue). Returns how
    many shapes it contains."""
    own = load_font(settings, game).own
    own.save(
        destination,
        extra={
            "game": game,
            "exported_with": f"RetroBard {app_version}",
            "emulator": emulator or "unknown",
        },
    )
    return len(own.glyphs)


def list_game_windows(query: str | None = None) -> list[WindowInfo]:
    """Open windows the user can link, optionally filtered by title or program name."""
    windows = list_windows()
    if query:
        needle = query.strip().lower()
        windows = [
            w for w in windows if needle in w.title.lower() or needle in w.process_name.lower()
        ]
    return windows


# Programs that usually have several unrelated windows open (browsers, Explorer,
# Store apps): linking one of them always needs a title filter
MULTI_WINDOW_PROGRAMS = frozenset({
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "explorer.exe",
    "applicationframehost.exe",
})


def is_multi_window_program(window: WindowInfo) -> bool:
    return window.process_name.lower() in MULTI_WINDOW_PROGRAMS


def needs_title_filter(window: WindowInfo, open_windows: list[WindowInfo]) -> bool:
    """True when the program alone would not tell the game window apart."""
    if is_multi_window_program(window):
        return True
    exe = window.process_name.lower()
    same_program = [
        w for w in open_windows if w.eligible and w.process_name.lower() == exe
    ]
    return len(same_program) > 1


def binding_for(
    window: WindowInfo, open_windows: list[WindowInfo], title_contains: str | None = None
) -> WindowBinding:
    """The binding for the chosen window.

    Bound by program, since emulator titles change with the loaded game and FPS
    counter, plus a part of the title when several windows share the program.
    Raises ValueError for a browser-like program without a title filter."""
    title_contains = (title_contains or "").strip() or None
    if title_contains is None and is_multi_window_program(window):
        raise ValueError(f"{window.process_name} needs a title filter.")
    if not needs_title_filter(window, open_windows):
        title_contains = None
    return WindowBinding(process_name=window.process_name, title_contains=title_contains)


def is_blank(frame: np.ndarray) -> bool:
    """True if a captured frame is one flat colour (the capture did not see the game)."""
    return int(frame[..., :3].max()) == int(frame[..., :3].min())


def capture_snapshot(binding: WindowBinding) -> np.ndarray | None:
    """One frame of the linked game window (BGRA), or None if it cannot be captured.
    Only that window is captured, never the screen."""
    source = Win32WindowSource(binding)
    try:
        return source.grab()
    finally:
        source.close()


def set_dialogue_region(
    profile: GameProfile, x: int, y: int, w: int, h: int, frame_w: int, frame_h: int
) -> None:
    """Stores the dialogue box the user dragged on a snapshot of the given size."""
    profile.ocr.region = NormRect.from_pixels(x, y, w, h, frame_w, frame_h)


def read_preview(profile: GameProfile, font: GlyphFont, frame: np.ndarray) -> ReadResult:
    """What the taught font reads in `frame`, to show the user while setting up."""
    return GlyphReader(profile.ocr, font).read(frame)


def assign_voice(
    settings: AppSettings, game: str, profile: GameProfile, character: str, voice_id: str
) -> int:
    """Gives a character a voice, saves the profile and drops that character's cached
    lines so the new voice takes effect. Returns how many cached lines were removed."""
    key = character.strip().upper()
    profile.voices.characters[key] = voice_id
    save_profile(settings, game, profile)
    return SqliteDiskCache(settings.cache_dir).purge_speaker(profile.game_id, key)
