# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no real window during tests

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from test_glyph_reader import _cfg, _draw
from test_session import SilentPlayer

from retrobard.adapters.ocr.glyph_reader import GlyphFont
from retrobard.app import session as session_module
from retrobard.config import AppSettings, GameProfile, NormRect, WindowBinding
from retrobard.domain.status import StatusEvent, StatusKind
from retrobard.gui.main import MainWindow
from retrobard.gui.region_selector import RegionSelector, frame_to_image


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path: Path, sample_profile: GameProfile, monkeypatch):
    monkeypatch.setattr(session_module, "PcmStreamPlayer", SilentPlayer)
    settings = AppSettings(
        _env_file=None,
        cache_dir=tmp_path / "cache",
        profiles_dir=tmp_path / "profiles",
        custom_tts_file=tmp_path / "custom_tts.json",
        udp_port=0,
        udp_token="gui-test",
    )
    sample_profile.ocr = _cfg()
    sample_profile.save_to_yaml(settings.profiles_dir / "chrono_trigger.yaml")
    # An ini file instead of the registry, so tests never touch real preferences
    ui_settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    main_window = MainWindow(settings, ui_settings)
    yield main_window
    main_window.shutdown()


def _wait_for(qapp, condition, timeout_ms: int = 3000) -> bool:
    waited = 0
    while waited < timeout_ms:
        qapp.processEvents()
        if condition():
            return True
        QTest.qWait(10)
        waited += 10
    return False


class TestRegionSelector:
    def test_frame_becomes_an_image_of_the_same_size(self, qapp) -> None:
        frame = np.zeros((48, 64, 4), dtype=np.uint8)
        frame[..., 2] = 255  # red in BGRA
        image = frame_to_image(frame)
        assert (image.width(), image.height()) == (64, 48)
        assert image.pixelColor(10, 10).red() == 255
        assert image.pixelColor(10, 10).blue() == 0

    def test_dragging_reports_the_box_in_image_pixels(self, qapp) -> None:
        selector = RegionSelector()
        selector.resize(640, 480)
        selector.set_frame(np.zeros((240, 320, 4), dtype=np.uint8))  # shown at 2x
        boxes: list[tuple[int, int, int, int]] = []
        selector.region_selected.connect(lambda *box: boxes.append(box))

        none = Qt.KeyboardModifier.NoModifier
        QTest.mousePress(selector, Qt.MouseButton.LeftButton, none, QPoint(100, 200))
        QTest.mouseMove(selector, QPoint(500, 400))
        QTest.mouseRelease(selector, Qt.MouseButton.LeftButton, none, QPoint(500, 400))

        assert boxes == [(50, 100, 200, 100)]

    def test_a_click_without_dragging_selects_nothing(self, qapp) -> None:
        selector = RegionSelector()
        selector.resize(640, 480)
        selector.set_frame(np.zeros((240, 320, 4), dtype=np.uint8))
        boxes: list = []
        selector.region_selected.connect(lambda *box: boxes.append(box))

        none = Qt.KeyboardModifier.NoModifier
        QTest.mousePress(selector, Qt.MouseButton.LeftButton, none, QPoint(100, 100))
        QTest.mouseRelease(selector, Qt.MouseButton.LeftButton, none, QPoint(101, 101))

        assert boxes == []


class TestMainWindow:
    def test_opens_with_the_existing_game_selected(self, window: MainWindow) -> None:
        assert window.state.game == "chrono_trigger"
        assert window.game_combo.currentText() == "chrono_trigger"
        assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
            "Play",
            "Setup",
            "Letters",
            "Options",
        ]

    def test_adding_a_game_creates_its_profile_and_opens_setup(self, window: MainWindow) -> None:
        window.state.add_game("new_game")

        assert (window.state.settings.profiles_dir / "new_game.yaml").exists()
        assert window.game_combo.currentText() == "new_game"
        assert window.setup_page.profile.game_id == "new_game"


