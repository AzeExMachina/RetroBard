# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import threading
import time
from collections.abc import Callable

from retrobard.config import InputConfig
from retrobard.domain.models import DialogueEvent, EventType
from retrobard.domain.status import StatusKind, StatusSink, console_status, report


class SkipInputWatcher:
    """Emits STOP when a skip key / gamepad button is pressed in the game.

    There is no global keyboard hook: while the game window is not focused the
    input state is not even read, and while it is focused only the configured
    skip keys and buttons are polled.
    """

    def __init__(
        self,
        config: InputConfig,
        is_game_focused: Callable[[], bool],
        read_keys: Callable[[], set[str]],
        read_pad_buttons: Callable[[], set[int]] | None = None,
        poll_interval_s: float = 0.016,
        debug: bool = False,
        status: StatusSink = console_status,
    ):
        self.config = config
        self.debug = debug
        self.status = status
        self._is_game_focused = is_game_focused
        self._read_keys = read_keys
        self._read_pad_buttons = read_pad_buttons
        self._poll_interval_s = poll_interval_s
        self._cooldown_s = config.cooldown_ms / 1000.0

        self._emit: Callable[[DialogueEvent], None] | None = None
        self._held: set[str] = set()
        self._last_trigger_time = float("-inf")
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, emit: Callable[[DialogueEvent], None]) -> None:
        if not self.config.enabled:
            return
        self._emit = emit
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self._poll_interval_s)

    def poll_once(self) -> None:
        if not self._is_game_focused():
            self._held.clear()
            return

        pressed = {f"key_{name}" for name in self._read_keys()}
        if self._read_pad_buttons:
            pressed |= {f"pad_{button}" for button in self._read_pad_buttons()}

        newly_pressed = pressed - self._held
        self._held = pressed
        if newly_pressed:
            self._trigger_stop(min(newly_pressed))

    def _trigger_stop(self, source_label: str) -> None:
        now = time.monotonic()
        if (now - self._last_trigger_time) < self._cooldown_s:
            return
        self._last_trigger_time = now

        if self.debug:
            report(self.status, StatusKind.DEBUG, f"[INPUT] Skip triggered via {source_label}")

        if self._emit:
            self._emit(
                DialogueEvent(event_type=EventType.STOP, source=f"input:{source_label}")
            )
