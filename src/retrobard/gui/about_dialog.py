# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""The About dialog: who made RetroBard, its license, privacy summary and credits."""

import sys
from collections.abc import Callable
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from retrobard import __version__
from retrobard.about import (
    APP_NAME,
    AUTHOR,
    CONTACT,
    COPYRIGHT_YEAR,
    DISCLAIMER,
    HANDLE,
    ITCH_URL,
    LICENSE_NAME,
    PRIVACY_SUMMARY,
    REPO_URL,
    WEBSITE_URL,
)
from retrobard.paths import is_frozen

NOTICES_FILE = "THIRD-PARTY-NOTICES.txt"


def notices_path() -> Path | None:
    """THIRD-PARTY-NOTICES.txt next to RetroBard.exe; None when running from source."""
    if not is_frozen():
        return None
    return Path(sys.executable).parent / NOTICES_FILE


def _link(label: str, url: str) -> str:
    return f'{label}: <a href="{escape(url)}">{escape(url)}</a>'


class AboutDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        check_updates: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(480)

        title = QLabel(f"<b>{APP_NAME} {__version__}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        credit = QLabel(f"Created by {AUTHOR} ({HANDLE})")
        license_line = QLabel(f"© {COPYRIGHT_YEAR} {AUTHOR}. Licensed under {LICENSE_NAME}.")

        layout = QVBoxLayout(self)
        for label in (title, credit, license_line):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)

        links = [
            _link(label, url)
            for label, url in (
                ("Source code", REPO_URL),
                ("itch.io", ITCH_URL),
                ("Website", WEBSITE_URL),
                ("Contact", f"mailto:{CONTACT}" if CONTACT else ""),
            )
            if url
        ]
        self.links_label = QLabel("<br>".join(links))
        self.links_label.setTextFormat(Qt.TextFormat.RichText)
        self.links_label.setOpenExternalLinks(True)
        self.links_label.setVisible(bool(links))
        layout.addWidget(self.links_label)

        for text in (PRIVACY_SUMMARY, DISCLAIMER):
            note = QLabel(text)
            note.setWordWrap(True)
            note.setStyleSheet("color: gray")
            layout.addWidget(note)

        self.licenses_button = QPushButton("Third-party licenses")
        self.about_qt_button = QPushButton("About Qt")
        # Contacts GitHub only when pressed; RetroBard never checks on its own
        self.update_button = QPushButton("Check for updates")
        self.update_button.setToolTip("Asks GitHub whether a newer RetroBard is available.")
        self.update_button.setVisible(check_updates is not None)
        self.close_button = QPushButton("Close")
        self.close_button.setDefault(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.update_button)
        buttons.addWidget(self.licenses_button)
        buttons.addWidget(self.about_qt_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        if check_updates is not None:
            self.update_button.clicked.connect(check_updates)
        self.licenses_button.clicked.connect(self.show_licenses)
        self.about_qt_button.clicked.connect(lambda: QMessageBox.aboutQt(self))
        self.close_button.clicked.connect(self.accept)

    def text(self) -> str:
        """Everything the dialog says, as plain text (for tests)."""
        return "\n".join(label.text() for label in self.findChildren(QLabel))

    def show_licenses(self) -> None:
        path = notices_path()
        if path is None or not path.exists():
            QMessageBox.information(
                self,
                "Third-party licenses",
                f"{NOTICES_FILE} is generated when the app is built, and sits next to "
                f"{APP_NAME}.exe.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
