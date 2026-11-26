# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""The quick start guide shown on first launch: one step per page, each with a drawing.

The pictures are drawn with QPainter instead of being screenshots, so they never go
out of date when the window changes and there are no image files to ship.
"""

from collections.abc import Callable
from dataclasses import dataclass
from html import escape

from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from retrobard.about import CONTACT

HIDE_KEY = "quick_start/hide"
PICTURE_W, PICTURE_H = 440, 220

ACCENT = QColor(40, 170, 110)
GAME_BG = QColor(24, 28, 48)
DIALOGUE_BLUE = QColor(36, 64, 150)

# A 7x9 bitmap "A", like a retro game's font
LETTER_A = [
    "0011100",
    "0110110",
    "1100011",
    "1100011",
    "1111111",
    "1100011",
    "1100011",
    "1100011",
]


def app_settings() -> QSettings:
    """Per-user settings (on Windows, stored in the registry under RetroBard)."""
    return QSettings("RetroBard", "RetroBard")


def should_show(settings: QSettings) -> bool:
    return str(settings.value(HIDE_KEY, "false")).lower() != "true"


# ---------------------------------------------------------------------- drawings


def _font(size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def _window_frame(p: QPainter, rect: QRect, title: str, palette: QPalette) -> QRect:
    """Draws a window with a title bar and returns its inside area."""
    p.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
    p.setBrush(palette.color(QPalette.ColorRole.Base))
    p.drawRoundedRect(rect, 6, 6)
    bar = QRect(rect.x(), rect.y(), rect.width(), 22)
    p.setBrush(palette.color(QPalette.ColorRole.Button))
    p.drawRoundedRect(bar, 6, 6)
    p.setPen(palette.color(QPalette.ColorRole.ButtonText))
    p.setFont(_font(11, bold=True))
    p.drawText(bar.adjusted(10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, title)
    return rect.adjusted(1, 23, -1, -1)


def _checkbox(p: QPainter, x: int, y: int, checked: bool, text: str, palette: QPalette) -> None:
    p.setPen(QPen(palette.color(QPalette.ColorRole.Text), 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(x, y, 14, 14)
    if checked:
        p.setPen(QPen(ACCENT, 2.5))
        p.drawPolyline(QPolygon([QPoint(x + 3, y + 7), QPoint(x + 6, y + 11), QPoint(x + 12, y + 3)]))
    p.setPen(palette.color(QPalette.ColorRole.Text))
    p.setFont(_font(13))
    p.drawText(x + 22, y + 12, text)


def _cursor(p: QPainter, x: int, y: int) -> None:
    arrow = QPolygon(
        [QPoint(x, y), QPoint(x, y + 18), QPoint(x + 5, y + 14), QPoint(x + 9, y + 21),
         QPoint(x + 12, y + 20), QPoint(x + 8, y + 13), QPoint(x + 14, y + 13)]
    )
    p.setPen(QPen(Qt.GlobalColor.black, 1))
    p.setBrush(Qt.GlobalColor.white)
    p.drawPolygon(arrow)


def _game_screen(p: QPainter, rect: QRect) -> QRect:
    """A made-up game picture with a dialogue box; returns the dialogue box."""
    p.save()
    p.setClipRect(rect)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(GAME_BG)
    p.drawRect(rect)
    # A few hills and a sun, so it reads as a game scene
    p.setBrush(QColor(52, 110, 70))
    p.drawEllipse(rect.x() - 40, rect.y() + 60, 200, 120)
    p.drawEllipse(rect.x() + 140, rect.y() + 70, 260, 120)
    p.setBrush(QColor(240, 200, 90))
    p.drawEllipse(rect.right() - 70, rect.y() + 14, 26, 26)
    box = QRect(rect.x() + 24, rect.bottom() - 70, rect.width() - 48, 58)
    p.setPen(QPen(QColor(220, 220, 240), 2))
    p.setBrush(DIALOGUE_BLUE)
    p.drawRoundedRect(box, 4, 4)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(230, 230, 240))
    for i, width in enumerate([0.75, 0.6, 0.4]):
        p.drawRect(box.x() + 14, box.y() + 12 + i * 14, int((box.width() - 28) * width), 6)
    p.restore()
    return box


def draw_emulator(p: QPainter, palette: QPalette) -> None:
    inside = _window_frame(p, QRect(70, 10, 300, 196), "Emulator - Display settings", palette)
    x, y = inside.x() + 20, inside.y() + 22
    _checkbox(p, x, y, False, "Smooth / bilinear filter", palette)
    _checkbox(p, x, y + 32, False, "Shader (CRT, scanlines)", palette)
    _checkbox(p, x, y + 64, True, "Stretch, keep aspect ratio", palette)
    _checkbox(p, x, y + 96, True, "Integer scaling", palette)


def draw_link_window(p: QPainter, palette: QPalette) -> None:
    inside = _window_frame(p, QRect(60, 10, 320, 196), "Setup - 1. Game window", palette)
    rows = [("Notes - shopping list", "notepad.exe", False),
            ("My Emulator 1.0 - 60 fps", "emulator.exe", True),
            ("Music player", "player.exe", False)]
    for i, (title, exe, selected) in enumerate(rows):
        row = QRect(inside.x() + 10, inside.y() + 10 + i * 40, inside.width() - 20, 36)
        if selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(palette.color(QPalette.ColorRole.Highlight))
            p.drawRect(row)
            p.setPen(palette.color(QPalette.ColorRole.HighlightedText))
        else:
            p.setPen(palette.color(QPalette.ColorRole.Text))
        p.setFont(_font(12))
        p.drawText(row.adjusted(8, 2, 0, 0), Qt.AlignmentFlag.AlignTop, title)
        p.setFont(_font(11))
        p.drawText(row.adjusted(24, 0, 0, -3), Qt.AlignmentFlag.AlignBottom, exe)
    button = QRect(inside.right() - 170, inside.bottom() - 36, 160, 26)
    p.setPen(QPen(ACCENT, 2))
    p.setBrush(palette.color(QPalette.ColorRole.Button))
    p.drawRoundedRect(button, 4, 4)
    p.setPen(palette.color(QPalette.ColorRole.ButtonText))
    p.setFont(_font(12))
    p.drawText(button, Qt.AlignmentFlag.AlignCenter, "Link selected window")
    _cursor(p, button.right() - 14, button.bottom() - 8)


def draw_dialogue_box(p: QPainter, palette: QPalette) -> None:
    inside = _window_frame(p, QRect(60, 6, 320, 208), "Setup - 2. Dialogue area", palette)
    box = _game_screen(p, inside)
    selection = box.adjusted(-6, -6, 6, 6)
    pen = QPen(ACCENT, 2.5, Qt.PenStyle.DashLine)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(selection)
    p.setPen(QPen(ACCENT, 1))
    p.setBrush(ACCENT)
    p.drawEllipse(selection.topLeft(), 4, 4)
    _cursor(p, selection.right() - 2, selection.bottom() - 2)


def draw_teach_letters(p: QPainter, palette: QPalette) -> None:
    cell = 14
    left, top = 70, 40
    p.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
    for r, row in enumerate(LETTER_A):
        for c, bit in enumerate(row):
            p.setBrush(QColor(235, 235, 245) if bit == "1" else GAME_BG)
            p.drawRect(left + c * cell, top + r * cell, cell, cell)
    # Arrow to the answer
    p.setPen(QPen(ACCENT, 3))
    ax, ay = left + 7 * cell + 30, top + 4 * cell + 7
    p.drawLine(ax, ay, ax + 70, ay)
    p.setBrush(ACCENT)
    p.drawPolygon(QPolygon([QPoint(ax + 80, ay), QPoint(ax + 68, ay - 7), QPoint(ax + 68, ay + 7)]))
    answer = QRect(ax + 100, ay - 22, 60, 44)
    p.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1.5))
    p.setBrush(palette.color(QPalette.ColorRole.Base))
    p.drawRect(answer)
    p.setPen(palette.color(QPalette.ColorRole.Text))
    p.setFont(_font(26, bold=True))
    p.drawText(answer, Qt.AlignmentFlag.AlignCenter, "A")
    _checkbox(p, ax + 100, ay + 40, False, "Not a letter", palette)


def _small_button(p: QPainter, rect: QRect, text: str, palette: QPalette) -> None:
    p.setPen(QPen(ACCENT, 2))
    p.setBrush(palette.color(QPalette.ColorRole.Button))
    p.drawRoundedRect(rect, 4, 4)
    p.setPen(palette.color(QPalette.ColorRole.ButtonText))
    p.setFont(_font(11))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def draw_voice_choice(p: QPainter, palette: QPalette) -> None:
    """The Play tab's voice list, open, with the button that sets up each service."""
    inside = _window_frame(p, QRect(30, 6, 380, 208), "Play - Voice", palette)
    rows = [
        ("Custom server (OpenAI-compatible)", "Server...", "Kokoro (free), OpenAI..."),
        ("ElevenLabs (AI voices)", "ElevenLabs key...", "your account"),
        ("Silent test (no voice, no credits)", None, "free"),
    ]
    for i, (label, button, note) in enumerate(rows):
        row = QRect(inside.x() + 12, inside.y() + 12 + i * 58, inside.width() - 24, 50)
        p.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
        p.setBrush(palette.color(QPalette.ColorRole.Button))
        p.drawRoundedRect(row, 4, 4)
        p.setPen(palette.color(QPalette.ColorRole.ButtonText))
        p.setFont(_font(12, bold=True))
        p.drawText(row.adjusted(10, 6, 0, 0), Qt.AlignmentFlag.AlignTop, label)
        p.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        p.setFont(_font(11))
        p.drawText(row.adjusted(10, 0, 0, -6), Qt.AlignmentFlag.AlignBottom, note)
        if button:
            rect = QRect(row.right() - 118, row.y() + 22, 108, 22)
            _small_button(p, rect, button, palette)
            if i == 0:
                _cursor(p, rect.right() - 20, rect.bottom() - 8)


