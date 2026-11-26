# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""The page you leave open while playing: start/stop and what RetroBard is doing."""

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from retrobard.app.session import (
    InputSource,
    SessionError,
    SessionOptions,
    TTSBackend,
    VoiceSession,
)
from retrobard.app.setup import load_or_create_profile
from retrobard.credentials import elevenlabs_api_key
from retrobard.domain.status import StatusEvent, StatusKind
from retrobard.gui.api_key_dialog import ApiKeyDialog
from retrobard.gui.custom_server_dialog import CustomServerDialog
from retrobard.gui.state import AppState, StatusBridge

# The free voice server first; ElevenLabs needs an account and, for most voices,
# a paid plan
VOICE_CHOICES = [
    ("Custom server (OpenAI-compatible)", TTSBackend.CUSTOM),
    ("ElevenLabs (AI voices)", TTSBackend.ELEVENLABS),
    ("Silent test (no voice, no credits)", TTSBackend.MOCK),
]
SOURCE_CHOICES = [
    ("The game window", InputSource.OCR),
    ("A helper script (UDP)", InputSource.UDP),
    ("Both", InputSource.BOTH),
]


class PlayPage(QWidget):
    running_changed = Signal(bool)
    teach_requested = Signal()
    # Results of starting a session on a background thread
    _start_finished = Signal(object, str)  # (session or None, error message)

    def __init__(self, state: AppState, bridge: StatusBridge):
        super().__init__()
        self.state = state
        self.bridge = bridge
        self._ambiguous_window = False
        self.session: VoiceSession | None = None
        self._starting = False

        self.voice_combo = QComboBox()
        for label, backend in VOICE_CHOICES:
            self.voice_combo.addItem(label, backend)
        self.source_combo = QComboBox()
        for label, source in SOURCE_CHOICES:
            self.source_combo.addItem(label, source)

        self.key_button = QPushButton("ElevenLabs key...")
        self.key_label = QLabel()
        voice_row = QHBoxLayout()
        voice_row.addWidget(self.voice_combo, 1)
        voice_row.addWidget(self.key_button)
        voice_row.addWidget(self.key_label)

        options = QFormLayout()
        options.addRow("Voice", voice_row)
        options.addRow("Read dialogue from", self.source_combo)

        self.start_button = QPushButton("Start")
        self.start_button.setMinimumHeight(40)
        self.start_button.clicked.connect(self.toggle)

        self.state_label = QLabel("Stopped")
        self.window_label = QLabel("-")
        self.line_label = QLabel("-")
        self.line_label.setWordWrap(True)
        status_form = QFormLayout()
        status_form.addRow("State", self.state_label)
        status_form.addRow("Game window", self.window_label)
        status_form.addRow("Now speaking", self.line_label)
        status_box = QGroupBox("Status")
        status_box.setLayout(status_form)

        self.unknown_label = QLabel()
        self.unknown_label.setWordWrap(True)
        self.unknown_button = QPushButton("Teach letters")
        self.unknown_button.clicked.connect(self.teach_requested.emit)
        unknown_row = QHBoxLayout()
        unknown_row.addWidget(self.unknown_label, 1)
        unknown_row.addWidget(self.unknown_button)
        self.unknown_box = QGroupBox("Letters not recognised")
        self.unknown_box.setLayout(unknown_row)
        self.unknown_box.hide()

        self.details_check = QCheckBox("Show details")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)

        layout = QVBoxLayout(self)
        layout.addLayout(options)
        layout.addWidget(self.start_button)
        layout.addWidget(status_box)
        layout.addWidget(self.unknown_box)
        layout.addWidget(self.details_check)
        layout.addWidget(self.log, 1)

        self.key_button.clicked.connect(self.configure_voice)
        self.voice_combo.currentIndexChanged.connect(lambda _i: self._refresh_controls())
        bridge.event.connect(self.on_status)
        self._start_finished.connect(self._on_start_finished)
        state.game_changed.connect(lambda _game: self._refresh_controls())
        state.game_data_saved.connect(self._apply_saved_options)
        self._refresh_controls()

    # ------------------------------------------------------------------ start/stop

    @property
    def running(self) -> bool:
        return self.session is not None and self.session.running

    def toggle(self) -> None:
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if self.running or self._starting or not self.state.game:
            return
        backend = self._backend()
        if backend == TTSBackend.ELEVENLABS and not self.has_api_key():
            self.ask_api_key()
            if not self.has_api_key():
                self.state_label.setText("ElevenLabs needs your API key (ElevenLabs key... button)")
                return
        if backend == TTSBackend.CUSTOM and not self.has_custom_server():
            self.ask_custom_server()
            if not self.has_custom_server():
                self.state_label.setText("Set up your voice server first (Server... button)")
                return
        self._starting = True
        self.unknown_box.hide()
        self._refresh_controls()

        session = VoiceSession(
            self.state.settings,
            SessionOptions(
                game=self.state.game,
                tts=backend,
                source=InputSource(self.source_combo.currentData()),
            ),
            status=self.bridge.sink,
        )

        def work() -> None:
            # Starting can take a moment (loading a text engine), so keep the window responsive
            try:
                session.start()
            except SessionError as exc:
                self._start_finished.emit(None, str(exc))
            except Exception as exc:  # noqa: BLE001 - unexpected: still tell the user instead of hanging
                self._start_finished.emit(None, f"Could not start: {exc}")
            else:
                self._start_finished.emit(session, "")

        threading.Thread(target=work, daemon=True).start()

    def _on_start_finished(self, session: VoiceSession | None, error: str) -> None:
        self._starting = False
        if session is None:
            self._append(f"Could not start: {error}")
            self.state_label.setText(error)
        else:
            self.session = session
        self._refresh_controls()
        self.running_changed.emit(self.running)

    def _apply_saved_options(self, game: str) -> None:
        """Lets Options changes reach a running session without a restart. The parser
        reads these flags for every line, so updating the session's profile is enough."""
        running = self.session.profile if self.running else None
        if running is None or game != self.state.game:
            return
        saved = load_or_create_profile(self.state.settings, game)
        running.voices.narrate_unnamed = saved.voices.narrate_unnamed
        running.dialogue_rules.skip_choices = saved.dialogue_rules.skip_choices

    def stop(self) -> None:
        if self.session is not None:
            self.session.stop()
            self.session = None
        self.line_label.setText("-")
        self.window_label.setText("-")
        self._ambiguous_window = False
        self._refresh_controls()
        self.running_changed.emit(False)

    # ------------------------------------------------------- voice service setup

    def _backend(self) -> TTSBackend:
        # Qt hands the stored values back as plain strings
        return TTSBackend(self.voice_combo.currentData())

    def has_api_key(self) -> bool:
        return bool(elevenlabs_api_key(self.state.settings))

    def has_custom_server(self) -> bool:
        return self.state.settings.custom_tts_file.exists()

    def configure_voice(self) -> None:
        if self._backend() == TTSBackend.CUSTOM:
            self.ask_custom_server()
        else:
            self.ask_api_key()

    def ask_api_key(self) -> None:
        ApiKeyDialog(self).exec()
        self._refresh_controls()

    def ask_custom_server(self) -> None:
        CustomServerDialog(self.state.settings, self).exec()
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        running = self.running
        backend = self._backend()
        self.key_button.setVisible(backend != TTSBackend.MOCK)
        self.key_label.setVisible(backend != TTSBackend.MOCK)
        if backend == TTSBackend.CUSTOM:
            self.key_button.setText("Server...")
            self.key_label.setText("Set up" if self.has_custom_server() else "Not set up yet")
        else:
            self.key_button.setText("ElevenLabs key...")
            self.key_label.setText("Key saved" if self.has_api_key() else "No key yet")
        self.start_button.setText("Starting..." if self._starting else "Stop" if running else "Start")
        self.start_button.setEnabled(not self._starting and bool(self.state.game))
        self.voice_combo.setEnabled(not running and not self._starting)
        self.source_combo.setEnabled(not running and not self._starting)
        if self._starting:
            self.state_label.setText("Starting...")
        elif running:
            self.state_label.setText(f"Running for {self.state.game}")
        elif not self.state.game:
            self.state_label.setText("Add a game on the Setup tab first")
        elif self.state_label.text().startswith(("Running", "Starting")):
            self.state_label.setText("Stopped")

    # ---------------------------------------------------------------------- status

    def on_status(self, event: StatusEvent) -> None:
        if event.kind == StatusKind.DEBUG and not self.details_check.isChecked():
            return
        if event.kind == StatusKind.LINE:
            # Shown on screen only; the log line carries no dialogue text
            self.line_label.setText(f"{event.data['speaker']}: {event.data['text']}")
        elif event.kind == StatusKind.WINDOW:
            self.window_label.setText(self._window_text(event.data))
        elif event.kind == StatusKind.UNKNOWN_LETTERS:
            self.unknown_label.setText(
                f"{event.data['count']} letter shape(s) on screen are not in the taught font, "
                f"so they were left out of the spoken line."
            )
            self.unknown_box.show()
        elif event.kind == StatusKind.SESSION and event.data.get("running"):
            self.window_label.setText("Waiting for dialogue")
        self._append(event.message)

    def _window_text(self, data: dict) -> str:
        reason = data.get("reason")
        if data.get("available"):
            self._ambiguous_window = False
            title = data.get("title")
            if title:
                return f"Reading: {title if len(title) <= 60 else title[:59] + '…'}"
            return "Found"
        if reason == "ambiguous":
            self._ambiguous_window = True
        elif reason is not None:
            self._ambiguous_window = False
        # The reader's own "paused" event follows the ambiguity report: keep it shown
        if self._ambiguous_window:
            return "Several matching windows: open Setup"
        return "Not open or minimised"

    def _append(self, message: str) -> None:
        self.log.appendPlainText(message)
