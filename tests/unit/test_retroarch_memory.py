# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import socket
import threading

import pytest
from pydantic import ValidationError

from retrobard.adapters.producers.memory_text import (
    TextDecoder,
    load_tbl,
    parse_hex_bytes,
)
from retrobard.adapters.producers.retroarch_memory import (
    RetroArchError,
    RetroArchMemoryClient,
    RetroArchMemoryWatcher,
    RomImage,
    parse_reply,
)
from retrobard.config import MemoryConfig, MemoryTextConfig, MemoryValue, RomFile
from retrobard.domain.models import EventType
from retrobard.domain.status import null_status

TEXT_AT = 0x7E2000
FLAG_AT = 0x7E0100
SPEAKER_AT = 0x7E0101
# A tiny made-up encoding: 0x01-0x1A = A-Z, 0x20 = space, 0x7F = new line, 0xF0 = "THE "
TABLE = {f"{i + 1:02X}": chr(ord("A") + i) for i in range(26)} | {"20": " ", "F0": "THE "}


def encode(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        out += b"\x7f" if ch == "\n" else b"\x20" if ch == " " else bytes([ord(ch) - ord("A") + 1])
    return bytes(out) + b"\x00"


class FakeMemory:
    """Stands in for RetroArchMemoryClient."""

    def __init__(self):
        self.ram: dict[int, int] = {}

    def write(self, address: int, data: bytes) -> None:
        for i, b in enumerate(data):
            self.ram[address + i] = b

    def read(self, address: int, length: int) -> bytes:
        return bytes(self.ram.get(address + i, 0) for i in range(length))

    def read_value(self, value: MemoryValue) -> int:
        return int.from_bytes(self.read(value.address, value.size), "little")

    def close(self) -> None:
        pass


def make_config(**extra) -> MemoryConfig:
    text = {"address": TEXT_AT, "max_bytes": 64, "table": TABLE, "newline_codes": ["7F"]}
    text.update(extra.pop("text", {}))
    return MemoryConfig(settle_ms=100, text=text, **extra)


def make_watcher(config: MemoryConfig, memory: FakeMemory, rom_path=None) -> RetroArchMemoryWatcher:
    return RetroArchMemoryWatcher(config, client=memory, rom_path=rom_path, status=null_status)


class TestDecoder:
    def test_table_newlines_and_end(self) -> None:
        decoder = TextDecoder(make_config().text)
        assert decoder.decode(encode("HI THERE\nBYE") + b"\x05\x05") == "HI THERE\nBYE"

    def test_multi_byte_codes_win_over_single_bytes(self) -> None:
        config = MemoryTextConfig(address=0, table=TABLE | {"0809": "HI!"})
        assert TextDecoder(config).decode(b"\x08\x09\x00") == "HI!"

    def test_control_args_are_skipped_and_unknown_bytes_dropped(self) -> None:
        config = MemoryTextConfig(address=0, table=TABLE, control_args={"E0": 2})
        assert TextDecoder(config).decode(b"\x08\xe0\x01\x02\x09\x99\x00") == "HI"

    def test_dictionary_entry(self) -> None:
        config = MemoryTextConfig(address=0, table=TABLE)
        assert TextDecoder(config).decode(b"\xf0\x05\x0e\x04\x00") == "THE END"

    def test_tbl_file(self, tmp_path) -> None:
        (tmp_path / "game.tbl").write_text("; comment\n41=A\nF012=the \n", encoding="utf-8")
        assert load_tbl(tmp_path / "game.tbl") == {b"A": "A", b"\xf0\x12": "the "}
        config = MemoryTextConfig(address=0, table_file="game.tbl")
        assert TextDecoder(config, tmp_path).decode(b"\xf0\x12\x41\x00") == "the A"

    def test_hex_keys(self) -> None:
        assert parse_hex_bytes("0xF0 12") == b"\xf0\x12"
        assert parse_hex_bytes(0x41) == b"A"
        with pytest.raises(ValueError):
            parse_hex_bytes("F")


class TestConfig:
    def test_needs_exactly_one_text_location(self) -> None:
        with pytest.raises(ValidationError):
            MemoryTextConfig()
        with pytest.raises(ValidationError):
            MemoryTextConfig(address=1, pointer={"address": 2})

    def test_table_file_must_stay_next_to_profile(self) -> None:
        with pytest.raises(ValidationError):
            MemoryTextConfig(address=0, table_file="../secrets.tbl")


class TestWatcher:
    def test_speaks_once_text_settles_then_clears_when_empty(self) -> None:
        memory = FakeMemory()
        watcher = make_watcher(make_config(), memory)
        assert watcher.poll_once(0.0) == []

        memory.write(TEXT_AT, encode("HELLO"))
        assert watcher.poll_once(1.00) == []  # just appeared: wait for it to settle
        [event] = watcher.poll_once(1.15)
        assert (event.event_type, event.raw_text) == (EventType.SPEAK, "HELLO")
        assert watcher.poll_once(1.30) == []  # spoken once only

        memory.write(TEXT_AT, b"\x00")
        [event] = watcher.poll_once(2.0)
        assert event.event_type == EventType.CLEAR_CONTEXT

    def test_text_still_being_written_is_not_spoken(self) -> None:
        memory = FakeMemory()
        watcher = make_watcher(make_config(), memory)
        memory.write(TEXT_AT, encode("HEL"))
        watcher.poll_once(0.0)
        memory.write(TEXT_AT, encode("HELLO"))
        assert watcher.poll_once(0.11) == []  # changed: the settle timer restarts
        assert watcher.poll_once(0.22)[0].raw_text == "HELLO"

    def test_box_flag_pointer_and_speaker(self) -> None:
        memory = FakeMemory()
        config = make_config(
            box_open={"address": FLAG_AT},
            speaker={"address": SPEAKER_AT, "names": {2: "PRIMM"}},
            text={"address": None, "pointer": {"address": 0x7E0200, "size": 2, "bank": 0x7E}},
        )
        watcher = make_watcher(config, memory)
        memory.write(0x7E0200, (0x3000).to_bytes(2, "little"))
        memory.write(0x7E3000, encode("HI"))
        memory.write(SPEAKER_AT, b"\x02")

        assert watcher.poll_once(0.0) == []  # box closed: text ignored
        memory.write(FLAG_AT, b"\x01")
        watcher.poll_once(1.0)
        [event] = watcher.poll_once(1.2)
        assert (event.raw_text, event.explicit_speaker) == ("HI", "PRIMM")

        memory.write(FLAG_AT, b"\x00")
        assert watcher.poll_once(2.0)[0].event_type == EventType.CLEAR_CONTEXT
        assert watcher.poll_once(2.1) == []


class TestCursorFromRom:
    """Modelled on Secret of Mana: text printed straight from ROM, a RAM cursor
    walking through it, script commands below 0x7F, 57 xx = a hero's name."""

    CURSOR_AT = 0x1D01
    # Text bytes all at or above 0x7F, as in the game
    TABLE = {f"{0x9B + i:02X}": chr(ord("A") + i) for i in range(26)} | {
        "80": " ",
        "C5": ":",
        "5700": "RANDI",
    }

    @staticmethod
    def som(text: str) -> bytes:
        special = {" ": 0x80, ":": 0xC5, "\n": 0x7F}
        return bytes(special.get(ch) or 0x9B + ord(ch) - ord("A") for ch in text)

    def _setup(self, tmp_path):
        script = (
            b"\x28\x08\x52"  # commands before the box
            + self.som("ELLIOTT: SHH\nBE QUIET")
            + b"\x28\x00"  # wait for a button
            + b"\x7f\x32\x06\x40"
            + b"\x57\x00"  # the hero's name
            + self.som(": HELP")
            + b"\x28\x00"
        )
        rom = bytes(0x10000) + bytes(0x123) + script + bytes(0x100)  # bank 1 in HiROM
        path = tmp_path / "game.sfc"
        path.write_bytes(rom)
        config = make_config(
            command="read_core_ram",
            text={
                "address": None,
                "cursor": {"address": self.CURSOR_AT, "size": 3},
                "rom": {"sha1": hashlib.sha1(rom).hexdigest(), "mapping": "hirom"},
                "table": self.TABLE,
            },
        )
        memory = FakeMemory()
        base = 0xC10123  # where the script starts, as the console sees it
        return make_watcher(config, memory, rom_path=path), memory, base, script

    def _move(self, memory, address: int) -> None:
        memory.write(self.CURSOR_AT, address.to_bytes(3, "little"))

    def test_speaks_each_box_when_the_cursor_waits(self, tmp_path) -> None:
        watcher, memory, base, script = self._setup(tmp_path)
        first_wait = base + script.index(b"\x28\x00")
        second_wait = base + script.rindex(b"\x28\x00")

        self._move(memory, base + 3 + 4)  # mid-text: nothing yet
        watcher.poll_once(0.0)
        assert watcher.poll_once(1.0) == []

        self._move(memory, first_wait)
        watcher.poll_once(2.0)
        [event] = watcher.poll_once(2.2)
        assert event.raw_text == "ELLIOTT: SHH\nBE QUIET"
        assert watcher.poll_once(3.0) == []  # once only

        self._move(memory, second_wait)
        watcher.poll_once(4.0)
        assert watcher.poll_once(4.2)[0].raw_text == "RANDI: HELP"

    def test_wrong_rom_is_refused(self, tmp_path) -> None:
        path = tmp_path / "other.sfc"
        path.write_bytes(b"\x00" * 0x8000)
        config = make_config(
            text={"address": None, "cursor": {"address": 0}, "rom": {"sha1": "0" * 40}}
        )
        with pytest.raises(ValueError, match="not the ROM"):
            make_watcher(config, FakeMemory(), rom_path=path)
        with pytest.raises(ValueError, match="choose the ROM"):
            make_watcher(config, FakeMemory())

    def test_mappings(self, tmp_path) -> None:
        path = tmp_path / "rom.sfc"
        path.write_bytes(bytes(512) + bytes(range(256)) * 512)  # with a copier header
        sha1 = hashlib.sha1(path.read_bytes()).hexdigest()
        hirom = RomImage(path, RomFile(sha1=sha1, mapping="hirom"))
        assert hirom.offset(0xC92946) == 0x92946
        assert hirom.offset(0x092946) == 0x92946
        assert hirom.read(0xC00010, 2) == b"\x10\x11"  # header skipped
        lorom = RomImage(path, RomFile(sha1=sha1, mapping="lorom"))
        assert lorom.offset(0x018000) == 0x8000


class TestCursorAfterBreaks:
    """Modelled on Chrono Trigger: pure-text script with 00 (end) and 0C (page)
    breaks, a dictionary, name codes, and a cursor that rests just past the break."""

    CURSOR_AT = 0x231
    TABLE = TestCursorFromRom.TABLE | {"13": "CRONO", "21": "THE "}

    som = staticmethod(TestCursorFromRom.som)

    def _setup(self, tmp_path):
        script = (
            b"\x00"
            + self.som("GIRL: OUCH")
            + b"\x00"  # box 1 ends
            + self.som("HI ")
            + b"\x13"  # CRONO
            + b"\x0c"  # page break
            + b"\x21"  # THE
            + self.som("END")
            + b"\x00"  # box 3 ends
            + self.som("NEVER SHOWN")
            + b"\x00"
            + self.som("AFTER A CHOICE")
            + b"\x00"
        )
        rom = bytes(0x100) + script + bytes(0x100)
        path = tmp_path / "game.sfc"
        path.write_bytes(rom)
        config = make_config(
            command="read_core_ram",
            text={
                "address": None,
                "cursor": {"address": self.CURSOR_AT, "size": 3},
                "cursor_rests": "after",
                "break_codes": ["00", "0B", "0C"],
                "require_progress": True,
                "rom": {"sha1": hashlib.sha1(rom).hexdigest(), "mapping": "linear"},
                "table": self.TABLE,
            },
        )
        memory = FakeMemory()
        return make_watcher(config, memory, rom_path=path), memory, 0x100, script

    def _walk(self, watcher, memory, start: int, end: int, t: float) -> list:
        """Moves the cursor through [start, end] a byte per poll, then lets it rest."""
        events = []
        for address in range(start, end + 1):
            memory.write(self.CURSOR_AT, address.to_bytes(3, "little"))
            events += watcher.poll_once(t)
            t += 0.05
        for _ in range(4):
            events += watcher.poll_once(t)
            t += 0.05
        return events

    def test_boxes_pages_names_and_dictionary(self, tmp_path) -> None:
        watcher, memory, base, script = self._setup(tmp_path)
        box1_end = base + script.index(b"\x00", 1) + 1  # just past its 00
        page_end = base + script.index(b"\x0c") + 1
        box3_end = base + script.index(b"\x00", page_end - base) + 1

        assert [e.raw_text for e in self._walk(watcher, memory, base + 1, box1_end, 0.0)] == [
            "GIRL: OUCH"
        ]
        assert [e.raw_text for e in self._walk(watcher, memory, box1_end + 1, page_end, 10.0)] == [
            "HI CRONO"
        ]
        assert [e.raw_text for e in self._walk(watcher, memory, page_end + 1, box3_end, 20.0)] == [
            "THE END"
        ]

    def test_a_message_jumped_over_is_not_spoken(self, tmp_path) -> None:
        watcher, memory, base, script = self._setup(tmp_path)
        box3_end = base + script.index(b"\x00", script.index(b"\x0c")) + 1
        skipped_end = base + script.index(b"\x00", box3_end - base) + 1
        self._walk(watcher, memory, box3_end - 2, box3_end, 0.0)
        # The game jumps straight past "NEVER SHOWN" (e.g. after a choice)
        memory.write(self.CURSOR_AT, skipped_end.to_bytes(3, "little"))
        assert [watcher.poll_once(t) for t in (5.0, 5.2, 5.4)] == [[], [], []]
        # ...then prints the next message normally
        final_end = base + len(script)
        events = self._walk(watcher, memory, skipped_end + 1, final_end, 6.0)
        assert [e.raw_text for e in events] == ["AFTER A CHOICE"]

    def test_text_entered_far_from_its_start_is_not_spoken(self, tmp_path) -> None:
        watcher, memory, base, script = self._setup(tmp_path)
        # The cursor jumps into the middle of "NEVER SHOWN" and walks to its end:
        # the text before the jump was never printed
        start = base + script.index(self.som("NEVER"))
        assert self._walk(watcher, memory, start + 9, start + 12, 0.0) == []


class TestProtocol:
    def test_parse_reply(self) -> None:
        assert parse_reply(b"READ_CORE_MEMORY 7e2000 41 42\n", 0x7E2000) == b"AB"
        assert parse_reply(b"READ_CORE_MEMORY 7e1000 41\n", 0x7E2000) is None
        assert parse_reply(b"READ_CORE_RAM 2000 41\n", 0x2000, "READ_CORE_RAM") == b"A"
        with pytest.raises(RetroArchError):
            parse_reply(b"READ_CORE_MEMORY 2000 41\n", 0x2000, "READ_CORE_RAM")
        with pytest.raises(RetroArchError, match="no memory map"):
            parse_reply(b"READ_CORE_MEMORY 7e2000 -1 no memory map defined\n", 0x7E2000)

    def test_refuses_remote_host(self) -> None:
        with pytest.raises(ValueError):
            RetroArchMemoryClient("192.0.2.1", 55355)

    def test_reads_in_chunks_from_a_fake_retroarch(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        server.settimeout(2.0)
        memory = bytes(range(10))
        requests: list[str] = []

        def serve() -> None:
            for _ in range(3):
                data, addr = server.recvfrom(1024)
                _, address, length = data.decode().split()
                requests.append(f"{address} {length}")
                start = int(address, 16) - 0x7E0000
                body = " ".join(f"{b:02x}" for b in memory[start : start + int(length)])
                server.sendto(f"READ_CORE_MEMORY {address} {body}\n".encode(), addr)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        client = RetroArchMemoryClient("127.0.0.1", server.getsockname()[1], chunk_bytes=4)
        try:
            assert client.read(0x7E0000, 10) == memory
        finally:
            client.close()
            thread.join(timeout=2.0)
            server.close()
        assert requests == ["7e0000 4", "7e0004 4", "7e0008 2"]


def test_shipped_mana_table_reads_numbers_and_symbols() -> None:
    from pathlib import Path

    from retrobard.config import GameProfile

    profiles = Path(__file__).resolve().parents[2] / "profiles"
    profile = GameProfile.from_yaml(profiles / "secret_of_mana.yaml")
    decoder = TextDecoder(profile.memory.text, profiles)
    # Straight from the ROM: " 'till you have 100% power,"
    line = bytes.fromhex("80 c2 94 89 8c 8c 80 99 8f 95 80 88 81 96 85 80 b6 b5 b5 c7 80 90 8f 97 85 92 c0")
    assert decoder.decode(line) == "'till you have 100% power,"
    # "re-energized", "L/R buttons"
    assert decoder.decode(bytes.fromhex("92 85 c6 85 8e 85 92 87 89 9a 85 84")) == "re-energized"
    assert decoder.decode(bytes.fromhex("a6 c1 ac")) == "L/R"


class TestRomDictionary:
    """Modelled on Chrono Trigger: one-byte letter groups whose text sits in the
    ROM's own table (2-byte pointers from code 0, then length + letters)."""

    LETTERS = {f"{0xA0 + i:02X}": chr(ord("A") + i) for i in range(26)} | {"EF": " "}

    @staticmethod
    def ct(text: str) -> bytes:
        return bytes(0xEF if ch == " " else 0xA0 + ord(ch) - ord("A") for ch in text)

    def _rom(self, groups: dict[int, bytes]) -> bytes:
        rom = bytearray(0x20000)
        table, strings = 0x18000, 0x19000  # bank 1 (HiROM $C1xxxx)
        for code, raw in groups.items():
            rom[table + 2 * code : table + 2 * code + 2] = (strings & 0xFFFF).to_bytes(2, "little")
            rom[strings] = len(raw)
            rom[strings + 1 : strings + 1 + len(raw)] = raw
            strings += 1 + len(raw)
        return bytes(rom)

    def _config(self, rom: bytes, **dictionary) -> MemoryConfig:
        return make_config(
            command="read_core_ram",
            text={
                "address": None,
                "cursor": {"address": 0x231, "size": 3},
                "rom": {"sha1": hashlib.sha1(rom).hexdigest(), "mapping": "hirom"},
                "rom_dictionary": {"first_code": 0x21, "last_code": 0x24, "pointers": 0xC18000}
                | dictionary,
                "table": self.LETTERS,
            },
        )

    def test_letter_groups_come_from_the_rom(self, tmp_path) -> None:
        groups = {0x21: self.ct("THE "), 0x22: self.ct("OU"), 0x23: b"\x05", 0x24: b""}
        rom = self._rom(groups)
        path = tmp_path / "game.sfc"
        path.write_bytes(rom)
        watcher = make_watcher(self._config(rom), FakeMemory(), rom_path=path)
        decoder = watcher._decoder
        # 0x23 holds a byte the table does not know and 0x24 is empty: both left out
        assert decoder.letters(b"\x21") == "THE "
        assert decoder.letters(b"\x23") is None and decoder.letters(b"\x24") is None
        assert decoder.decode(b"\x21\xb3\x22\xa2\xa7\x00") == "THE TOUCH"

    def test_the_table_file_wins_over_the_rom(self, tmp_path) -> None:
        rom = self._rom({0x21: self.ct("THE")})
        path = tmp_path / "game.sfc"
        path.write_bytes(rom)
        config = self._config(rom)
        config.text.table["21"] = "A"
        watcher = make_watcher(config, FakeMemory(), rom_path=path)
        assert watcher._decoder.letters(b"\x21") == "A"

    def test_needs_the_rom(self) -> None:
        with pytest.raises(ValidationError, match="rom_dictionary needs text.rom"):
            MemoryTextConfig(
                cursor={"address": 0x231, "size": 3},
                rom_dictionary={"first_code": 0x21, "last_code": 0x9F, "pointers": 0xDEF9BE},
            )

    def test_shipped_chrono_trigger_table_holds_no_game_text(self) -> None:
        from pathlib import Path

        from retrobard.adapters.producers.memory_text import load_tbl

        profiles = Path(__file__).resolve().parents[2] / "profiles"
        table = load_tbl(profiles / "chrono_trigger.tbl")
        assert not [code for code in table if len(code) == 1 and 0x21 <= code[0] <= 0x9F]