def _toggle(p: QPainter, rect: QRect, label: str, value: str, on: bool, palette: QPalette) -> None:
    """A RetroArch-style settings row: the setting on the left, its value on the right."""
    p.setPen(palette.color(QPalette.ColorRole.Text))
    p.setFont(_font(11))
    p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
    p.setPen(ACCENT if on else palette.color(QPalette.ColorRole.Text))
    p.setFont(_font(11, bold=on))
    p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, value)


def draw_memory_reading(p: QPainter, palette: QPalette) -> None:
    """RetroArch with network commands on, and RetroBard reading from its memory."""
    left = _window_frame(p, QRect(10, 20, 200, 150), "RetroArch - Network", palette)
    row = QRect(left.x() + 10, left.y() + 12, left.width() - 20, 22)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(palette.color(QPalette.ColorRole.AlternateBase))
    p.drawRect(row.adjusted(-4, 0, 4, 0))
    _toggle(p, row, "Network Commands", "ON", True, palette)
    _toggle(p, row.translated(0, 30), "Command Port", "55355", False, palette)
    _toggle(p, row.translated(0, 60), "Netplay", "OFF", False, palette)

    # Arrow: RetroBard asks RetroArch for the game's memory
    p.setPen(QPen(ACCENT, 3))
    ay = 95
    p.drawLine(218, ay, 238, ay)
    p.setBrush(ACCENT)
    p.drawPolygon(QPolygon([QPoint(246, ay), QPoint(236, ay - 6), QPoint(236, ay + 6)]))

    right = _window_frame(p, QRect(252, 20, 180, 150), "RetroBard - Play", palette)
    p.setPen(palette.color(QPalette.ColorRole.Text))
    p.setFont(_font(11))
    p.drawText(right.adjusted(10, 10, 0, 0), Qt.AlignmentFlag.AlignTop, "Read dialogue from")
    combo = QRect(right.x() + 10, right.y() + 30, right.width() - 20, 26)
    p.setPen(QPen(ACCENT, 2))
    p.setBrush(palette.color(QPalette.ColorRole.Button))
    p.drawRoundedRect(combo, 4, 4)
    p.setPen(palette.color(QPalette.ColorRole.ButtonText))
    p.setFont(_font(10))
    p.drawText(combo.adjusted(6, 0, -14, 0), Qt.AlignmentFlag.AlignVCenter, "Game memory (RetroArch)")
    p.drawText(combo.adjusted(0, 0, -6, 0),
               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "v")
    p.setPen(palette.color(QPalette.ColorRole.Text))
    p.setFont(_font(11))
    p.drawText(right.adjusted(10, 70, -6, 0), Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
               "GIRL: Ouch, that hurt!")
    p.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
    p.drawText(right.adjusted(10, 92, -6, 0), Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
               "No OCR, no letters to teach")
    _cursor(p, combo.center().x() + 20, combo.bottom() - 8)


