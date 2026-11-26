# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest
from typer.testing import CliRunner

from retrobard.adapters.producers.retroarch_memory import RetroArchError
from retrobard.app.memory_search import (
    check_search_text,
    dump_ram,
    relative_search,
    strip_copier_header,
)
from retrobard.cli import app

UPPER_A, LOWER_A, SPACE = 0x40, 0x5A, 0x01  # a made-up encoding, not ASCII


def encode(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(UPPER_A + ord(ch) - ord("A"))
        elif "a" <= ch <= "z":
            out.append(LOWER_A + ord(ch) - ord("a"))
        else:
            out.append(SPACE)
    return bytes(out)


def test_finds_text_and_alphabet_in_a_foreign_encoding() -> None:
    data = bytes(range(256)) * 4 + encode("Welcome to Potos") + b"\x00" * 50
    [hit] = relative_search(data, "Welcome to Potos")
    assert hit.offset == 1024
    assert (hit.upper_a, hit.lower_a) == (UPPER_A, LOWER_A)
    assert hit.decode(data, hit.offset, hit.offset + 7) == "Welcome"
    assert hit.decode(data, hit.offset, hit.offset + 8).endswith("[01]")


def test_punctuation_and_spaces_match_any_byte() -> None:
    data = b"\xff" * 10 + encode("Hey") + b"\x77" + encode("you") + b"\xff" * 10
    assert [h.offset for h in relative_search(data, "Hey, you")] == []  # one byte too many
    assert [h.offset for h in relative_search(data, "Hey you")] == [10]


def test_two_bytes_per_letter_with_steady_attribute() -> None:
    line = b"".join(bytes([b, 0x20]) for b in encode("Hello"))
    data = b"\x00" * 7 + line + b"\x00" * 7
    hits = relative_search(data, "Hello", stride=2)
    assert [h.offset for h in hits] == [7]
    assert relative_search(data, "Hello", stride=1) == []

    broken = bytearray(data)
    broken[7 + 3] = 0x21  # the attribute changes mid-word: not a line of text
    assert relative_search(bytes(broken), "Hello", stride=2) == []


def test_needs_enough_letters() -> None:
    assert check_search_text("Hi!") is not None
    assert check_search_text("Hi there") is None


def test_copier_header() -> None:
    assert strip_copier_header(b"\x00" * 512 + b"\x01" * 0x8000) == (b"\x01" * 0x8000, 512)
    assert strip_copier_header(b"\x01" * 0x8000) == (b"\x01" * 0x8000, 0)


class FakeClient:
    def __init__(self, memory: bytes):
        self.memory = memory

    def read(self, address: int, length: int) -> bytes:
        if address >= len(self.memory):
            raise RetroArchError("address out of range")
        return self.memory[address : address + length]


def test_dump_stops_at_the_end_of_memory() -> None:
    assert dump_ram(FakeClient(bytes(300)), 1000, chunk=128) == bytes(300)
    with pytest.raises(RetroArchError):
        dump_ram(FakeClient(b""), 1000)


def test_cli_on_a_file(tmp_path) -> None:
    rom = tmp_path / "game.bin"
    rom.write_bytes(b"\x00" * 100 + encode("Welcome to Potos") + b"\x00" * 100)
    result = CliRunner().invoke(app, ["find-text", "Welcome to Potos", "--file", str(rom)])
    assert result.exit_code == 0, result.output
    assert "offset 0x64" in result.output
    assert "'A' = 40, 'a' = 5A" in result.output
