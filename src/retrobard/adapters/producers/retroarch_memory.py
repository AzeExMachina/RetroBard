# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads dialogue straight from the game's memory through RetroArch.

RetroArch answers network commands on UDP port 55355 when `network_cmd_enable =
true` is set in retroarch.cfg. RetroBard asks

    READ_CORE_MEMORY 7e2000 64

and RetroArch replies with the address followed by the bytes in hex, or with -1
and an error (no game loaded, the core has no memory map...). Cores without a
memory map can still be read with READ_CORE_RAM, which takes offsets into the
console's work RAM instead of addresses. Example replies:

    READ_CORE_MEMORY 7e2000 41 42 00 ...
    READ_CORE_MEMORY 7e2000 -1 no memory map defined

Nothing is written to the emulator; RetroBard only reads.
"""

import hashlib
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from retrobard.adapters.producers.memory_text import TextDecoder
from retrobard.adapters.producers.udp_listener import is_loopback
from retrobard.config import MemoryConfig, MemoryValue, RomFile
from retrobard.domain.models import DialogueEvent, EventType
from retrobard.domain.status import StatusKind, StatusSink, console_status, report

COMMANDS = {"read_core_memory": "READ_CORE_MEMORY", "read_core_ram": "READ_CORE_RAM"}
RETRY_AFTER_ERROR_S = 2.0
# Cursor mode: the most a printing cursor moves forward between two polls (more
# is a jump to other text), and how far past a message's start it may first be
# seen (it has usually printed a letter or two by the first poll)
CURSOR_STEP_MAX = 64
ENTRY_SLACK = 8


class RetroArchError(Exception):
    """RetroArch did not answer, or answered with an error."""


def parse_reply(reply: bytes, address: int, command: str = "READ_CORE_MEMORY") -> bytes | None:
    """The bytes in a READ_CORE_MEMORY reply, or None if it is for another address
    (a late answer to an earlier request). Raises RetroArchError for an error reply."""
    parts = reply.decode("ascii", errors="replace").split()
    if len(parts) < 2 or parts[0] != command:
        raise RetroArchError("unexpected reply from RetroArch")
    try:
        if int(parts[1], 16) != address:
            return None
    except ValueError as exc:
        raise RetroArchError("unexpected reply from RetroArch") from exc
    if len(parts) >= 3 and parts[2] == "-1":
        raise RetroArchError(" ".join(parts[3:]) or "RetroArch could not read the memory")
    try:
        return bytes(int(p, 16) for p in parts[2:])
    except ValueError as exc:
        raise RetroArchError("unexpected reply from RetroArch") from exc


class RetroArchMemoryClient:
    """Reads core memory over RetroArch's UDP command interface."""

    def __init__(
        self,
        host: str,
        port: int,
        chunk_bytes: int = 128,
        timeout_s: float = 0.25,
        command: str = "read_core_memory",
    ):
        if not is_loopback(host):
            raise ValueError(
                f"RetroArch host {host!r} is not a loopback address. RetroBard only "
                f"talks to an emulator on this computer (use 127.0.0.1)."
            )
        self._chunk = chunk_bytes
        self._command = COMMANDS[command]
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # connect() makes the socket drop datagrams from anywhere but RetroArch's port
        self._sock.connect((host, port))
        self._sock.settimeout(timeout_s)

    def read(self, address: int, length: int) -> bytes:
        data = bytearray()
        while len(data) < length:
            chunk_at = address + len(data)
            chunk = self._read_chunk(chunk_at, min(self._chunk, length - len(data)))
            if not chunk:
                break
            data += chunk
        return bytes(data)

    def read_value(self, value: MemoryValue) -> int:
        raw = self.read(value.address, value.size)
        if len(raw) < value.size:
            raise RetroArchError(f"short read at {value.address:#x}")
        return int.from_bytes(raw, "little")

    def _read_chunk(self, address: int, length: int) -> bytes:
        try:
            self._sock.send(f"{self._command} {address:x} {length}\n".encode("ascii"))
            deadline = time.monotonic() + (self._sock.gettimeout() or 0.25)
            while True:
                reply = self._sock.recv(65535)
                data = parse_reply(reply, address, self._command)
                if data is not None:
                    return data
                if time.monotonic() > deadline:
                    raise TimeoutError
        except TimeoutError as exc:
            raise RetroArchError("RetroArch did not answer") from exc
        except ConnectionResetError as exc:
            # Windows: nothing is listening on the port
            raise RetroArchError("RetroArch is not listening") from exc

    def close(self) -> None:
        self._sock.close()


