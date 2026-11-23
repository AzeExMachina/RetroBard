# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Linking a game: pick its window, drag a box around the dialogue, see what is read."""

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from retrobard.adapters.capture.win32_window import Win32WindowSource, WindowInfo
from retrobard.adapters.ocr.glyph_reader import GlyphReader
from retrobard.app import setup
from retrobard.config import GameProfile
from retrobard.domain.status import null_status
from retrobard.gui.region_selector import RegionSelector
from retrobard.gui.state import AppState

PREVIEW_INTERVAL_MS = 300


CHECKLIST = [
    ("window", "Game window linked"),
    ("region", "Dialogue box selected"),
    ("letters", "Letters taught (Letters tab)"),
]


class SetupPage(QWidget):
    guide_requested = Signal()

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.profile: GameProfile | None = None
        self._source: Win32WindowSource | None = None
        self._open_windows: list[WindowInfo] = []  # as last listed, for title filters
        self._reader: GlyphReader | None = None
        self._dirty = False

        # ---- step 1: the game window
        self.window_list = QListWidget()
        self.refresh_button = QPushButton("Refresh list")
        self.link_button = QPushButton("Link selected window")
        self.linked_label = QLabel()
        self.linked_label.setWordWrap(True)
        window_buttons = QHBoxLayout()
        window_buttons.addWidget(self.refresh_button)
        window_buttons.addWidget(self.link_button)
        window_layout = QVBoxLayout()
        window_layout.addWidget(QLabel("Start the game or emulator, then pick its window:"))
        window_layout.addWidget(self.window_list, 1)
        window_layout.addLayout(window_buttons)
        window_layout.addWidget(self.linked_label)
        window_box = QGroupBox("1. Game window")
        window_box.setLayout(window_layout)

        # ---- what is done so far
        self.check_labels = {key: QLabel() for key, _text in CHECKLIST}
        self.guide_button = QPushButton("Open the quick start guide")
        checklist_layout = QVBoxLayout()
        for label in self.check_labels.values():
            checklist_layout.addWidget(label)
        checklist_layout.addWidget(self.guide_button)
        checklist_box = QGroupBox("Getting started")
        checklist_box.setLayout(checklist_layout)

        left = QVBoxLayout()
        left.addWidget(checklist_box)
        left.addWidget(window_box, 1)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left.setContentsMargins(0, 0, 0, 0)
        left_widget.setMaximumWidth(360)

        # ---- step 2: the dialogue box
        self.selector = RegionSelector()
        self.region_hint = QLabel(
            "Drag a box around the area where dialogue text appears. "
            "Only the linked window is captured, never the rest of your screen."
        )
        self.region_hint.setWordWrap(True)
        region_layout = QVBoxLayout()
        region_layout.addWidget(self.region_hint)
        region_layout.addWidget(self.selector, 1)
        region_box = QGroupBox("2. Dialogue area")
        region_box.setLayout(region_layout)

        # ---- step 3: what is read
        self.preview_label = QLabel("-")
        self.preview_label.setWordWrap(True)
        self.preview_label.setMinimumHeight(48)
        preview_layout = QVBoxLayout()
        preview_layout.addWidget(self.preview_label)
        preview_box = QGroupBox("3. What RetroBard reads right now")
        preview_box.setLayout(preview_layout)

        self.save_button = QPushButton("Save setup")
        self.save_label = QLabel()

        right = QVBoxLayout()
        right.addWidget(region_box, 1)
        right.addWidget(preview_box)
        save_row = QHBoxLayout()
        save_row.addWidget(self.save_label, 1)
        save_row.addWidget(self.save_button)
        right.addLayout(save_row)

        layout = QHBoxLayout(self)
        layout.addWidget(left_widget)
        layout.addLayout(right, 1)

        self.refresh_button.clicked.connect(self.refresh_windows)
        self.link_button.clicked.connect(self.link_selected_window)
        self.selector.region_selected.connect(self.on_region_selected)
        self.save_button.clicked.connect(self.save)
        state.game_changed.connect(lambda _game: self.load_game())
        state.game_data_saved.connect(lambda _game: self._rebuild_reader())
        state.game_data_saved.connect(lambda _game: self._update_checklist())
        self.guide_button.clicked.connect(self.guide_requested.emit)

        self._timer = QTimer(self)
        self._timer.setInterval(PREVIEW_INTERVAL_MS)
        self._timer.timeout.connect(self.update_preview)

        self.load_game()

    # ------------------------------------------------------------------- lifecycle

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_windows()
        self._open_source()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        # Stop looking at the game window as soon as this page is not visible
        self._timer.stop()
        self._close_source()

    def shutdown(self) -> None:
        self._timer.stop()
        self._close_source()

    def _open_source(self) -> None:
        self._close_source()
        if self.profile is not None and self.profile.window is not None:
            self._source = Win32WindowSource(self.profile.window, status=null_status)

    def _close_source(self) -> None:
        if self._source is not None:
            self._source.close()
            self._source = None

    # ------------------------------------------------------------------------ data

    def load_game(self) -> None:
        game = self.state.game
        self.profile = setup.load_or_create_profile(self.state.settings, game) if game else None
        self._dirty = False
        self.selector.set_frame(None)
        self.selector.set_region(self.profile.ocr.region if self.profile else None)
        self._rebuild_reader()
        self._update_labels()
        if self.isVisible():
            self._open_source()

    def _rebuild_reader(self) -> None:
        self._reader = None
        if self.profile is None or self.profile.ocr.region is None:
            return
        font = setup.load_font(self.state.settings, self.state.game).font
        if font.is_usable:
            self._reader = GlyphReader(self.profile.ocr, font)

    def _update_labels(self) -> None:
        if self.profile is None:
            self.linked_label.setText("Add a game first (button at the top).")
        elif self.profile.window is None:
            self.linked_label.setText("No window linked yet.")
        else:
            self.linked_label.setText(f"Linked to: {self.profile.window.process_name}")
        self.save_button.setEnabled(self._dirty and self.profile is not None)
        self.save_label.setText("Unsaved changes" if self._dirty else "")
        self._update_checklist()

    def _update_checklist(self) -> None:
        profile = self.profile
        done = {
            "window": profile is not None and profile.window is not None,
            "region": profile is not None and profile.ocr.region is not None,
            "letters": profile is not None
            and setup.load_font(self.state.settings, self.state.game).font.is_usable,
        }
        for key, text in CHECKLIST:
            mark = "✔" if done[key] else "○"  # check mark / empty circle
            label = self.check_labels[key]
            label.setText(f"{mark}  {text}")
            label.setStyleSheet("color: rgb(40, 170, 110);" if done[key] else "")

    def refresh_windows(self) -> None:
        self.window_list.clear()
        self._open_windows = setup.list_game_windows()
        for window in self._open_windows:
            item = QListWidgetItem(f"{window.title}\n    {window.process_name}")
            item.setData(256, window)  # Qt.UserRole
            self.window_list.addItem(item)

    def link_selected_window(self) -> None:
        item = self.window_list.currentItem()
        if item is None or self.profile is None:
            return
        window: WindowInfo = item.data(256)
        open_windows = self._open_windows or [window]
        title_filter = None
        if setup.needs_title_filter(window, open_windows):
            title_filter = self.ask_title_filter(window)
            if title_filter is None:
                return  # cancelled
        self.profile.window = setup.binding_for(window, open_windows, title_filter)
        self._dirty = True
        self.selector.set_frame(None)
        self._open_source()
        self._update_labels()

    def ask_title_filter(self, window: WindowInfo) -> str | None:
        """Part of the game window's title to link by. None cancels the link; an
        empty string links by program only (allowed after a warning, not for browsers)."""
        exe = window.process_name
        text, accepted = QInputDialog.getText(
            self,
            "Which window is the game?",
            f"Several windows belong to {exe}. Type part of the game window's title "
            f"that stays the same, such as the game's name.",
            text=window.title,
        )
        if not accepted:
            return None
        text = text.strip()
        if text:
            return text
        if setup.is_multi_window_program(window):
            return None
        answer = QMessageBox.warning(
            self,
            "No title filter",
            f"Without a title filter, nothing is read while more than one {exe} "
            f"window is open.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        return "" if answer == QMessageBox.StandardButton.Ok else None

    def on_region_selected(self, x: int, y: int, w: int, h: int) -> None:
        if self.profile is None or self._last_frame_size is None:
            return
        frame_w, frame_h = self._last_frame_size
        setup.set_dialogue_region(self.profile, x, y, w, h, frame_w, frame_h)
        self._dirty = True
        self.selector.set_region(self.profile.ocr.region)
        self._rebuild_reader()
        self._update_labels()

    _last_frame_size: tuple[int, int] | None = None

    def save(self) -> None:
        if self.profile is None:
            return
        window, region = self.profile.window, self.profile.ocr.region

        def apply(profile: GameProfile) -> None:
            profile.window = window
            profile.ocr.region = region

        self.profile = setup.update_profile(self.state.settings, self.state.game, apply)
        self._dirty = False
        self._update_labels()
        self.save_label.setText("Saved")
        self.state.game_data_saved.emit(self.state.game)

    # --------------------------------------------------------------------- preview

    def update_preview(self) -> None:
        if self._source is None:
            self.preview_label.setText("Link the game window first.")
            return
        frame = self._source.grab()
        if frame is None:
            self.selector.set_frame(None)
            self.preview_label.setText("The game window is not open, or it is minimised.")
            return
        if setup.is_blank(frame):
            self.preview_label.setText(
                "The picture of the game window is blank, so nothing can be read from it."
            )
        self._last_frame_size = (frame.shape[1], frame.shape[0])
        self.selector.set_frame(frame)

        if self.profile is None or self.profile.ocr.region is None:
            self.preview_label.setText("Drag a box around the dialogue area above.")
        elif self._reader is None:
            self.preview_label.setText(
                "No letters have been taught for this game yet. Save this setup, "
                "then go to the Letters tab."
            )
        else:
            result = self._reader.read(frame)
            if result.text:
                extra = f"   ({result.unknown} letter(s) not recognised)" if result.unknown else ""
                self.preview_label.setText(result.text.replace("\n", " / ") + extra)
            elif result.unknown:
                self.preview_label.setText(
                    f"{result.unknown} letter shape(s) seen, none recognised. "
                    f"Teach them on the Letters tab."
                )
            else:
                self.preview_label.setText("(no dialogue on screen)")