class TestPlayPage:
    def test_start_and_stop_from_the_button(self, qapp, window: MainWindow) -> None:
        play = window.play_page
        play.source_combo.setCurrentIndex(1)  # UDP: needs no game window

        play.toggle()
        assert _wait_for(qapp, lambda: play.running)
        assert play.start_button.text() == "Stop"
        assert not play.voice_combo.isEnabled()

        play.toggle()
        assert not play.running
        assert play.start_button.text() == "Start"

    def test_a_start_problem_is_shown_instead_of_crashing(self, qapp, window: MainWindow) -> None:
        play = window.play_page
        play.source_combo.setCurrentIndex(0)  # the game window, but none is linked

        play.start()
        assert _wait_for(qapp, lambda: not play._starting)

        assert not play.running
        assert "No game window is linked" in play.state_label.text()

    def test_status_events_update_the_display(self, window: MainWindow) -> None:
        play = window.play_page
        play.on_status(
            StatusEvent(
                StatusKind.LINE,
                "[VOICE] Speaker=MARLE | 8 chars",
                {"speaker": "MARLE", "text": "Come on!", "chars": 8, "cached": False},
            )
        )
        play.on_status(StatusEvent(StatusKind.UNKNOWN_LETTERS, "hint", {"count": 3}))
        play.on_status(StatusEvent(StatusKind.DEBUG, "secret detail"))

        assert play.line_label.text() == "MARLE: Come on!"
        assert not play.unknown_box.isHidden()
        # The log shows the message without the dialogue text, and no details unless asked
        log = play.log.toPlainText()
        assert "Come on!" not in log
        assert "secret detail" not in log


class TestSetupPage:
    def test_dragged_box_is_saved_to_the_profile(self, window: MainWindow) -> None:
        page = window.setup_page
        page._last_frame_size = (640, 480)

        page.on_region_selected(64, 300, 512, 120)
        assert page.save_button.isEnabled()
        page.save()

        saved = GameProfile.from_yaml(window.state.settings.profiles_dir / "chrono_trigger.yaml")
        assert saved.ocr.region == NormRect.from_pixels(64, 300, 512, 120, 640, 480)
        assert not page.save_button.isEnabled()


class TestLettersPage:
    def test_unknown_shapes_become_cards_and_answers_are_saved(self, window: MainWindow) -> None:
        # A linked window (one that matches no running program) so the page is "set up"
        settings = window.state.settings
        profile = GameProfile.from_yaml(settings.profiles_dir / "chrono_trigger.yaml")
        profile.window = WindowBinding(process_name="no-such-emulator.exe")
        profile.save_to_yaml(settings.profiles_dir / "chrono_trigger.yaml")

        page = window.letters_page
        page.show_frames([_draw(["AAB", "A"])])
        assert len(page._cards) == 2

        page._cards[0].edit.setText("A")  # most frequent shape first
        page._cards[1].ignore.setChecked(True)
        page.save_letters()

        font = GlyphFont.load(window.state.settings.profiles_dir / "chrono_trigger.font.json")
        assert sorted(font.glyphs.values()) == ["", "A"]
        assert page._cards == []
        assert "1 letter shape(s) taught" in page.summary_label.text()

    def test_saving_with_nothing_typed_changes_nothing(self, window: MainWindow) -> None:
        page = window.letters_page
        page.show_frames([_draw(["AB", ""])])
        page.save_letters()

        assert len(page._cards) == 2
        assert "at least one" in page.save_label.text()


class TestOptionsPage:
    def _saved(self, window: MainWindow) -> GameProfile:
        return GameProfile.from_yaml(window.state.settings.profiles_dir / "chrono_trigger.yaml")

    def test_shows_the_saved_options_and_saves_changes(self, window: MainWindow) -> None:
        page = window.options_page
        assert page.narrator_check.isChecked()

        page.narrator_check.setChecked(False)
        page.skip_keys_check.setChecked(False)

        saved = self._saved(window)
        assert saved.voices.narrate_unnamed is False
        assert saved.controls.enabled is False
        assert saved.voices.characters  # the rest of the profile is untouched

    def test_saving_setup_keeps_the_options(self, window: MainWindow) -> None:
        window.options_page.narrator_check.setChecked(False)
        setup = window.setup_page
        setup._last_frame_size = (640, 480)
        setup.on_region_selected(64, 300, 512, 120)
        setup.save()

        saved = self._saved(window)
        assert saved.voices.narrate_unnamed is False
        assert saved.ocr.region == NormRect.from_pixels(64, 300, 512, 120, 640, 480)

    def test_a_running_session_picks_up_the_change(self, qapp, window: MainWindow) -> None:
        play = window.play_page
        play.source_combo.setCurrentIndex(1)  # UDP: needs no game window
        play.toggle()
        assert _wait_for(qapp, lambda: play.running)

        window.options_page.narrator_check.setChecked(False)

        assert play.session.profile.voices.narrate_unnamed is False
        play.stop()


