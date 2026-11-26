# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""State shared by the pages: which game is selected, and status events on the GUI thread."""

from PySide6.QtCore import QObject, QSettings, Signal

from retrobard.app import setup
from retrobard.app.profile_updates import update_profiles
from retrobard.app.session import InputSource
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
    # Where the dialogue is read from changed (an InputSource); also sent after
    # game_changed, since each game remembers its own
    source_changed = Signal(object)

    def __init__(
        self,
        settings: AppSettings | None = None,
        last_game: str = "",
        ui_settings: QSettings | None = None,
    ):
        super().__init__()
        self.settings = settings or AppSettings()
        self.ui_settings = ui_settings
        # The packaged app starts with the game profiles shipped with it, and
        # profiles from an earlier version get the voice updates of this one
        install_default_profiles(self.settings.bundled_profiles_dir, self.settings.profiles_dir)
        update_profiles(self.settings.bundled_profiles_dir, self.settings.profiles_dir)
        games = self.games()
        # The game used last time, if its profile is still there; else the first one
        self.game: str = last_game if last_game in games else (games[0] if games else "")
        self.source: InputSource = self._saved_source(self.game)

    def games(self) -> list[str]:
        return setup.list_games(self.settings)

    def select_game(self, game: str) -> None:
        if game and game != self.game:
            self.game = game
            self.source = self._saved_source(game)
            self.game_changed.emit(game)
            self.source_changed.emit(self.source)

    @property
    def reads_memory(self) -> bool:
        """The dialogue comes from the game's memory: nothing is read off the screen."""
        return self.source == InputSource.MEMORY

    def set_source(self, source: InputSource) -> None:
        """Chooses where the current game's dialogue is read from, and remembers it."""
        if source == self.source:
            return
        self.source = source
        if self.ui_settings is not None and self.game:
            self.ui_settings.setValue(f"source/{self.game}", source.value)
        self.source_changed.emit(source)

    def _saved_source(self, game: str) -> InputSource:
        if self.ui_settings is None or not game:
            return InputSource.OCR
        try:
            return InputSource(str(self.ui_settings.value(f"source/{game}", InputSource.OCR.value)))
        except ValueError:
            return InputSource.OCR

    def add_game(self, game: str) -> None:
        profile = setup.load_or_create_profile(self.settings, game)
        setup.save_profile(self.settings, game, profile)
        self.games_changed.emit()
        self.select_game(game)
