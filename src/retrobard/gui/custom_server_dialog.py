# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Setting up a custom voice server (anything that speaks the OpenAI speech API)."""

import threading

import keyring.errors
from pydantic import ValidationError
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from retrobard.adapters.tts.openai_compat_tts import CustomTTSError, OpenAICompatibleTTS
from retrobard.config import AppSettings, CustomTTSConfig
from retrobard.credentials import (
    CUSTOM_ACCOUNT,
    custom_tts_api_key,
    forget_api_key,
    store_api_key,
    stored_api_key,
)
from retrobard.domain.models import VoiceProfile

FORMATS = [
    ("PCM - starts playing at once (recommended)", "pcm"),
    ("WAV - waits for the whole line", "wav"),
    ("MP3 - waits for the whole line", "mp3"),
]
TEST_LINE = "Halt, travelers! The road ahead is dangerous."

# Filled in when the user picks a preset; they can still change every field
PRESETS = {
    "Kokoro-FastAPI (free, on this computer)": CustomTTSConfig(),
    "OpenAI": CustomTTSConfig(
        base_url="https://api.openai.com/v1", model="gpt-4o-mini-tts", default_voice="alloy"
    ),
    "Speaches (free, on this computer)": CustomTTSConfig(
        base_url="http://localhost:8000/v1",
        model="speaches-ai/Kokoro-82M-v1.0-ONNX",
        default_voice="af_heart",
    ),
}