class TestQuickStart:
    def test_steps_through_every_page_and_remembers_the_choice(self, window: MainWindow) -> None:
        from retrobard.gui.quick_start import STEPS, QuickStartDialog, should_show

        assert should_show(window.ui_settings)
        dialog = QuickStartDialog(window.ui_settings, window)
        assert not dialog.back_button.isEnabled()
        for _ in range(len(STEPS) - 1):
            dialog.next_button.click()
        assert dialog.step_label.text() == f"Step {len(STEPS)} of {len(STEPS)}"
        assert dialog.next_button.text() == "Done"

        dialog.hide_check.setChecked(True)
        dialog.next_button.click()

        assert dialog.result() == dialog.DialogCode.Accepted
        assert not should_show(window.ui_settings)

    def test_setup_checklist_follows_the_profile(self, window: MainWindow) -> None:
        page = window.setup_page
        assert page.check_labels["window"].text().startswith("○")
        assert page.check_labels["region"].text().startswith("✔")

        page.profile.window = WindowBinding(process_name="emulator.exe")
        page._update_labels()

        assert page.check_labels["window"].text().startswith("✔")
        assert page.check_labels["letters"].text().startswith("○")  # no font taught yet


class TestTesterFeatures:
    def test_api_key_dialog_saves_the_key(self, window: MainWindow) -> None:
        from retrobard.credentials import stored_api_key
        from retrobard.gui.api_key_dialog import ApiKeyDialog

        assert window.play_page.key_label.text() == "No key yet"
        dialog = ApiKeyDialog(window)
        dialog.key_edit.setText("has space")
        dialog.save()
        assert stored_api_key() == ""

        dialog.key_edit.setText("sk_abc")
        dialog.save()
        assert stored_api_key() == "sk_abc"

    def test_voice_button_follows_the_chosen_service(self, window: MainWindow) -> None:
        from retrobard.app.session import TTSBackend

        play = window.play_page
        play.voice_combo.setCurrentIndex(play.voice_combo.findData(TTSBackend.CUSTOM))
        assert play.key_button.text() == "Server..."
        assert play.key_label.text() == "Not set up yet"
        play.voice_combo.setCurrentIndex(play.voice_combo.findData(TTSBackend.ELEVENLABS))
        assert play.key_button.text() == "ElevenLabs key..."
        play.voice_combo.setCurrentIndex(play.voice_combo.findData(TTSBackend.MOCK))
        assert play.key_button.isHidden()

    def test_export_button_writes_the_letters(self, window: MainWindow, tmp_path: Path) -> None:
        page = window.letters_page
        GlyphFont(line_phase=10, glyphs={"3:11/11": "."}).save(
            window.state.settings.profiles_dir / "chrono_trigger.font.json"
        )
        page.refresh_summary()
        assert page.export_button.isEnabled()

        page.export_letters(tmp_path / "out.json")

        assert GlyphFont.load(tmp_path / "out.json").glyphs == {"3:11/11": "."}
        assert "Exported 1" in page.share_label.text()

    def test_title_shows_the_version(self, window: MainWindow) -> None:
        from retrobard import __version__

        assert __version__ in window.windowTitle()


