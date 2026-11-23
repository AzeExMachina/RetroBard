# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-game options: a few switches stored in the game's profile."""

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from retrobard.adapters.cache.sqlite_disk import SqliteDiskCache
from retrobard.app import setup
from retrobard.app.session import MB
from retrobard.config import GameProfile
from retrobard.gui.state import AppState


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label


class OptionsPage(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state

        self.narrator_check = QCheckBox("Read lines with no speaker name in the narrator voice")
        voices_layout = QVBoxLayout()
        voices_layout.addWidget(self.narrator_check)
        voices_layout.addWidget(
            _hint(
                "Unnamed people (shopkeepers, kids, townsfolk) and messages like "
                "\"Got 1 Tonic!\" have no name in the dialogue box. When this is off "
                "they are not spoken."
            )
        )
        voices_box = QGroupBox("Voices")
        voices_box.setLayout(voices_layout)

        self.skip_choices_check = QCheckBox("Only read the question of a choice, not the answers")
        dialogue_layout = QVBoxLayout()
        dialogue_layout.addWidget(self.skip_choices_check)
        dialogue_layout.addWidget(_hint("\"Will you rest?\" is read, \"Yes\" / \"No\" are not."))
        dialogue_box = QGroupBox("Dialogue")
        dialogue_box.setLayout(dialogue_layout)

        self.skip_keys_check = QCheckBox("The game's confirm/cancel buttons cut the line being spoken")
        controls_layout = QVBoxLayout()
        controls_layout.addWidget(self.skip_keys_check)
        controls_layout.addWidget(_hint("Takes effect the next time you press Start."))
        controls_box = QGroupBox("Controls")
        controls_box.setLayout(controls_layout)

        self.saved_label = _hint("Changes are saved right away.")

        self.cache_label = QLabel()
        self.clear_cache_button = QPushButton("Clear voice cache")
        cache_layout = QVBoxLayout()
        cache_layout.addWidget(self.cache_label)
        cache_layout.addWidget(self.clear_cache_button)
        cache_box = QGroupBox("Voice cache (all games)")
        cache_box.setLayout(cache_layout)

        layout = QVBoxLayout(self)
        layout.addWidget(voices_box)
        layout.addWidget(dialogue_box)
        layout.addWidget(controls_box)
        layout.addWidget(self.saved_label)
        layout.addWidget(cache_box)
        layout.addStretch(1)

        self.narrator_check.toggled.connect(self._save)
        self.skip_choices_check.toggled.connect(self._save)
        self.skip_keys_check.toggled.connect(self._save)
        self.clear_cache_button.clicked.connect(self.clear_cache)
        state.game_changed.connect(lambda _game: self.load_game())
        state.game_data_saved.connect(lambda _game: self.load_game())
        self.load_game()

    def showEvent(self, event) -> None:
        self.refresh_cache()
        super().showEvent(event)

    def _cache(self) -> SqliteDiskCache:
        return SqliteDiskCache(self.state.settings.cache_dir)

    def refresh_cache(self) -> None:
        cache = self._cache()
        size_mb = cache.total_bytes() / MB
        self.cache_label.setText(
            f"Spoken lines saved on this computer: {cache.count()} lines, {size_mb:.1f} MB"
        )

    def clear_cache(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear voice cache",
            "Delete all spoken lines saved on this computer? Lines spoken again are "
            "requested from the voice service again.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._cache().clear()
        self.refresh_cache()

    def _checks(self) -> list[QCheckBox]:
        return [self.narrator_check, self.skip_choices_check, self.skip_keys_check]

    def load_game(self) -> None:
        game = self.state.game
        profile = setup.load_or_create_profile(self.state.settings, game) if game else None
        for check in self._checks():
            check.blockSignals(True)
            check.setEnabled(profile is not None)
        if profile is not None:
            self.narrator_check.setChecked(profile.voices.narrate_unnamed)
            self.skip_choices_check.setChecked(profile.dialogue_rules.skip_choices)
            self.skip_keys_check.setChecked(profile.controls.enabled)
        for check in self._checks():
            check.blockSignals(False)

    def _save(self) -> None:
        if not self.state.game:
            return
        narrate = self.narrator_check.isChecked()
        skip_choices = self.skip_choices_check.isChecked()
        skip_keys = self.skip_keys_check.isChecked()

        def apply(profile: GameProfile) -> None:
            profile.voices.narrate_unnamed = narrate
            profile.dialogue_rules.skip_choices = skip_choices
            profile.controls.enabled = skip_keys

        setup.update_profile(self.state.settings, self.state.game, apply)
        self.state.game_data_saved.emit(self.state.game)