def rom_mismatch(data: bytes, rom: RomFile, name: str) -> str | None:
    """Why `data` is not the ROM the profile was made for, or None if it is."""
    digest = hashlib.sha1(data).hexdigest()
    if digest == rom.sha1.lower():
        return None
    return (
        f"{name} is not the ROM this profile was made for "
        f"(SHA-1 {digest}, expected {rom.sha1.lower()}). Choose the exact version "
        f"the profile names, e.g. a clean USA dump."
    )


class RomImage:
    """The game's ROM file, read with console addresses (for text printed from ROM)."""

    def __init__(self, path: Path, rom: RomFile):
        data = path.read_bytes()
        problem = rom_mismatch(data, rom, path.name)
        if problem:
            raise ValueError(problem)
        # A 512-byte copier header is not part of the game
        self._data = data[512:] if len(data) % 0x8000 == 512 else data
        self._mapping = rom.mapping

    def offset(self, address: int) -> int:
        bank, low = (address >> 16) & 0xFF, address & 0xFFFF
        if self._mapping == "hirom":
            return ((bank & 0x3F) << 16) | low
        if self._mapping == "lorom":
            return ((bank & 0x7F) << 15) | (low & 0x7FFF)
        return address

    def read(self, address: int, length: int) -> bytes:
        start = self.offset(address)
        if start < 0:
            length, start = length + start, 0
        return self._data[start : start + max(0, length)]

    def close(self) -> None:
        pass


