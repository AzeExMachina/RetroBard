# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""RetroBard's window: Play, Setup, Letters and Options tabs, plus a tray icon."""

import faulthandler
import io
import re
import signal
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from retrobard import __version__
from retrobard.config import GAME_ID_HINT, AppSettings, is_valid_game_id
from retrobard.gui.about_dialog import AboutDialog
from retrobard.gui.letters_page import LettersPage
from retrobard.gui.options_page import OptionsPage
from retrobard.gui.play_page import PlayPage
from retrobard.gui.quick_start import QuickStartDialog, app_settings, should_show
from retrobard.gui.setup_page import SetupPage
from retrobard.gui.state import AppState, StatusBridge
from retrobard.gui.updater import Updater
from retrobard.paths import is_frozen, user_data_dir

GAME_NAME_RE = re.compile(r"[^a-z0-9_]+")


def make_icon(active: bool = False) -> QIcon:
    """A simple drawn icon (speech bubble), green while a session is running."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(40, 170, 110) if active else QColor(90, 100, 120))
    painter.drawRoundedRect(4, 6, 56, 40, 12, 12)
    painter.drawPolygon([_pt(18, 44), _pt(14, 60), _pt(32, 44)])
    painter.setPen(QColor(255, 255, 255))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(22)
    painter.setFont(font)
    painter.drawText(4, 6, 56, 40, Qt.AlignmentFlag.AlignCenter, "RB")
    painter.end()
    return QIcon(pixmap)


def _pt(x: int, y: int):
    from PySide6.QtCore import QPoint

    return QPoint(x, y)


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings | None = None, ui_settings: QSettings | None = None):
        super().__init__()
        # Per-user window preferences, such as hiding the quick start guide
        self.ui_settings = ui_settings or app_settings()
        self.setWindowTitle(f"RetroBard {__version__}")
        self.setWindowIcon(make_icon())
        self.resize(980, 680)

        self.state = AppState(settings)
        self.bridge = StatusBridge()
        self.updater = Updater(self)
        self.updater.restart_requested.connect(self.quit)

        self.game_combo = QComboBox()
        self.game_combo.setMinimumWidth(220)
        self.new_game_button = QPushButton("New game...")
        self.guide_button = QPushButton("Quick start")
        self.about_button = QPushButton("About")
        top = QHBoxLayout()
        top.addWidget(QLabel("Game:"))
        top.addWidget(self.game_combo)
        top.addWidget(self.new_game_button)
        top.addStretch(1)
        top.addWidget(self.guide_button)
        top.addWidget(self.about_button)

        self.play_page = PlayPage(self.state, self.bridge)
        self.setup_page = SetupPage(self.state)
        self.letters_page = LettersPage(self.state)
        self.options_page = OptionsPage(self.state)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.play_page, "Play")
        self.tabs.addTab(self.setup_page, "Setup")
        self.tabs.addTab(self.letters_page, "Letters")
        self.tabs.addTab(self.options_page, "Options")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.credit_label = QLabel(f"RetroBard {__version__} · by Aze")
        self.credit_label.setStyleSheet("color: gray")
        self.statusBar().addPermanentWidget(self.credit_label)

        self.game_combo.currentTextChanged.connect(self._on_game_picked)
        self.new_game_button.clicked.connect(self.ask_new_game)
        self.guide_button.clicked.connect(self.show_quick_start)
        self.about_button.clicked.connect(self.show_about)
        self.state.games_changed.connect(self._fill_games)
        self.state.game_changed.connect(self._show_selected_game)
        self.play_page.teach_requested.connect(lambda: self.tabs.setCurrentWidget(self.letters_page))
        self.play_page.running_changed.connect(self._on_running_changed)
        self.setup_page.guide_requested.connect(self.show_quick_start)
        self._fill_games()

        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._build_tray()

        # A game with no linked window starts on the Setup tab
        if not self.state.game:
            self.tabs.setCurrentWidget(self.setup_page)

    # ----------------------------------------------------------------------- games

    def _fill_games(self) -> None:
        self.game_combo.blockSignals(True)
        self.game_combo.clear()
        self.game_combo.addItems(self.state.games())
        self.game_combo.setCurrentText(self.state.game)
        self.game_combo.blockSignals(False)

    def _show_selected_game(self, game: str) -> None:
        self.game_combo.blockSignals(True)
        self.game_combo.setCurrentText(game)
        self.game_combo.blockSignals(False)

    def _on_game_picked(self, game: str) -> None:
        if self.play_page.running:
            # Switching games while one is being voiced would be confusing: stop first
            self.play_page.stop()
        self.state.select_game(game)

    def ask_new_game(self) -> None:
        name, accepted = QInputDialog.getText(
            self, "New game", "Short name for the game (letters, digits, underscores):"
        )
        if not accepted:
            return
        game = GAME_NAME_RE.sub("_", name.strip().lower()).strip("_")
        if not game:
            return
        if not is_valid_game_id(game):
            QMessageBox.warning(self, "New game", f"{GAME_ID_HINT} Use at most 64 characters.")
            return
        self.state.add_game(game)
        self.tabs.setCurrentWidget(self.setup_page)

    # ------------------------------------------------------------------ quick start

    def show_quick_start(self) -> None:
        QuickStartDialog(self.ui_settings, self).exec()

    def show_about(self) -> None:
        AboutDialog(self, check_updates=self.updater.check).exec()

    def show_quick_start_if_wanted(self) -> None:
        if should_show(self.ui_settings):
            self.show_quick_start()

    # ------------------------------------------------------------------------ tray

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("RetroBard")
        menu = QMenu()
        self.tray_toggle = QAction("Start", self)
        self.tray_toggle.triggered.connect(self.play_page.toggle)
        show_action = QAction("Show window", self)
        show_action.triggered.connect(self.show_window)
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        update_action = QAction("Check for updates", self)
        update_action.triggered.connect(self.updater.check)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit)
        menu.addAction(self.tray_toggle)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(about_action)
        menu.addAction(update_action)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu  # keep it alive
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def _on_running_changed(self, running: bool) -> None:
        icon = make_icon(active=running)
        self.setWindowIcon(icon)
        if self.tray is not None:
            self.tray.setIcon(icon)
            self.tray.setToolTip(
                f"RetroBard - reading {self.state.game}" if running else "RetroBard"
            )
            self.tray_toggle.setText("Stop" if running else "Start")

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ---------------------------------------------------------------------- closing

    def closeEvent(self, event) -> None:
        if self.play_page.running and self.tray is not None:
            # Keep voicing the game; the window comes back from the tray icon
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "RetroBard is still running",
                "Use the tray icon to show the window, stop, or quit.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
            return
        self.shutdown()
        event.accept()
        # Closing only hides a QMainWindow, so end the app explicitly
        QApplication.instance().quit()

    def shutdown(self) -> None:
        self.play_page.stop()
        self.setup_page.shutdown()
        self.letters_page.shutdown()
        if self.tray is not None:
            self.tray.hide()

    def quit(self) -> None:
        self.shutdown()
        QApplication.instance().quit()


LOG_MAX_BYTES = 1_000_000


def home_pattern(home: str, windows: bool) -> re.Pattern[str]:
    """Matches the user's home folder in text. On Windows any case and either slash."""
    if windows:
        parts = [re.escape(part) for part in re.split(r"[\\/]+", home.rstrip("\\/"))]
        body = r"[\\/]+".join(parts)
        return re.compile(body + r"(?![\w-])", re.IGNORECASE)
    return re.compile(re.escape(home.rstrip("/")) + r"(?![\w-])")