class TestAbout:
    def test_dialog_names_version_author_and_license(self, window: MainWindow) -> None:
        from retrobard import __version__
        from retrobard.about import AUTHOR, LICENSE_NAME
        from retrobard.gui.about_dialog import AboutDialog

        dialog = AboutDialog(window)
        text = dialog.text()
        assert __version__ in text and AUTHOR in text and LICENSE_NAME in text
        dialog.close()

    def test_dialog_links_source_itch_and_website(self, window: MainWindow) -> None:
        from retrobard.about import ITCH_URL, REPO_URL, WEBSITE_URL
        from retrobard.gui.about_dialog import AboutDialog

        dialog = AboutDialog(window)
        links = dialog.links_label.text()
        assert all(url in links for url in (REPO_URL, ITCH_URL, WEBSITE_URL))
        assert not dialog.links_label.isHidden()
        dialog.close()

    def test_main_window_has_about_button_and_credit(self, window: MainWindow) -> None:
        from retrobard import __version__

        assert window.about_button.text() == "About"
        assert window.credit_label.text() == f"RetroBard {__version__} · by Aze"
        assert window.credit_label in window.statusBar().findChildren(QLabel)


class TestWindowStatus:
    def test_shows_the_title_being_read(self, window: MainWindow) -> None:
        play = window.play_page
        title = "Chrono Trigger (USA) - Snes9x 1.62.3 - a very long window title indeed"
        play.on_status(StatusEvent(StatusKind.WINDOW, "found", {"available": True, "title": title}))
        assert play.window_label.text().startswith("Reading: Chrono Trigger")
        assert len(play.window_label.text()) <= len("Reading: ") + 60
        assert title not in play.log.toPlainText()

    def test_ambiguity_stays_shown_until_resolved(self, window: MainWindow) -> None:
        play = window.play_page
        play.on_status(
            StatusEvent(
                StatusKind.WINDOW, "2 windows", {"available": False, "reason": "ambiguous", "count": 2}
            )
        )
        play.on_status(StatusEvent(StatusKind.WINDOW, "paused", {"available": False}))
        assert play.window_label.text() == "Several matching windows: open Setup"
        play.on_status(StatusEvent(StatusKind.WINDOW, "found", {"available": True}))
        assert play.window_label.text() == "Found"


class TestVoiceCache:
    def test_clear_button_empties_the_cache(self, window: MainWindow, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox

        from retrobard.adapters.cache.sqlite_disk import SqliteDiskCache

        options = window.options_page
        cache = SqliteDiskCache(window.state.settings.cache_dir)
        cache.put("chrono_trigger", "CRONO", "Hi", "v1", b"x" * 2000)
        options.refresh_cache()
        assert "1 lines" in options.cache_label.text()

        monkeypatch.setattr(
            QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes
        )
        options.clear_cache_button.click()

        assert cache.count() == 0
        assert "0 lines, 0.0 MB" in options.cache_label.text()



class TestUpdates:
    """The "Check for updates" flow, with GitHub replaced by a fake answer."""

    @staticmethod
    def _updater(window: MainWindow, tmp_path: Path, tag: str, frozen: bool):
        import httpx

        from retrobard.app import updates
        from retrobard.gui.updater import Updater

        release = {
            "tag_name": tag,
            "body": "New voices for Frog.",
            "html_url": "https://github.com/x/releases/tag/" + tag,
            "assets": [{
                "name": f"RetroBard-{tag[1:]}-windows.zip",
                "browser_download_url": "https://github.com/x/RetroBard.zip",
                "size": 10,
                "digest": "sha256:" + "0" * 64,
            }],
        }
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, json=release)

        app = tmp_path / "RetroBard"
        app.mkdir(exist_ok=True)
        (app / "RetroBard.exe").write_bytes(b"exe")
        updater = Updater(
            window,
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
            frozen=lambda: frozen,
            folder=lambda: app,
        )
        boxes = []
        updater._show = boxes.append  # read the message boxes instead of showing them
        opened = []
        updater._open = opened.append
        return updater, boxes, requests, updates

    def test_nothing_is_checked_until_asked(self, window: MainWindow, tmp_path: Path) -> None:
        _, boxes, requests, _ = self._updater(window, tmp_path, "v99.0.0", frozen=True)
        assert requests == [] and boxes == []

    def test_says_when_up_to_date(self, qapp, window: MainWindow, tmp_path: Path) -> None:
        from retrobard import __version__

        updater, boxes, requests, updates = self._updater(
            window, tmp_path, f"v{__version__}", frozen=True
        )
        updater.check()
        assert _wait_for(qapp, lambda: boxes)
        assert requests == [updates.LATEST_RELEASE_API]
        assert "latest version" in boxes[0].text()
        assert not updater.busy

    def test_offers_a_newer_version(self, qapp, window: MainWindow, tmp_path: Path) -> None:
        updater, boxes, _, _ = self._updater(window, tmp_path, "v99.0.0", frozen=True)
        updater.check()
        assert _wait_for(qapp, lambda: boxes)
        box = boxes[0]
        assert "99.0.0 is available" in box.text()
        assert "New voices for Frog." in box.informativeText()
        labels = [button.text() for button in box.buttons()]
        assert "Download and install" in labels and "Open download page" in labels
        assert not updater.busy  # closed without a choice: "Later"

    def test_from_source_only_links_the_page(
        self, qapp, window: MainWindow, tmp_path: Path
    ) -> None:
        updater, boxes, _, _ = self._updater(window, tmp_path, "v99.0.0", frozen=False)
        updater.check()
        assert _wait_for(qapp, lambda: boxes)
        box = boxes[0]
        assert "git pull" in box.informativeText()
        assert "Download and install" not in [button.text() for button in box.buttons()]

    def test_about_dialog_has_the_button(self, window: MainWindow) -> None:
        from retrobard.gui.about_dialog import AboutDialog

        calls = []
        dialog = AboutDialog(window, check_updates=lambda: calls.append(1))
        assert not dialog.update_button.isHidden()
        dialog.update_button.click()
        assert calls == [1]
        dialog.close()
        assert AboutDialog(window).update_button.isHidden()