def draw_start(p: QPainter, palette: QPalette) -> None:
    inside = _window_frame(p, QRect(60, 10, 320, 196), "Play", palette)
    combo = QRect(inside.x() + 20, inside.y() + 22, inside.width() - 40, 30)
    p.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
    p.setBrush(palette.color(QPalette.ColorRole.Button))
    p.drawRoundedRect(combo, 4, 4)
    p.setPen(palette.color(QPalette.ColorRole.ButtonText))
    p.setFont(_font(12))
    p.drawText(combo.adjusted(10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
               "Voice: Silent test (no voice, no credits)")
    p.drawText(combo.adjusted(0, 0, -10, 0),
               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "v")
    button = QRect(inside.x() + 20, inside.y() + 72, inside.width() - 40, 44)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ACCENT)
    p.drawRoundedRect(button, 6, 6)
    p.setPen(Qt.GlobalColor.white)
    p.setFont(_font(16, bold=True))
    p.drawText(button, Qt.AlignmentFlag.AlignCenter, "Start")
    p.setPen(palette.color(QPalette.ColorRole.Text))
    p.setFont(_font(12))
    p.drawText(QRect(inside.x() + 20, inside.y() + 128, inside.width() - 40, 30),
               Qt.AlignmentFlag.AlignVCenter, "Now speaking:  CRONO: Wait up!")
    _cursor(p, button.center().x() + 20, button.center().y() + 4)


