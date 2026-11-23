# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Entering the user's own ElevenLabs API key."""

import keyring.errors
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from retrobard.credentials import forget_api_key, store_api_key, stored_api_key

KEYS_PAGE = "https://elevenlabs.io/app/settings/api-keys"


class ApiKeyDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("ElevenLabs API key")
        self.setMinimumWidth(480)

        intro = QLabel(
            "RetroBard uses your own ElevenLabs account for the voices, so the "
            "credits used are yours. <b><u>A free account is enough to try it.</u></b><br><br>"
            f'1. Sign in and open <a href="{KEYS_PAGE}">API keys</a> in ElevenLabs.<br>'
            "2. Create a key with access to <b>Text to Speech</b>.<br>"
            "3. Paste it below."
        )
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(
            "A key is saved - paste a new one to replace it" if stored_api_key() else "sk_..."
        )
        self.show_check = QPushButton("Show")
        self.show_check.setCheckable(True)
        self.show_check.toggled.connect(
            lambda shown: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
            )
        )
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.show_check)

        privacy = QLabel(
            "The key is kept in Windows Credential Manager on this computer and is only "
            "sent to ElevenLabs. While voices are on, the dialogue read from the game is "
            "sent to ElevenLabs to be spoken. ElevenLabs may use it to improve its models "
            "unless you opt out in your ElevenLabs account (Terms and Privacy > Data use)."
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: gray;")

        self.message_label = QLabel()
        self.remove_button = QPushButton("Remove saved key")
        self.remove_button.setEnabled(bool(stored_api_key()))
        self.save_button = QPushButton("Save")
        self.save_button.setDefault(True)
        cancel_button = QPushButton("Cancel")
        buttons = QHBoxLayout()
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(key_row)
        layout.addWidget(privacy)
        layout.addWidget(self.message_label)
        layout.addLayout(buttons)

        self.save_button.clicked.connect(self.save)
        self.remove_button.clicked.connect(self.remove)
        cancel_button.clicked.connect(self.reject)

    def save(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.message_label.setText("Paste a key first.")
            return
        if any(ch.isspace() for ch in key):
            self.message_label.setText("That does not look like a key: it contains spaces.")
            return
        try:
            store_api_key(key)
        except (keyring.errors.KeyringError, OSError, RuntimeError) as exc:  # store unavailable
            self.message_label.setText(f"Could not save the key: {exc}")
            return
        self.accept()

    def remove(self) -> None:
        forget_api_key()
        self.accept()
