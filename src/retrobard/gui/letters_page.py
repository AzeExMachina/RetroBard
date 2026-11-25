# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Teaching the game's letters: collect dialogue while playing, then name each new shape."""

import threading
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from retrobard import __version__
from retrobard.adapters.capture.win32_window import Win32WindowSource
from retrobard.adapters.ocr.teacher import (
    UnknownShape,
    collect_dialogue_frames,
    name_shape,
    prepare_teaching,
)
from retrobard.app import setup
from retrobard.domain.status import null_status
from retrobard.gui.state import AppState

PIXEL_SCALE = 5
CARDS_PER_ROW = 3
# Native pixels of neighbouring text shown on each side of a shape; kept small
# enough that three cards fit side by side
CONTEXT_COLUMNS = 18


def shape_pixmap(shape: UnknownShape) -> QPixmap:
    """The text around a shape, enlarged, with the shape itself highlighted."""
    picture, (start, end) = shape.image(context_columns=CONTEXT_COLUMNS)
    height, width = picture.shape
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[picture > 0] = (150, 150, 150)  # neighbouring letters in grey
    rgb[:, start:end][picture[:, start:end] > 0] = (255, 210, 0)  # this shape in yellow
    image = QImage(rgb.tobytes(), width, height, width * 3, QImage.Format.Format_RGB888)
    pixmap = QPixmap.fromImage(image).scaled(
        width * PIXEL_SCALE,
        height * PIXEL_SCALE,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    # A thin frame around the shape so single dots are easy to spot
    painter = QPainter(pixmap)
    painter.setPen(QColor(255, 210, 0))
    painter.drawRect(start * PIXEL_SCALE - 2, 0, (end - start) * PIXEL_SCALE + 3, pixmap.height() - 1)
    painter.end()
    return pixmap


class ShapeCard(QWidget):
    def __init__(self, shape: UnknownShape):
        super().__init__()
        self.shape = shape
        picture = QLabel()
        picture.setPixmap(shape_pixmap(shape))
        picture.setStyleSheet("background: #18181c; padding: 4px;")
        self.edit = QLineEdit()
        self.edit.setMaxLength(4)
        self.edit.setFixedWidth(64)
        self.edit.setPlaceholderText("letter")
        self.ignore = QCheckBox("Not a letter")
        self.ignore.toggled.connect(lambda checked: self.edit.setDisabled(checked))
        answer = QHBoxLayout()
        answer.addWidget(self.edit)
        answer.addWidget(self.ignore)
        answer.addWidget(QLabel(f"seen {shape.count}x"))
        answer.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(picture)
        layout.addLayout(answer)

    def answer(self) -> tuple[bool, str | None]:
        """Returns (answered, characters); characters is None for 'not a letter'."""
        if self.ignore.isChecked():
            return True, None
        text = self.edit.text().strip()
        return (True, text) if text else (False, None)


class LettersPage(QWidget):
    _progress = Signal(str)
    _collected = Signal(object)  # list of frames

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self._stop_collecting = threading.Event()
        self._collecting = False
        self._cards: list[ShapeCard] = []

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)

        self.boxes_spin = QSpinBox()
        self.boxes_spin.setRange(3, 200)
        self.boxes_spin.setValue(20)
        self.collect_button = QPushButton("Collect dialogue")
        self.progress_label = QLabel()
        collect_row = QHBoxLayout()
        collect_row.addWidget(QLabel("Dialogue boxes to collect:"))
        collect_row.addWidget(self.boxes_spin)
        collect_row.addWidget(self.collect_button)
        collect_row.addWidget(self.progress_label, 1)

        self.help_label = QLabel(
            "Press Collect dialogue, then play the game and step through some dialogue. "
            "Afterwards each letter shape RetroBard does not know is shown in yellow: "
            "type which character it is. Tick 'Not a letter' for cursors and icons."
        )
        self.help_label.setWordWrap(True)

        self.cards_holder = QWidget()
        self.cards_grid = QGridLayout(self.cards_holder)
        self.cards_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.cards_holder)

        self.save_button = QPushButton("Save letters")
        self.save_button.setEnabled(False)
        self.save_label = QLabel()
        save_row = QHBoxLayout()
        save_row.addWidget(self.save_label, 1)
        save_row.addWidget(self.save_button)

        # ---- sending the letters you taught to the developer
        self.share_label = QLabel()
        self.share_label.setWordWrap(True)
        self.export_button = QPushButton("Export letters...")
        share_row = QHBoxLayout()
        share_row.addWidget(self.share_label, 1)
        share_row.addWidget(self.export_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.help_label)
        layout.addLayout(collect_row)
        layout.addWidget(scroll, 1)
        layout.addLayout(save_row)
        layout.addLayout(share_row)

        self.collect_button.clicked.connect(self.toggle_collecting)
        self.save_button.clicked.connect(self.save_letters)
        # clicked passes a `checked` bool; keep it away from `destination`.
        self.export_button.clicked.connect(lambda: self.export_letters())
        self._progress.connect(self.progress_label.setText)
        self._collected.connect(self.show_frames)
        state.game_changed.connect(lambda _game: self.load_game())
        state.game_data_saved.connect(lambda _game: self.refresh_summary())
        self.load_game()

    # ------------------------------------------------------------------------ data

    def load_game(self) -> None:
        self._clear_cards()
        self.refresh_summary()

    def refresh_summary(self) -> None:
        game = self.state.game
        if not game:
            self.summary_label.setText("Add a game first (button at the top).")
            self.collect_button.setEnabled(False)
            self.export_button.setEnabled(False)
            return
        profile = setup.load_or_create_profile(self.state.settings, game)
        game_font = setup.load_font(self.state.settings, game)
        font = game_font.font
        own = len(game_font.own.glyphs)
        self.export_button.setEnabled(own > 0)
        self.share_label.setText(
            f"You taught {own} letter shape(s) yourself. Export them to a file and send it "
            "to the developer so everyone gets them. The file holds letter shapes only."
            if own
            else "Letters you teach can be exported here and sent to the developer."
        )
        ready = profile.window is not None and profile.ocr.region is not None
        known = sum(1 for char in font.glyphs.values() if char)
        ignored = sum(1 for char in font.glyphs.values() if not char)
        if not ready:
            self.summary_label.setText(
                "Link the game window and mark the dialogue area on the Setup tab first."
            )
        else:
            self.summary_label.setText(
                f"{known} letter shape(s) taught for this game"
                + (f", {ignored} marked as not a letter." if ignored else ".")
            )
        self.collect_button.setEnabled(ready or self._collecting)

    # ------------------------------------------------------------------ collecting

    def toggle_collecting(self) -> None:
        if self._collecting:
            self._stop_collecting.set()
            return
        game = self.state.game
        profile = setup.load_or_create_profile(self.state.settings, game)
        if profile.window is None or profile.ocr.region is None:
            return
        self._collecting = True
        self._stop_collecting.clear()
        self.collect_button.setText("Stop collecting")
        self.progress_label.setText("Play the game and step through dialogue...")
        max_boxes = self.boxes_spin.value()

        def work() -> None:
            source = Win32WindowSource(profile.window, status=null_status)
            try:
                frames = collect_dialogue_frames(
                    source,
                    profile.ocr,
                    max_boxes,
                    echo=lambda message: self._progress.emit(message.strip()),
                    should_stop=self._stop_collecting.is_set,
                )
            finally:
                source.close()
            self._collected.emit(frames)

        threading.Thread(target=work, daemon=True).start()

    def shutdown(self) -> None:
        self._stop_collecting.set()

    def show_frames(self, frames: list) -> None:
        """Lists the unknown letter shapes found in `frames` for the user to name."""
        self._collecting = False
        self.collect_button.setText("Collect dialogue")
        self._clear_cards()
        game = self.state.game
        if not frames:
            self.progress_label.setText("No dialogue boxes were collected.")
            return

        profile = setup.load_or_create_profile(self.state.settings, game)
        self._game_font = setup.load_font(self.state.settings, game)
        plan = prepare_teaching(profile.ocr, self._game_font.font, frames)
        self._game_font.save()  # keeps the worked-out text layout

        note = f"Collected {len(frames)} box(es)"
        if plan.frames_left_out:
            note += f", left out {plan.frames_left_out} that did not show lines of text"
        if not plan.shapes:
            self.progress_label.setText(note + ". Every letter in them is already known.")
            self.refresh_summary()
            return
        self.progress_label.setText(note + f". {len(plan.shapes)} new letter shape(s) to name.")

        for index, shape in enumerate(plan.shapes):
            card = ShapeCard(shape)
            self._cards.append(card)
            self.cards_grid.addWidget(card, index // CARDS_PER_ROW, index % CARDS_PER_ROW)
        self.save_button.setEnabled(True)
        self.save_label.setText("")

    def _clear_cards(self) -> None:
        for card in self._cards:
            self.cards_grid.removeWidget(card)
            card.deleteLater()
        self._cards = []
        self.save_button.setEnabled(False)

    # ---------------------------------------------------------------------- saving

    def save_letters(self) -> None:
        named = 0
        remaining: list[ShapeCard] = []
        for card in self._cards:
            answered, characters = card.answer()
            if answered:
                name_shape(self._game_font.font, card.shape.key, characters)
                named += 1
            else:
                remaining.append(card)
        if named == 0:
            self.save_label.setText("Type at least one letter first.")
            return
        self._game_font.save()

        for card in self._cards:
            self.cards_grid.removeWidget(card)
            if card not in remaining:
                card.deleteLater()
        self._cards = remaining
        for index, card in enumerate(self._cards):
            self.cards_grid.addWidget(card, index // CARDS_PER_ROW, index % CARDS_PER_ROW)

        self.save_button.setEnabled(bool(self._cards))
        self.save_label.setText(
            f"Saved {named} letter shape(s)."
            + (f" {len(self._cards)} still unnamed." if self._cards else "")
            + " Restart reading on the Play tab to use them."
        )
        self.refresh_summary()
        self.state.game_data_saved.emit(self.state.game)

    # -------------------------------------------------------------------- exporting

    def export_letters(self, destination: Path | None = None) -> None:
        game = self.state.game
        if not game:
            return
        if destination is None:
            chosen, _filter = QFileDialog.getSaveFileName(
                self,
                "Export letters",
                str(Path.home() / "Desktop" / f"{game}.letters.json"),
                "Letter files (*.json)",
            )
            if not chosen:
                return
            destination = Path(chosen)
        profile = setup.load_or_create_profile(self.state.settings, game)
        emulator = profile.window.process_name if profile.window else None
        count = setup.export_letters(self.state.settings, game, destination, __version__, emulator)
        self.share_label.setText(f"Exported {count} letter shape(s) to {destination}.")
