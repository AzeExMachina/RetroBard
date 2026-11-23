# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
from retrobard.adapters.producers.skip_input import SkipInputWatcher
from retrobard.adapters.producers.win32_input import key_to_vk
from retrobard.config import InputConfig
from retrobard.domain.models import DialogueEvent, EventType


class FakeInput:
    def __init__(self) -> None:
        self.focused = True
        self.keys: set[str] = set()
        self.pad: set[int] = set()
        self.key_reads = 0

    def is_focused(self) -> bool:
        return self.focused

    def read_keys(self) -> set[str]:
        self.key_reads += 1
        return set(self.keys)

    def read_pad(self) -> set[int]:
        return set(self.pad)


def _make(fake: FakeInput, cooldown_ms: int = 50) -> tuple[SkipInputWatcher, list[DialogueEvent]]:
    events: list[DialogueEvent] = []
    watcher = SkipInputWatcher(
        InputConfig(cooldown_ms=cooldown_ms),
        is_game_focused=fake.is_focused,
        read_keys=fake.read_keys,
        read_pad_buttons=fake.read_pad,
    )
    watcher._emit = events.append
    return watcher, events


def test_input_is_not_even_read_while_game_is_unfocused() -> None:
    fake = FakeInput()
    fake.focused = False
    fake.keys = {"z"}
    watcher, events = _make(fake)

    watcher.poll_once()

    assert events == []
    assert fake.key_reads == 0


def test_press_emits_one_stop_and_holding_does_not_repeat() -> None:
    fake = FakeInput()
    watcher, events = _make(fake)

    fake.keys = {"z"}
    watcher.poll_once()
    watcher.poll_once()
    watcher.poll_once()

    assert len(events) == 1
    assert events[0].event_type == EventType.STOP
    assert events[0].source == "input:key_z"


def test_gamepad_button_emits_stop() -> None:
    fake = FakeInput()
    watcher, events = _make(fake)

    fake.pad = {0}
    watcher.poll_once()

    assert [e.source for e in events] == ["input:pad_0"]


def test_cooldown_suppresses_rapid_retrigger() -> None:
    fake = FakeInput()
    watcher, events = _make(fake, cooldown_ms=60_000)

    fake.keys = {"z"}
    watcher.poll_once()
    fake.keys = set()
    watcher.poll_once()
    fake.keys = {"x"}
    watcher.poll_once()

    assert len(events) == 1


def test_key_names_map_to_virtual_keys() -> None:
    assert key_to_vk("z") == 0x5A
    assert key_to_vk("Space") == 0x20
    assert key_to_vk("enter") == key_to_vk("return") == 0x0D
    assert key_to_vk("not-a-key") is None
