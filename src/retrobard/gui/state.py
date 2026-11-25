# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""State shared by the pages: which game is selected, and status events on the GUI thread."""

from PySide6.QtCore import QObject, Signal

from retrobard.app import setup
from retrobard.app.profile_updates import update_profiles
from retrobard.config import AppSettings
from retrobard.domain.status import StatusEvent
from retrobard.paths import install_default_profiles


class StatusBridge(QObject):
    """Carries status events from background threads to the GUI thread.

    Components call `sink` from whatever thread they run on; Qt queues the
    signal, so everything connected to `event` runs on the GUI thread.
    """

    event = Signal(object)

    def sink(self, status_event: StatusEvent) -> None:
        self.event.emit(status_event)


class AppState(QObject):
    game_changed = Signal(str)
    games_changed = Signal()
    # Emitted after a profile or font was saved, so other pages can reload
    game_data_saved = Signal(str)

    def __init__(self, settings: AppSettings | None = None):
        super().__init__()
        self.settings = settings or AppSettings()
        # The packaged app starts with the game profiles shipped with it, and
        # profiles from an earlier version get the voice updates of this one
        install_default_profiles(self.settings.bundled_profiles_dir, self.settings.profiles_dir)
        update_profiles(self.settings.bundled_profiles_dir, self.settings.profiles_dir)
        games = self.games()
        self.game: str = games[0] if games else ""

    def games(self) -> list[str]:
        return setup.list_games(self.settings)

    def select_game(self, game: str) -> None:
        if game and game != self.game:
            self.game = game
            self.game_changed.emit(game)

    def add_game(self, game: str) -> None:
        profile = setup.load_or_create_profile(self.settings, game)
        setup.save_profile(self.settings, game, profile)
        self.games_changed.emit()
        self.select_game(game)