class TestWhatsNew:
    @staticmethod
    def _settings(tmp_path: Path) -> QSettings:
        return QSettings(str(tmp_path / "seen.ini"), QSettings.Format.IniFormat)

    def test_first_install_says_nothing(self, tmp_path: Path) -> None:
        from retrobard.gui.whats_new import updated_since_last_run

        settings = self._settings(tmp_path)
        assert not updated_since_last_run(settings, "0.3.1")
        assert not updated_since_last_run(settings, "0.3.1")

    def test_announces_an_update_once(self, tmp_path: Path) -> None:
        from retrobard.gui.whats_new import updated_since_last_run

        settings = self._settings(tmp_path)
        updated_since_last_run(settings, "0.3.1")
        assert updated_since_last_run(settings, "0.3.2")
        assert not updated_since_last_run(settings, "0.3.2")
        # Going back to an older version is not an update
        assert not updated_since_last_run(settings, "0.3.1")

    def test_users_of_versions_before_0_3_1_are_told(self, tmp_path: Path) -> None:
        from retrobard.gui.quick_start import HIDE_KEY
        from retrobard.gui.whats_new import updated_since_last_run

        settings = self._settings(tmp_path)
        settings.setValue(HIDE_KEY, False)
        assert updated_since_last_run(settings, "0.3.1")

    def test_dialog_shows_the_shipped_changelog(self, window: MainWindow) -> None:
        from retrobard import __version__
        from retrobard.gui.whats_new import WhatsNewDialog

        text = WhatsNewDialog(window).text()
        assert f"{__version__}" in text and "this version" in text
        assert "Unreleased" not in text

    def test_dialog_without_the_file(self, window: MainWindow, tmp_path: Path) -> None:
        from retrobard.gui.whats_new import WhatsNewDialog

        text = WhatsNewDialog(window, path=tmp_path / "missing.md").text()
        assert "was not found" in text

    def test_about_dialog_has_the_button(self, window: MainWindow) -> None:
        from retrobard.gui.about_dialog import AboutDialog

        calls = []
        dialog = AboutDialog(window, show_whats_new=lambda: calls.append(1))
        assert not dialog.whats_new_button.isHidden()
        dialog.whats_new_button.click()
        assert calls == [1]
        dialog.close()
        assert AboutDialog(window).whats_new_button.isHidden()

    def test_a_fresh_window_announces_nothing(self, window: MainWindow) -> None:
        assert not window.was_updated
        window.announce_update()
        assert not window._announced
