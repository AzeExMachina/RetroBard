# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import socket
import time
from pathlib import Path

import pytest

from retrobard.adapters.producers import udp_listener as udp
from retrobard.adapters.producers.udp_listener import (
    UdpListener,
    is_loopback,
    load_or_create_token,
    parse_packet,
)
from retrobard.domain.models import DialogueEvent, EventType

TOKEN = "test-token"


def _packet(**fields) -> bytes:
    return json.dumps({"token": TOKEN, **fields}).encode("utf-8")


class TestParsePacket:
    def test_speak_with_speaker(self) -> None:
        event, reason = parse_packet(_packet(text="Come on!", speaker="MARLE"), TOKEN)
        assert reason == ""
        assert event == DialogueEvent(
            event_type=EventType.SPEAK, raw_text="Come on!", explicit_speaker="MARLE", source="udp"
        )

    def test_stop_and_clear_context_need_no_text(self) -> None:
        assert parse_packet(_packet(event="stop"), TOKEN)[0].event_type == EventType.STOP
        assert (
            parse_packet(_packet(event="clear_context"), TOKEN)[0].event_type
            == EventType.CLEAR_CONTEXT
        )

    @pytest.mark.parametrize(
        "data, reason",
        [
            (b"\xff\xfe not text", "not valid JSON"),
            (b"{broken", "not valid JSON"),
            (b"[1, 2, 3]", "not a JSON object"),
            (json.dumps({"text": "hi"}).encode(), "wrong or missing token"),
            (json.dumps({"token": "nope", "text": "hi"}).encode(), "wrong or missing token"),
            (json.dumps({"token": 123, "text": "hi"}).encode(), "wrong or missing token"),
            (_packet(event="explode", text="hi"), "unknown event"),
            (_packet(event=["speak"], text="hi"), "unknown event"),
            (_packet(text=42), "bad text"),
            (_packet(text="x" * 1001), "bad text"),
            (_packet(text="hi", speaker={"name": "X"}), "bad speaker"),
            (_packet(text="hi", speaker="S" * 41), "bad speaker"),
            (_packet(text="   "), "empty text"),
            (_packet(text="a", pad="p" * 9000), "too large"),
        ],
    )
    def test_bad_packets_are_rejected_with_a_reason(self, data: bytes, reason: str) -> None:
        assert parse_packet(data, TOKEN) == (None, reason)


class TestToken:
    def test_token_is_created_once_and_reused(self, tmp_path: Path) -> None:
        path = tmp_path / "cache" / "udp_token"
        first = load_or_create_token(path)
        assert len(first) >= 24
        assert load_or_create_token(path) == first

    def test_only_loopback_hosts_are_accepted(self) -> None:
        assert is_loopback("127.0.0.1")
        assert is_loopback("localhost")
        assert not is_loopback("0.0.0.0")
        assert not is_loopback("192.168.1.20")
        with pytest.raises(ValueError, match="loopback"):
            UdpListener("0.0.0.0", 0, TOKEN)

    def test_a_token_is_required(self) -> None:
        with pytest.raises(ValueError, match="token"):
            UdpListener("127.0.0.1", 0, "")


@pytest.fixture
def listener():
    events: list[DialogueEvent] = []
    messages: list[str] = []
    instance = UdpListener(
        "127.0.0.1", 0, TOKEN, status=lambda event: messages.append(event.message)
    )
    instance.start(events.append)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    yield instance, events, messages, sender
    sender.close()
    instance.stop()


def _wait_for(condition, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class TestListenerOverLoopback:
    def test_valid_packet_becomes_an_event(self, listener) -> None:
        instance, events, _, sender = listener
        sender.sendto(_packet(text="CRONO: Let's go!"), (instance.host, instance.port))

        assert _wait_for(lambda: len(events) == 1)
        assert events[0].raw_text == "CRONO: Let's go!"

    def test_garbage_does_not_stop_the_listener(self, listener) -> None:
        instance, events, messages, sender = listener
        address = (instance.host, instance.port)
        for junk in (b"", b"\x00\x01\x02", b"{not json", b"null", _packet(event="explode")):
            sender.sendto(junk, address)
        sender.sendto(json.dumps({"token": "wrong", "text": "spend my credits"}).encode(), address)
        sender.sendto(_packet(text="Still alive."), address)

        assert _wait_for(lambda: len(events) == 1)
        assert events[0].raw_text == "Still alive."
        assert sum(instance.rejected.values()) == 6
        # The log line names reasons, never what a packet contained
        assert messages and "spend my credits" not in " ".join(messages)

    def test_flood_of_speak_events_is_limited(self, listener, monkeypatch) -> None:
        instance, events, _, sender = listener
        monkeypatch.setattr(udp, "SPEAK_PER_SECOND", 0.0)  # no refill during the test
        for i in range(udp.SPEAK_BURST + 15):
            sender.sendto(_packet(text=f"Line {i}."), (instance.host, instance.port))

        assert _wait_for(
            lambda: len(events) + instance.rejected.get("too many speak events", 0)
            == udp.SPEAK_BURST + 15
        )
        assert len(events) == udp.SPEAK_BURST

    def test_a_failing_handler_does_not_stop_the_listener(self) -> None:
        seen: list[str] = []

        def handler(event: DialogueEvent) -> None:
            seen.append(event.raw_text)
            if event.raw_text == "boom":
                raise RuntimeError("downstream failure")

        instance = UdpListener("127.0.0.1", 0, TOKEN, status=lambda _: None)
        instance.start(handler)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sender.sendto(_packet(text="boom"), (instance.host, instance.port))
            sender.sendto(_packet(text="after"), (instance.host, instance.port))
            assert _wait_for(lambda: seen == ["boom", "after"])
        finally:
            sender.close()
            instance.stop()