class HomeRedactingWriter(io.TextIOBase):
    """A text stream that writes the user's home folder as ~ (keeps names out of logs)."""

    def __init__(self, stream, home: str | None = None, windows: bool | None = None):
        super().__init__()
        self.stream = stream
        self.pattern = home_pattern(
            home if home is not None else str(Path.home()),
            sys.platform == "win32" if windows is None else windows,
        )

    def write(self, text: str) -> int:
        self.stream.write(self.pattern.sub("~", text))
        return len(text)

    def flush(self) -> None:
        self.stream.flush()

    @property
    def encoding(self) -> str:
        return self.stream.encoding

    def writable(self) -> bool:
        return True


def log_file() -> Path:
    return user_data_dir() / "logs" / "retrobard.log"


def start_log_file() -> None:
    """The packaged app has no console, so output and errors go to a log file.

    The log holds start-up messages and errors only: the dialogue that is read is
    shown in the window, never written here."""
    path = log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        path.replace(path.with_suffix(".old.log"))
    stream = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - open for the app's lifetime
    sys.stdout = sys.stderr = HomeRedactingWriter(stream)
    faulthandler.enable(stream)  # also record hard crashes; needs the real file
    print(f"--- RetroBard {__version__} started {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z}")


def report_errors() -> None:
    """Unexpected errors are written to the log (or console) and shown in a message box."""

    def show(exc_type, exc, tb) -> None:
        traceback.print_exception(exc_type, exc, tb)
        if QApplication.instance() is not None and threading.current_thread() is threading.main_thread():
            where = f"\n\nDetails were saved in:\n{log_file()}" if is_frozen() else ""
            QMessageBox.critical(None, "RetroBard", f"Something went wrong: {exc}{where}")

    sys.excepthook = show
    threading.excepthook = lambda args: traceback.print_exception(
        args.exc_type, args.exc_value, args.exc_traceback
    )


def main() -> int:
    if is_frozen():
        start_log_file()
    report_errors()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("RetroBard")
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    # After the window is on screen, so the guide opens on top of it
    QTimer.singleShot(0, window.show_quick_start_if_wanted)
    # Without a tray there is no other way back to a hidden window, so closing quits
    if window.tray is None:
        app.setQuitOnLastWindowClosed(True)

    # Ctrl+C in the console quits cleanly. Python only handles signals while its own
    # code runs, so a timer briefly hands control back from Qt's event loop.
    signal.signal(signal.SIGINT, lambda *_: window.quit())
    wake_timer = QTimer()
    wake_timer.timeout.connect(lambda: None)
    wake_timer.start(200)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
