# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Captures the pixels of ONE bound window on Windows, and nothing else.

Frames come from Windows Graphics Capture bound to the window's handle, i.e. from
the window's own surface rather than the desktop. Other applications are never
captured, even when they overlap the game or the game is in the background, and
it works with GPU-rendered emulators (where GDI/PrintWindow only returns black).
"""

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from retrobard.config import WindowBinding
from retrobard.domain.status import StatusKind, StatusSink, console_status, report

GA_ROOT = 2
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
REBIND_INTERVAL_S = 1.0
FIRST_FRAME_TIMEOUT_S = 1.5
# Upper bound on how often a new frame is copied out of the capture session
CAPTURE_INTERVAL_MS = 33

_WNDENUMPROC = (
    ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    if sys.platform == "win32"
    else None
)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process_name: str
    # Dialogs (owned by another window) and tool windows are never the game
    owned: bool = False
    tool: bool = False

    @property
    def eligible(self) -> bool:
        return not (self.owned or self.tool)


@dataclass(frozen=True)
class AmbiguousMatch:
    """Several windows match the binding, so none of them is read."""

    count: int


@lru_cache(maxsize=1)
def _api():
    if sys.platform != "win32":
        raise RuntimeError("Window-bound capture is currently only supported on Windows.")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Report real pixels on scaled (HiDPI) displays; fails harmlessly if already set
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass

    def sig(fn, argtypes, restype):
        fn.argtypes = argtypes
        fn.restype = restype

    H, B, I, U = wintypes.HWND, wintypes.BOOL, ctypes.c_int, wintypes.UINT
    rect_p = ctypes.POINTER(wintypes.RECT)
    sig(user32.EnumWindows, [_WNDENUMPROC, wintypes.LPARAM], B)
    sig(user32.IsWindow, [H], B)
    sig(user32.IsWindowVisible, [H], B)
    sig(user32.IsIconic, [H], B)
    sig(user32.GetWindowTextLengthW, [H], I)
    sig(user32.GetWindowTextW, [H, wintypes.LPWSTR, I], I)
    sig(user32.GetWindowThreadProcessId, [H, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD)
    sig(user32.GetClientRect, [H, rect_p], B)
    sig(user32.GetWindowRect, [H, rect_p], B)
    sig(user32.ClientToScreen, [H, ctypes.POINTER(wintypes.POINT)], B)
    sig(user32.GetForegroundWindow, [], H)
    sig(user32.GetAncestor, [H, U], H)
    sig(user32.GetWindow, [H, U], H)
    # GetWindowLongPtrW only exists in 64-bit user32; 32-bit Python uses GetWindowLongW
    get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    sig(get_long, [H, I], ctypes.c_ssize_t)
    sig(
        dwmapi.DwmGetWindowAttribute,
        [H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD],
        ctypes.c_long,
    )
    sig(kernel32.OpenProcess, [wintypes.DWORD, B, wintypes.DWORD], wintypes.HANDLE)
    sig(
        kernel32.QueryFullProcessImageNameW,
        [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)],
        B,
    )
    sig(kernel32.CloseHandle, [wintypes.HANDLE], B)
    return user32, dwmapi, kernel32


def _process_name(pid: int) -> str:
    _, _, kernel32 = _api()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)


def _ex_style(user32, hwnd) -> int:
    get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    return int(get_long(hwnd, GWL_EXSTYLE))


def list_windows(
    include_own_process: bool = False, include_dialogs: bool = False
) -> list[WindowInfo]:
    """Lists visible, titled top-level windows with their owning executable name.

    Dialogs (windows owned by another window) and tool windows are left out unless
    include_dialogs is set: they are never the game."""
    user32, _, _ = _api()
    own_pid = os.getpid()
    found: list[WindowInfo] = []

    def on_window(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid and not include_own_process:
            return True
        owned = bool(user32.GetWindow(hwnd, GW_OWNER))
        tool = bool(_ex_style(user32, hwnd) & WS_EX_TOOLWINDOW)
        if (owned or tool) and not include_dialogs:
            return True
        name = _process_name(pid.value)
        if name:
            found.append(
                WindowInfo(
                    hwnd=int(hwnd), title=buf.value, process_name=name, owned=owned, tool=tool
                )
            )
        return True

    user32.EnumWindows(_WNDENUMPROC(on_window), 0)
    return found


def pick_window(
    candidates: list[WindowInfo], binding: WindowBinding
) -> WindowInfo | AmbiguousMatch | None:
    """The one window matching the binding. Never guesses: when several match, the
    result is AmbiguousMatch and nothing should be read. Dialogs never match."""
    matches = [
        info
        for info in candidates
        if info.eligible and binding.matches(info.process_name, info.title)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return AmbiguousMatch(count=len(matches))
    return None


def find_window(
    binding: WindowBinding, include_own_process: bool = False
) -> WindowInfo | AmbiguousMatch | None:
    return pick_window(list_windows(include_own_process=include_own_process), binding)


def window_title(hwnd: int) -> str | None:
    user32, _, _ = _api()
    if not user32.IsWindow(hwnd):
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(max(length, 0) + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def client_area_box(hwnd: int, frame_w: int, frame_h: int) -> tuple[int, int, int, int] | None:
    """Locates the window's client area inside a captured whole-window frame.

    Returns (x0, y0, x1, y1) in frame pixels, which excludes the title bar, menu
    bar and borders, or None while the frame does not match the window's current
    size (e.g. mid-resize).
    """
    user32, dwmapi, _ = _api()

    client = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        return None
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None

    # The captured frame covers either the visible frame bounds or the full window rect
    candidates = []
    visible = wintypes.RECT()
    if (
        dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(visible), ctypes.sizeof(visible)
        )
        == 0
    ):
        candidates.append(visible)
    full = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(full)):
        candidates.append(full)

    for bounds in candidates:
        if (bounds.right - bounds.left, bounds.bottom - bounds.top) == (frame_w, frame_h):
            x0, y0 = origin.x - bounds.left, origin.y - bounds.top
            x1, y1 = x0 + client.right, y0 + client.bottom
            if 0 <= x0 < x1 <= frame_w and 0 <= y0 < y1 <= frame_h:
                return x0, y0, x1, y1
    return None


class Win32WindowSource:
    """FrameSource bound to a single game window; re-binds if the game restarts."""

    def __init__(
        self,
        binding: WindowBinding,
        include_own_process: bool = False,
        status: StatusSink = console_status,
    ):
        self.binding = binding
        self.status = status
        self._include_own_process = include_own_process
        self._hwnd: int | None = None
        self._last_bind_attempt = 0.0

        self._lock = threading.Lock()
        self._first_frame = threading.Event()
        self._latest: np.ndarray | None = None
        self._session_hwnd: int | None = None
        self._session_control = None
        self._session_id = 0
        self._warned_start_failure = False
        self._ambiguous = False  # inside an episode of several matching windows
        _api()  # fail early on unsupported platforms

    def _ensure_bound(self) -> int | None:
        user32, _, _ = _api()
        if self._hwnd is not None and user32.IsWindow(self._hwnd):
            return self._hwnd

        self._hwnd = None
        now = time.monotonic()
        if (now - self._last_bind_attempt) < REBIND_INTERVAL_S and self._last_bind_attempt:
            return None
        self._last_bind_attempt = now

        info = find_window(self.binding, include_own_process=self._include_own_process)
        if isinstance(info, AmbiguousMatch):
            if not self._ambiguous:
                self._ambiguous = True
                report(
                    self.status,
                    StatusKind.WINDOW,
                    f"[CAPTURE] {info.count} windows of {self.binding.process_name} are open, "
                    f"so none is read. Close the extra ones, or add a title filter on the "
                    f"Setup tab.",
                    available=False,
                    reason="ambiguous",
                    count=info.count,
                )
            return None
        if self._ambiguous:
            self._ambiguous = False
            if info is None:
                report(
                    self.status,
                    StatusKind.WINDOW,
                    "[CAPTURE] The game window is not open.",
                    available=False,
                    reason="not_found",
                )
        if info:
            self._hwnd = info.hwnd
        return self._hwnd

    def current_title(self) -> str | None:
        """The live title of the bound window, or None while none is bound."""
        if self._hwnd is None:
            return None
        return window_title(self._hwnd)

    def describe(self) -> str:
        suffix = (
            f", title contains {self.binding.title_contains!r}"
            if self.binding.title_contains
            else ""
        )
        return f"{self.binding.process_name}{suffix}"

    def is_available(self) -> bool:
        return self._ensure_bound() is not None

    def _start_session(self, hwnd: int) -> None:
        from windows_capture import WindowsCapture

        self._stop_session()
        with self._lock:
            self._session_id += 1
            session_id = self._session_id
            self._session_hwnd = hwnd
        self._first_frame.clear()

        try:
            capture = WindowsCapture(
                cursor_capture=False,
                draw_border=False,
                minimum_update_interval=CAPTURE_INTERVAL_MS,
                window_hwnd=hwnd,
            )
        except Exception as exc:  # noqa: BLE001 - the native layer raises plain exceptions
            if not self._warned_start_failure:
                report(
                    self.status,
                    StatusKind.ERROR,
                    f"[CAPTURE] Could not start capturing the game window: {exc}",
                )
                self._warned_start_failure = True
            return

        @capture.event
        def on_frame_arrived(frame, _control) -> None:
            buffer = frame.frame_buffer
            box = client_area_box(hwnd, buffer.shape[1], buffer.shape[0])
            if box is None:
                return
            x0, y0, x1, y1 = box
            # The buffer is only valid inside this callback, so keep a copy
            client_frame = buffer[y0:y1, x0:x1].copy()
            with self._lock:
                if session_id == self._session_id:
                    self._latest = client_frame
            self._first_frame.set()

        @capture.event
        def on_closed() -> None:
            with self._lock:
                if session_id == self._session_id:
                    self._latest = None
                    self._session_hwnd = None

        self._session_control = capture.start_free_threaded()
        self._warned_start_failure = False

    def _stop_session(self) -> None:
        control, self._session_control = self._session_control, None
        with self._lock:
            self._session_id += 1  # frames from the old session are ignored from now on
            self._session_hwnd = None
            self._latest = None
        if control is not None:
            try:
                control.stop()
            except Exception:  # noqa: BLE001, S110 - already stopping; nothing left to do
                pass

    def close(self) -> None:
        """Stops capturing. Safe to call more than once."""
        self._stop_session()

    def grab(self) -> np.ndarray | None:
        """Latest frame of the game's client area (BGRA), or None if unavailable."""
        user32, _, _ = _api()
        hwnd = self._ensure_bound()
        if hwnd is None:
            if self._session_control is not None:
                self._stop_session()
            return None
        if user32.IsIconic(hwnd):
            return None

        if self._session_hwnd != hwnd:
            self._start_session(hwnd)
            self._first_frame.wait(FIRST_FRAME_TIMEOUT_S)

        with self._lock:
            return self._latest

    def is_foreground(self) -> bool:
        """True only while the bound game window is the one receiving input."""
        hwnd = self._ensure_bound()
        if hwnd is None:
            return False
        user32, _, _ = _api()
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False
        root = user32.GetAncestor(foreground, GA_ROOT) or foreground
        return int(root) == hwnd