class CustomServerDialog(QDialog):
    # (message, success) from the connection test running on a background thread
    _test_finished = Signal(str, bool)

    def __init__(self, settings: AppSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Custom voice server")
        self.setMinimumWidth(520)

        intro = QLabel(
            "Use any text-to-speech service that speaks the <b>OpenAI speech API</b>: "
            "OpenAI itself, or a free server running on this computer such as "
            "Kokoro-FastAPI, Speaches or LocalAI. Pick a preset to fill in the usual "
            "values, then adjust them to match your server."
        )
        intro.setWordWrap(True)

        try:
            current = CustomTTSConfig.load(settings.custom_tts_file)
        except (OSError, ValueError):
            current = CustomTTSConfig()

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Choose a preset...", None)
        for name in PRESETS:
            self.preset_combo.addItem(name, name)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("http://localhost:8880/v1")
        self.model_edit = QLineEdit()
        self.voice_edit = QLineEdit()
        self.format_combo = QComboBox()
        for label, value in FORMATS:
            self.format_combo.addItem(label, value)
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(8000, 48000)
        self.rate_spin.setSingleStep(1000)
        self.rate_spin.setSuffix(" Hz")
        self.format_combo.currentIndexChanged.connect(self._refresh_rate)
        self._fill(current)

        has_key = bool(stored_api_key(CUSTOM_ACCOUNT))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(
            "A key is saved - type a new one to replace it"
            if has_key
            else "Only if the server asks for one"
        )
        self.remove_key_button = QPushButton("Remove key")
        self.remove_key_button.setEnabled(has_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.remove_key_button)

        form = QFormLayout()
        form.addRow("Preset", self.preset_combo)
        form.addRow("Server address", self.url_edit)
        form.addRow("Model", self.model_edit)
        form.addRow("Default voice", self.voice_edit)
        form.addRow("Audio format", self.format_combo)
        form.addRow("PCM sample rate", self.rate_spin)
        form.addRow("API key", key_row)

        voices_hint = QLabel(
            "Every character uses the default voice until you give them their own in "
            "the game's profile, under <code>custom_voices:</code> (same layout as "
            "<code>voices:</code>, with the server's voice names)."
        )
        voices_hint.setWordWrap(True)
        privacy = QLabel(
            "While voices are on, the dialogue read from the game is sent to this server. "
            "A server on this computer keeps it here; a service on the internet follows "
            "its own privacy terms. The key is kept in Windows Credential Manager."
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: gray;")

        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.test_button = QPushButton("Test")
        self.save_button = QPushButton("Save")
        self.save_button.setDefault(True)
        cancel_button = QPushButton("Cancel")
        buttons = QHBoxLayout()
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(voices_hint)
        layout.addWidget(privacy)
        layout.addWidget(self.message_label)
        layout.addLayout(buttons)

        self.test_button.clicked.connect(self.test)
        self.save_button.clicked.connect(self.save)
        self.remove_key_button.clicked.connect(self.remove_key)
        cancel_button.clicked.connect(self.reject)
        self._test_finished.connect(self._on_test_finished)

    # ------------------------------------------------------------------ fields

    def _fill(self, config: CustomTTSConfig) -> None:
        self.url_edit.setText(config.base_url)
        self.model_edit.setText(config.model)
        self.voice_edit.setText(config.default_voice)
        self.format_combo.setCurrentIndex(self.format_combo.findData(config.response_format))
        self.rate_spin.setValue(config.sample_rate)
        self._refresh_rate()

    def _apply_preset(self) -> None:
        name = self.preset_combo.currentData()
        if name:
            self._fill(PRESETS[name])

    def _refresh_rate(self) -> None:
        self.rate_spin.setEnabled(self.format_combo.currentData() == "pcm")

    def config(self) -> CustomTTSConfig:
        """The settings as typed. Raises ValueError with a readable message."""
        try:
            return CustomTTSConfig(
                base_url=self.url_edit.text(),
                model=self.model_edit.text(),
                default_voice=self.voice_edit.text(),
                response_format=self.format_combo.currentData(),
                sample_rate=self.rate_spin.value(),
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            field = {"base_url": "Server address", "default_voice": "Default voice"}.get(
                str(first["loc"][0]), str(first["loc"][0]).capitalize()
            )
            raise ValueError(f"{field}: {first['msg'].removeprefix('Value error, ')}") from exc

    def _typed_key(self) -> str:
        return self.key_edit.text().strip()

    def _insecure_key_warning(self, config: CustomTTSConfig, key: str) -> str:
        if key and config.base_url.startswith("http://") and not config.is_local():
            return (
                "Warning: the key would be sent unencrypted (http://) to another computer. "
                "Use https:// unless the server is on your own network."
            )
        return ""

    # ----------------------------------------------------------------- actions

    def test(self) -> None:
        try:
            config = self.config()
        except ValueError as exc:
            self.message_label.setText(str(exc))
            return
        key = self._typed_key() or custom_tts_api_key(self.settings)
        self.test_button.setEnabled(False)
        self.message_label.setText(f"Asking {config.base_url} for a test line...")

        def work() -> None:
            tts = OpenAICompatibleTTS(config, api_key=key)
            try:
                size = sum(
                    len(chunk)
                    for chunk in tts.synthesize_stream(
                        TEST_LINE, VoiceProfile(voice_id=config.default_voice)
                    )
                )
            except CustomTTSError as exc:
                self._test_finished.emit(str(exc), False)
            except Exception as exc:  # noqa: BLE001 - report anything instead of hanging
                self._test_finished.emit(f"Test failed: {exc}", False)
            else:
                if size:
                    self._test_finished.emit(
                        f"It works: the server sent {size / 1024:.0f} KB of audio.", True
                    )
                else:
                    self._test_finished.emit("The server answered but sent no audio.", False)
            finally:
                tts.close()

        threading.Thread(target=work, daemon=True).start()

    def _on_test_finished(self, message: str, _ok: bool) -> None:
        self.test_button.setEnabled(True)
        self.message_label.setText(message)

    def save(self) -> None:
        try:
            config = self.config()
        except ValueError as exc:
            self.message_label.setText(str(exc))
            return
        key = self._typed_key()
        if any(ch.isspace() for ch in key):
            self.message_label.setText("That does not look like a key: it contains spaces.")
            return
        warning = self._insecure_key_warning(config, key or custom_tts_api_key(self.settings))
        if warning and self.message_label.text() != warning:
            # Shown once; pressing Save again goes ahead
            self.message_label.setText(warning)
            return
        try:
            config.save(self.settings.custom_tts_file)
            if key:
                store_api_key(key, CUSTOM_ACCOUNT)
        except (keyring.errors.KeyringError, OSError, RuntimeError) as exc:
            self.message_label.setText(f"Could not save: {exc}")
            return
        self.accept()

    def remove_key(self) -> None:
        forget_api_key(CUSTOM_ACCOUNT)
        self.remove_key_button.setEnabled(False)
        self.key_edit.setPlaceholderText("Only if the server asks for one")
        self.message_label.setText("The saved key was removed.")
