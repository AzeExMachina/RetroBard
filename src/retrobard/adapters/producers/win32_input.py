# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Polls the state of specific keys / XInput buttons on Windows (no hooks)."""

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

from retrobard.domain.status import StatusKind, StatusSink, console_status, report

_NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "tab": 0x09,
    "backspace": 0x08,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}

# SDL-style button index -> XInput wButtons bit
_XINPUT_BUTTONS = {
    0: 0x1000,  # A
    1: 0x2000,  # B
    2: 0x4000,  # X
    3: 0x8000,  # Y
    4: 0x0020,  # Back
    6: 0x0010,  # Start
    7: 0x0040,  # Left stick
    8: 0x0080,  # Right stick
    9: 0x0100,  # Left shoulder
    10: 0x0200,  # Right shoulder
}

_RESCAN_EVERY_POLLS = 120


def key_to_vk(name: str) -> int | None:
    key = name.strip().lower()
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if len(key) == 1 and (key.isascii() and key.isalnum()):
        return ord(key.upper())
    return None


def make_key_reader(
    key_names: list[str], status: StatusSink = console_status
) -> Callable[[], set[str]]:
    """Returns a function reporting which of the given keys are currently down."""
    if sys.platform != "win32":
        raise RuntimeError("Skip-key polling is currently only supported on Windows.")

    user32 = ctypes.WinDLL("user32")
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short

    watched: dict[str, int] = {}
    for name in key_names:
        vk = key_to_vk(name)
        if vk is None:
            report(status, StatusKind.WARNING, f"[INPUT] Ignoring unknown skip key {name!r}")
        else:
            watched[name.strip().lower()] = vk

    def read() -> set[str]:
        return {name for name, vk in watched.items() if user32.GetAsyncKeyState(vk) & 0x8000}

    return read


class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _XINPUT_GAMEPAD)]


def make_pad_reader(button_indices: list[int]) -> Callable[[], set[int]] | None:
    """Returns a function reporting which of the given XInput buttons are down."""
    masks = {i: _XINPUT_BUTTONS[i] for i in button_indices if i in _XINPUT_BUTTONS}
    if not masks or sys.platform != "win32":
        return None

    xinput = None
    for dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            xinput = ctypes.WinDLL(dll)
            break
        except OSError:
            continue
    if xinput is None:
        return None

    xinput.XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(_XINPUT_STATE)]
    xinput.XInputGetState.restype = wintypes.DWORD

    connected: set[int] = set()
    polls_until_rescan = 0

    def read() -> set[int]:
        nonlocal polls_until_rescan
        # Probing empty controller slots is slow, so only rescan them occasionally
        if polls_until_rescan <= 0:
            slots = range(4)
            polls_until_rescan = _RESCAN_EVERY_POLLS
        else:
            slots = tuple(connected)
            polls_until_rescan -= 1

        down: set[int] = set()
        state = _XINPUT_STATE()
        for slot in slots:
            if xinput.XInputGetState(slot, ctypes.byref(state)) != 0:
                connected.discard(slot)
                continue
            connected.add(slot)
            buttons = state.Gamepad.wButtons
            down |= {index for index, mask in masks.items() if buttons & mask}
        return down

    return read
