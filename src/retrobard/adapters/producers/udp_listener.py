# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Receives dialogue events from a local helper (e.g. an emulator script) over UDP.

Each packet is one JSON object:

    {"token": "<shared secret>", "event": "speak", "text": "MARLE: Come on!", "speaker": "MARLE"}

`event` is "speak" (default), "stop" or "clear_context"; `speaker` is optional.
Packets without the right token, or that are malformed, are dropped and counted;
nothing a sender does can crash the listener.
"""

import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from retrobard.domain.models import DialogueEvent, EventType
from retrobard.domain.status import StatusKind, StatusSink, console_status, report

MAX_PACKET_BYTES = 8192
MAX_TEXT_CHARS = 1000
MAX_SPEAKER_CHARS = 40
# Speak events allowed in a burst, and how fast that allowance refills
SPEAK_BURST = 10
SPEAK_PER_SECOND = 2.0
LOG_EVERY_S = 10.0


def load_or_create_token(path: Path) -> str:
    """Returns the shared secret stored at `path`, creating a random one if missing."""
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    return token


def is_loopback(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_packet(data: bytes, token: str) -> tuple[DialogueEvent | None, str]:
    """Turns one datagram into an event. Returns (event, "") or (None, reason)."""
    if len(data) > MAX_PACKET_BYTES:
        return None, "too large"
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "not valid JSON"
    if not isinstance(payload, dict):
        return None, "not a JSON object"

    supplied = payload.get("token")
    if not isinstance(supplied, str) or not hmac.compare_digest(
        supplied.encode("utf-8"), token.encode("utf-8")
    ):
        return None, "wrong or missing token"

    try:
        event_type = EventType(payload.get("event", "speak"))
    except ValueError:
        return None, "unknown event"

    text = payload.get("text", "")
    speaker = payload.get("speaker")
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        return None, "bad text"
    if speaker is not None and (not isinstance(speaker, str) or len(speaker) > MAX_SPEAKER_CHARS):
        return None, "bad speaker"
    if event_type == EventType.SPEAK and not text.strip():
        return None, "empty text"

    return (
        DialogueEvent(
            event_type=event_type,
            raw_text=text,
            explicit_speaker=speaker or None,
            source="udp",
        ),
        "",
    )


class UdpListener:
    """EventProducer that accepts token-checked JSON datagrams on a loopback address."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        status: StatusSink = console_status,
    ):
        if not is_loopback(host):
            raise ValueError(
                f"UDP host {host!r} is not a loopback address. RetroBard only accepts "
                f"events from this computer (use 127.0.0.1)."
            )
        if not token:
            raise ValueError("A UDP token is required.")
        self._token = token
        self._status = status
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Windows: no other program may bind the same port and read the packets
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self._sock.bind((host, port))
        self._sock.settimeout(0.25)
        self.host, self.port = self._sock.getsockname()[:2]

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.accepted = 0
        self.rejected: dict[str, int] = {}
        self._unreported = 0
        self._last_log = 0.0
        self._allowance = float(SPEAK_BURST)
        self._allowance_at = time.monotonic()

    def start(self, emit: Callable[[DialogueEvent], None]) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(emit,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._sock.close()

    def _speak_allowed(self) -> bool:
        now = time.monotonic()
        self._allowance = min(
            SPEAK_BURST, self._allowance + (now - self._allowance_at) * SPEAK_PER_SECOND
        )
        self._allowance_at = now
        if self._allowance < 1.0:
            return False
        self._allowance -= 1.0
        return True

    def _reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1
        self._unreported += 1
        now = time.monotonic()
        if (now - self._last_log) >= LOG_EVERY_S:
            # Reasons only: packet contents are never printed
            summary = ", ".join(f"{count} {why}" for why, count in sorted(self.rejected.items()))
            report(
                self._status,
                StatusKind.WARNING,
                f"[UDP] Ignored {self._unreported} packet(s) so far this period ({summary} in total).",
                rejected=dict(self.rejected),
            )
            self._unreported = 0
            self._last_log = now

    def _run(self, emit: Callable[[DialogueEvent], None]) -> None:
        while not self._stop_event.is_set():
            try:
                data, _addr = self._sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                # Windows reports a previous send to a closed port here; the socket
                # is still usable unless we are shutting down.
                if self._stop_event.is_set():
                    return
                continue

            event, reason = parse_packet(data, self._token)
            if event is None:
                self._reject(reason)
                continue
            if event.event_type == EventType.SPEAK and not self._speak_allowed():
                self._reject("too many speak events")
                continue
            try:
                emit(event)
                self.accepted += 1
            except Exception as exc:  # noqa: BLE001 - a failure downstream must not stop the listener
                self._reject(f"handler error ({type(exc).__name__})")