def picture(draw: Callable[[QPainter, QPalette], None], palette: QPalette) -> QPixmap:
    pixmap = QPixmap(PICTURE_W, PICTURE_H)
    pixmap.fill(palette.color(QPalette.ColorRole.Window))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw(painter, palette)
    painter.end()
    return pixmap


# ------------------------------------------------------------------------- steps


@dataclass(frozen=True)
class Step:
    title: str
    text: str
    draw: Callable[[QPainter, QPalette], None]


def credit_line(contact: str) -> str:
    """The grey line under the last step: who made RetroBard and where to write."""
    text = "RetroBard is made by Aze."
    if contact:
        text += f" Bugs and ideas: {escape(contact)}"
    return f'<br><br><span style="color:gray">{text}</span>'


STEPS = [
    Step(
        "Start your game",
        "Open your emulator and load the game. In the emulator's display settings, "
        "<b>turn off smoothing filters and shaders</b> and keep the picture's aspect "
        "ratio. RetroBard reads the game's letters pixel by pixel, so a blurred "
        "or tiny picture makes them unreadable. Make the window big enough for the "
        "game to be zoomed 3x or more.<br>"
        "In <b>Snes9x</b> (Video &gt; Display Configuration): tick <b>Stretch Image</b>, "
        "<b>Maintain Aspect Ratio</b> set to <b>8:7</b> and <b>Integer Scaling</b>; untick "
        "<b>Bilinear Filtering</b>, <b>NTSC Filter Scanlines</b>, <b>Use Shader</b> and "
        "<b>Extend Height of SNES Image</b>.",
        draw_emulator,
    ),
    Step(
        "Link the game window",
        "Pick your game in the <b>Game</b> list at the top (or press <b>New game...</b>), "
        "then open the <b>Setup</b> tab. Choose the emulator in the window list and press "
        "<b>Link selected window</b>. RetroBard only captures this one window, "
        "never the rest of your screen.",
        draw_link_window,
    ),
    Step(
        "Select the dialogue box",
        "Play until some dialogue is on screen. On the Setup tab, <b>drag a box around "
        "the area where the text appears</b>, then press <b>Save setup</b>. The box "
        "follows the window if you move or resize it.",
        draw_dialogue_box,
    ),
    Step(
        "Teach the letters",
        "Open the <b>Letters</b> tab and press <b>Collect dialogue</b>, then step through "
        "a few dialogue boxes in the game. RetroBard shows each letter shape it does not "
        "know yet: <b>type the character it is</b>, or tick <b>Not a letter</b> for "
        "cursors and icons. Each shape only has to be taught once. If the Play tab later "
        "says letters were not recognised, come back and teach the new ones.",
        draw_teach_letters,
    ),
    Step(
        "Choose where the voices come from",
        "Besides <b>Silent test</b>, the <b>Voice</b> list on the <b>Play</b> tab offers two "
        "voice services, each set up with the button next to it:<br>"
        "&bull; <b>Custom server</b>: a <b>free server on your own computer</b> like "
        "Kokoro-FastAPI, or any other service that speaks the OpenAI speech API, such as "
        "OpenAI. Press <b>Server...</b>, pick a preset, adjust the address and voice, and "
        "press <b>Test</b>.<br>"
        "&bull; <b>ElevenLabs</b>: natural AI voices from <b>your own account</b> (most "
        "voices need a paid plan). Press <b>ElevenLabs key...</b>, follow the link to "
        "create a key with Text to Speech access, and paste it.<br>"
        "Keys are kept in Windows Credential Manager on this computer.",
        draw_voice_choice,
    ),
    Step(
        "Optional: read the text from game memory",
        "For <b>Secret of Mana</b> and <b>Chrono Trigger</b> (USA versions), RetroBard can "
        "read the dialogue straight from the game's memory instead of the screen. It's "
        "exact even on see-through text boxes, and you can <b>skip linking the window, "
        "selecting the box and teaching letters</b>.<br>"
        "&bull; Play the game in <b>RetroArch</b> with the <b>Snes9x</b> core.<br>"
        "&bull; In RetroArch: <b>Settings &gt; Network</b>, turn on <b>Network Commands</b> "
        "and leave the port at <b>55355</b>.<br>"
        "&bull; On the <b>Play</b> tab, set <b>Read dialogue from</b> to <b>Game memory "
        "(RetroArch)</b>. The first time you press Start, choose the game's ROM file: "
        "the text is read from there, so it must be the exact version the profile "
        "was made for. Its location is remembered, and it never leaves your computer.",
        draw_memory_reading,
    ),
    Step(
        "Press Start",
        "On the <b>Play</b> tab, first try <b>Silent test</b>: it costs nothing and shows "
        "under <i>Now speaking</i> what RetroBard reads. When that looks right, switch "
        "to ElevenLabs or your custom server for real voices. The <b>Options</b> tab has "
        "a few switches for each game, such as whether unnamed characters are read by "
        "the narrator."
        + credit_line(CONTACT),
        draw_start,
    ),
]