class RetroArchMemoryWatcher:
    """EventProducer that polls the dialogue text in memory and speaks each new message."""

    def __init__(
        self,
        config: MemoryConfig,
        client: RetroArchMemoryClient | None = None,
        profile_dir: Path | None = None,
        rom_path: Path | None = None,
        debug: bool = False,
        status: StatusSink = console_status,
    ):
        if config.text.rom is not None and rom_path is None:
            raise ValueError("this profile reads its text from the game's ROM; choose the ROM file")
        self._config = config
        self._client = client or RetroArchMemoryClient(
            config.host, config.port, chunk_bytes=config.chunk_bytes, command=config.command
        )
        self._decoder = TextDecoder(config.text, profile_dir)
        # Where the text bytes themselves are read from
        self._text_source = (
            RomImage(rom_path, config.text.rom) if config.text.rom else self._client
        )
        self._debug = debug
        self._status = status
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._pending: str = ""
        self._pending_since = 0.0
        self._spoken: str | None = None
        self._connected: bool | None = None  # None until the first poll
        # Cursor mode: where the cursor is, since when, and where a message was last read
        self._cursor: int | None = None
        self._previous_cursor: int | None = None
        self._cursor_entry: int | None = None  # where the cursor last jumped to
        self._cursor_since = 0.0
        self._cursor_read: int | None = None

    def start(self, emit: Callable[[DialogueEvent], None]) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(emit,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._client.close()

    def _run(self, emit: Callable[[DialogueEvent], None]) -> None:
        interval = self._config.poll_ms / 1000.0
        while not self._stop_event.is_set():
            wait = interval
            try:
                for event in self.poll_once(time.monotonic()):
                    emit(event)
                self._set_connected(True, "")
            except RetroArchError as exc:
                self._set_connected(False, str(exc))
                wait = RETRY_AFTER_ERROR_S
            except Exception as exc:  # noqa: BLE001 - keep polling whatever happens downstream
                report(self._status, StatusKind.WARNING, f"[Memory] {type(exc).__name__}: {exc}")
            self._stop_event.wait(wait)

    def _set_connected(self, connected: bool, reason: str) -> None:
        if connected == self._connected:
            return
        self._connected = connected
        if connected:
            report(self._status, StatusKind.INFO, "[Memory] Reading dialogue from RetroArch.")
        else:
            report(
                self._status,
                StatusKind.WARNING,
                f"[Memory] Cannot read RetroArch's memory ({reason}). Is a game loaded and "
                f"network_cmd_enable = true set in retroarch.cfg? Retrying...",
            )

    def poll_once(self, now: float) -> list[DialogueEvent]:
        """Reads memory once and returns the events it causes (for the loop and tests)."""
        config, client = self._config, self._client

        if config.box_open is not None and not config.box_open.is_open(
            client.read_value(config.box_open)
        ):
            return self._box_closed()

        if config.text.cursor is not None:
            return self._poll_cursor(now)

        text = self._decoder.decode(
            self._text_source.read(self._text_address(), config.text.max_bytes)
        )
        if not text:
            # Without a box flag, an empty buffer is the only sign the box closed
            return self._box_closed() if config.box_open is None else []

        if text != self._pending:
            self._pending, self._pending_since = text, now
        if text == self._spoken or (now - self._pending_since) * 1000 < config.settle_ms:
            return []

        self._spoken = text
        return [self._speak(text)]

    def _poll_cursor(self, now: float) -> list[DialogueEvent]:
        """The game prints straight from the script: speak a message once the
        cursor walking through it rests on (or just past) the code that ends the
        box (e.g. "wait for a button"), reading the text that comes before it."""
        text_cfg = self._config.text
        cursor = text_cfg.cursor.target(self._client.read_value(text_cfg.cursor))
        if cursor != self._cursor:
            if self._cursor is None or not 0 < cursor - self._cursor <= CURSOR_STEP_MAX:
                self._cursor_entry = cursor  # jumped: a new piece of text starts near here
            self._previous_cursor = self._cursor
            self._cursor, self._cursor_since = cursor, now
            return []
        if cursor == self._cursor_read or (now - self._cursor_since) * 1000 < self._config.settle_ms:
            return []

        # The window ends with the code the cursor rests on or just passed
        boundary = cursor if text_cfg.cursor_rests == "on" else cursor - 1
        first = max(0, boundary - text_cfg.max_bytes)
        window = self._text_source.read(first, boundary - first + 1)
        if not window or not self._decoder.is_break(window[-1], text_cfg.command_below):
            return []  # paused in the middle of the text (e.g. between lines)
        self._cursor_read = cursor
        end = len(window) - 1
        start = self._decoder.text_start(window, end, text_cfg.command_below)
        if text_cfg.require_progress:
            # The cursor must have been seen inside this text on its way here;
            # a jump straight to the end means the box was skipped, never shown
            previous = self._previous_cursor
            if previous is None or not first + start < previous < cursor:
                return []
            # ...and must have entered it near its start. Entering far past it
            # means the text found reaches back into data that was never printed
            # (e.g. a menu string right after a table, with no end code between)
            entry = self._cursor_entry
            if entry is None or entry > first + start + ENTRY_SLACK:
                return []
        text = self._decoder.decode(window[start:end])
        return [self._speak(text)] if text else []

    def _speak(self, text: str) -> DialogueEvent:
        config = self._config
        speaker = None
        if config.speaker is not None:
            speaker = config.speaker.names.get(self._client.read_value(config.speaker))
        if self._debug:
            report(self._status, StatusKind.INFO, f"[Memory] {speaker or '-'}: {text!r}")
        return DialogueEvent(
            event_type=EventType.SPEAK,
            raw_text=text,
            explicit_speaker=speaker,
            source="retroarch_memory",
        )

    def _text_address(self) -> int:
        text = self._config.text
        if text.pointer is None:
            return text.address
        return text.pointer.target(self._client.read_value(text.pointer))

    def _box_closed(self) -> list[DialogueEvent]:
        self._pending = ""
        if self._spoken is None:
            return []
        self._spoken = None
        return [DialogueEvent(event_type=EventType.CLEAR_CONTEXT, source="retroarch_memory")]
