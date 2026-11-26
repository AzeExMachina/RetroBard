# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Turns a game's raw text bytes into a string using a character table.

Games rarely store text as ASCII: each one has its own table mapping bytes to
letters, often with multi-byte entries for dictionary compression ("F0 12" ->
"the ") and control codes for names, line breaks and the end of a message.
The table uses the romhacking ".tbl" convention: hex byte sequences on the left,
text on the right ("41=A", "F012=the ").
"""

from pathlib import Path

from retrobard.config import MemoryTextConfig


def parse_hex_bytes(key: str | int) -> bytes:
    """"41", "F0 12", "0xF012" or the YAML integer 0x41 -> the bytes they name."""
    if isinstance(key, int):
        if not 0 <= key <= 0xFF:
            raise ValueError(f"table code {key:#x} is not a single byte; write it as a hex string")
        return bytes([key])
    cleaned = key.strip().lower().removeprefix("0x").replace(" ", "")
    if not cleaned or len(cleaned) % 2:
        raise ValueError(f"table code {key!r} is not a whole number of hex bytes")
    return bytes.fromhex(cleaned)


def load_tbl(path: Path) -> dict[bytes, str]:
    """Reads a .tbl file ("41=A" per line). Lines starting with ';' or '#' are comments."""
    table: dict[bytes, str] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith((";", "#")):
            continue
        code, sep, text = line.partition("=")
        if not sep:
            raise ValueError(f"{path.name}:{line_no}: expected CODE=TEXT")
        table[parse_hex_bytes(code)] = text
    return table


class TextDecoder:
    def __init__(self, config: MemoryTextConfig, profile_dir: Path | None = None):
        table: dict[bytes, str] = {}
        if config.table_file:
            base = profile_dir or Path(".")
            table.update(load_tbl(base / config.table_file))
        table.update({parse_hex_bytes(k): v for k, v in config.table.items()})
        self._table = table
        self._longest = max((len(code) for code in table), default=1)
        self._end = {parse_hex_bytes(c) for c in config.end_codes}
        self._newline = {parse_hex_bytes(c) for c in config.newline_codes}
        self._args = {parse_hex_bytes(c): n for c, n in config.control_args.items()}
        self._breaks = {parse_hex_bytes(c) for c in config.break_codes}
        self._unknown = config.unknown

    def is_break(self, byte: int, command_below: int) -> bool:
        """Whether `byte` ends a box: one of the break codes if there are any,
        otherwise any script command."""
        if self._breaks:
            return bytes([byte]) in self._breaks
        return byte < command_below

    def text_start(self, data: bytes, end: int, command_below: int) -> int:
        """Walks back from `end` over the text before it and returns where it begins.

        With break codes, the text runs back to the previous one. Otherwise bytes
        at or above `command_below` are text, and table entries are text too even
        when they start with a command byte (e.g. "5700" = the hero's name)."""
        if self._breaks:
            j = end
            while j > 0 and data[j - 1 : j] not in self._breaks:
                j -= 1
            return j
        j = end
        while j > 0:
            if data[j - 1] >= command_below:
                j -= 1
                continue
            for size in range(min(self._longest, j), 1, -1):
                if data[j - size : j] in self._table:
                    j -= size
                    break
            else:
                break
        return j

    def decode(self, data: bytes) -> str:
        """Decodes up to the first end code. Line breaks become "\\n", like OCR output."""
        out: list[str] = []
        i = 0
        while i < len(data):
            for size in range(min(self._longest, len(data) - i), 0, -1):
                code = data[i : i + size]
                if code in self._end:
                    return _tidy("".join(out))
                if code in self._newline:
                    out.append("\n")
                    i += size
                    break
                if code in self._args:
                    # A control code followed by argument bytes (a pause length,
                    # a colour, a sound...): nothing to say out loud
                    out.append(self._table.get(code, ""))
                    i += size + self._args[code]
                    break
                if code in self._table:
                    out.append(self._table[code])
                    i += size
                    break
            else:
                if self._unknown == "stop":
                    break
                i += 1  # "skip": an unmapped byte is most likely a control code
        return _tidy("".join(out))


def _tidy(text: str) -> str:
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)