class QuickStartDialog(QDialog):
    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("RetroBard - Quick start")
        self.setModal(True)

        self.pages = QStackedWidget()
        palette = self.palette()
        for step in STEPS:
            self.pages.addWidget(self._page(step, palette))

        self.step_label = QLabel()
        self.hide_check = QCheckBox("Don't show this at startup")
        self.hide_check.setChecked(not should_show(settings))
        self.back_button = QPushButton("Back")
        self.next_button = QPushButton("Next")
        self.next_button.setDefault(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.hide_check)
        buttons.addStretch(1)
        buttons.addWidget(self.step_label)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.pages, 1)
        layout.addLayout(buttons)

        self.back_button.clicked.connect(lambda: self._go(-1))
        self.next_button.clicked.connect(self._next)
        self.finished.connect(lambda _result: self._remember())
        self._go(0)

    @staticmethod
    def _page(step: Step, palette: QPalette) -> QWidget:
        title = QLabel(step.title)
        title.setFont(_font(20, bold=True))
        image = QLabel()
        image.setPixmap(picture(step.draw, palette))
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel(step.text)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setMinimumHeight(90)
        text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(title)
        layout.addWidget(image)
        layout.addWidget(text, 1)
        page.setMinimumWidth(PICTURE_W + 40)
        return page

    def _go(self, delta: int) -> None:
        index = max(0, min(self.pages.count() - 1, self.pages.currentIndex() + delta))
        self.pages.setCurrentIndex(index)
        self.step_label.setText(f"Step {index + 1} of {self.pages.count()}")
        self.back_button.setEnabled(index > 0)
        last = index == self.pages.count() - 1
        self.next_button.setText("Done" if last else "Next")

    def _next(self) -> None:
        if self.pages.currentIndex() == self.pages.count() - 1:
            self.accept()
        else:
            self._go(1)

    def _remember(self) -> None:
        self.settings.setValue(HIDE_KEY, self.hide_check.isChecked())
        self.settings.sync()
