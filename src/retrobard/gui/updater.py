# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""The "Check for updates" flow: ask GitHub, offer the new version, install it.

Runs only when the user presses the button; RetroBard never checks on its own.
The network work happens on a background thread and reports back through signals.
"""

import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from retrobard import __version__
from retrobard.about import ITCH_URL
from retrobard.app import updates
from retrobard.paths import is_frozen, user_data_dir

NOTES_PREVIEW_CHARS = 1200


class Updater(QObject):
    # The new version is unpacked and the swap script started: the app must quit now
    restart_requested = Signal()
    busy_changed = Signal(bool)

    _checked = Signal(object, str)  # (Release or None, error message)
    _progress = Signal(int, int)  # (bytes done, bytes total)
    _prepared = Signal(object, str)  # (new app folder or None, error message)

    def __init__(
        self,
        parent: QWidget,
        client_factory: Callable = updates.make_client,
        frozen: Callable[[], bool] = is_frozen,
        folder: Callable[[], Path] = updates.install_dir,
    ):
        super().__init__(parent)
        self._window = parent
        self._client_factory = client_factory
        self._frozen = frozen
        self._folder = folder
        self._busy = False
        self._cancel = threading.Event()
        self._progress_dialog: QProgressDialog | None = None
        self._release: updates.Release | None = None
        self._checked.connect(self._on_checked)
        self._progress.connect(self._on_progress)
        self._prepared.connect(self._on_prepared)

    @property
    def busy(self) -> bool:
        return self._busy

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.busy_changed.emit(busy)

    def _show(self, box: QMessageBox) -> None:
        """Shows a message box modally (tests replace this to read it instead)."""
        box.exec()

    def _open(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    # ---------------------------------------------------------------------- check

    def check(self) -> None:
        if self._busy:
            return
        self._set_busy(True)

        def work() -> None:
            try:
                with self._client_factory() as client:
                    release = updates.fetch_latest_release(client)
            except updates.UpdateError as exc:
                self._checked.emit(None, str(exc))
            except Exception as exc:  # noqa: BLE001 - report anything instead of hanging
                self._checked.emit(None, f"The check failed: {exc}")
            else:
                self._checked.emit(release, "")

        threading.Thread(target=work, daemon=True).start()

    def _box(self, icon: QMessageBox.Icon, text: str, informative: str = "") -> QMessageBox:
        box = QMessageBox(self._window)
        box.setWindowTitle("Check for updates")
        box.setIcon(icon)
        box.setText(text)
        if informative:
            box.setInformativeText(informative)
        return box

    def _on_checked(self, release: updates.Release | None, error: str) -> None:
        if release is None:
            self._set_busy(False)
            box = self._box(QMessageBox.Icon.Warning, "Could not check for updates.", error)
            page = box.addButton("Open download page", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Close)
            self._show(box)
            if box.clickedButton() is page:
                self._open(updates.RELEASES_PAGE)
            return
        if not updates.is_newer(release.version, __version__):
            self._set_busy(False)
            self._show(
                self._box(
                    QMessageBox.Icon.Information,
                    f"You have the latest version of RetroBard ({__version__}).",
                )
            )
            return
        self._offer(release)

    def _offer(self, release: updates.Release) -> None:
        folder = self._folder()
        blocker = updates.update_blocker(folder, self._frozen())
        if blocker is None and not release.can_download:
            blocker = "This release has no Windows download yet."
        notes = release.notes or "No release notes."
        if len(notes) > NOTES_PREVIEW_CHARS:
            notes = notes[:NOTES_PREVIEW_CHARS].rstrip() + "..."
        informative = notes if blocker is None else f"{blocker}\n\n{notes}"
        box = self._box(
            QMessageBox.Icon.Information,
            f"RetroBard {release.version} is available (you have {__version__}).",
            informative,
        )
        box.setTextFormat(Qt.TextFormat.PlainText)
        install = None
        if blocker is None:
            install = box.addButton("Download and install", QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(install)
        page = box.addButton("Open download page", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        self._show(box)
        clicked = box.clickedButton()
        if install is not None and clicked is install:
            self._download(release, folder)
            return
        self._set_busy(False)
        if clicked is page:
            self._open(ITCH_URL if updates.managed_by_itch(folder) else release.page_url)

    # ------------------------------------------------------------------- download

    def _download(self, release: updates.Release, folder: Path) -> None:
        self._release = release
        self._cancel.clear()
        dialog = QProgressDialog(
            f"Downloading RetroBard {release.version}...", "Cancel", 0, 100, self._window
        )
        dialog.setWindowTitle("Check for updates")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self._cancel.set)
        dialog.show()
        self._progress_dialog = dialog
        download_dir = Path(tempfile.gettempdir()) / "RetroBard-update"

        def work() -> None:
            zip_path = None
            try:
                with self._client_factory() as client:
                    zip_path = updates.download_release(
                        client, release, download_dir,
                        progress=self._progress.emit,
                        cancelled=self._cancel.is_set,
                    )
                new_app = updates.extract_update(zip_path, folder, release.version)
            except updates.UpdateCancelled:
                self._prepared.emit(None, "")
            except updates.UpdateError as exc:
                self._prepared.emit(None, str(exc))
            except Exception as exc:  # noqa: BLE001 - report anything instead of hanging
                self._prepared.emit(None, f"The update failed: {exc}")
            else:
                self._prepared.emit(new_app, "")
            finally:
                if zip_path is not None:
                    zip_path.unlink(missing_ok=True)

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        if self._progress_dialog is None:
            return
        if total > 0:
            self._progress_dialog.setMaximum(100)
            self._progress_dialog.setValue(min(100, done * 100 // total))
        else:
            self._progress_dialog.setMaximum(0)  # unknown size: busy indicator

    def _on_prepared(self, new_app: Path | None, error: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        release = self._release
        if new_app is None:
            self._set_busy(False)
            if error:  # empty when the user cancelled
                self._show(self._box(QMessageBox.Icon.Warning, "The update failed.", error))
            return
        box = self._box(
            QMessageBox.Icon.Information,
            f"RetroBard {release.version if release else ''} is ready to install.",
            "RetroBard will close, update itself and start again. Your settings, "
            "profiles and letters are kept. If a game is being voiced, it stops.",
        )
        restart = box.addButton("Restart now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(restart)
        self._show(box)
        if box.clickedButton() is not restart:
            updates.discard_staged(new_app)
            self._set_busy(False)
            return
        try:
            updates.start_swap(
                self._folder(),
                new_app,
                log=user_data_dir() / "logs" / "update.log",
                temp_dir=Path(tempfile.gettempdir()) / "RetroBard-update",
            )
        except OSError as exc:
            updates.discard_staged(new_app)
            self._set_busy(False)
            self._show(
                self._box(QMessageBox.Icon.Warning, "Could not start the update.", str(exc))
            )
            return
        self.restart_requested.emit()
