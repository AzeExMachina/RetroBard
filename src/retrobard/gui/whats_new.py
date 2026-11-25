# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""The "What's new" window: the changelog shipped with the app, read offline.

After an update, RetroBard says once that it was updated and offers this window.
"""

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from retrobard import __version__, changelog
from retrobard.app.updates import parse_version
from retrobard.gui.quick_start import HIDE_KEY

SEEN_KEY = "whats_new/seen_version"


def updated_since_last_run(settings: QSettings, current: str = __version__) -> bool:
    """True once after RetroBard was updated; records the running version.

    A first install says nothing. Versions before 0.3.1 did not record a version,
    so an earlier run is recognised by the quick start guide's saved choice.
    """
    seen = settings.value(SEEN_KEY)
    if seen is None:
        updated = settings.contains(HIDE_KEY)
    else:
        before, now = parse_version(str(seen)), parse_version(current)
        updated = before is not None and now is not None and now > before
    if str(seen) != current:
        settings.setValue(SEEN_KEY, current)
        settings.sync()
    return updated


class WhatsNewDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, path: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle("What's new in RetroBard")
        self.resize(560, 520)
        path = path or changelog.changelog_path()
        try:
            items = changelog.released(path.read_text(encoding="utf-8"))
        except OSError:
            items = []

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        if items:
            self.browser.setMarkdown(changelog.as_markdown(items, __version__))
        else:
            self.browser.setPlainText(
                f"The list of changes ({changelog.FILE_NAME}) was not found next to "
                "RetroBard. You can read it on the download page."
            )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.browser)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.browser.toPlainText()
