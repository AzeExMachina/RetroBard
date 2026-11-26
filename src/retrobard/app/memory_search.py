# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Finds a line of dialogue in game memory without knowing how the game encodes text.

This is the romhacking "relative search": games almost always store A-Z (and a-z)
as consecutive byte values, just not at ASCII's values. So "Hello" is found by
looking for bytes whose *differences* match the letters' differences (H->e->l->l->o),
whatever value the game gives "A". A hit tells both where the text is and most
of the game's alphabet.

Spaces, digits and punctuation in the searched text match any byte, since games
put them in unpredictable places. Upper and lower case are compared separately,
because many games do not keep them 32 apart like ASCII does.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from retrobard.adapters.producers.retroarch_memory import (
    RetroArchError,
    RetroArchMemoryClient,
)

# Letters of one case needed for a search narrow enough to be useful
MIN_LETTERS = 4
# Bytes shown before and after a hit
PREVIEW_BEFORE = 16
PREVIEW_AFTER = 48
SNES_WRAM_START = 0x7E0000


@dataclass(frozen=True)
class SearchHit:
    offset: int
    # 1: one byte per letter. 2: two bytes per letter (letter, then an attribute
    # byte, as in tile maps); the attribute bytes must all be equal.
    stride: int
    # The byte value the game uses for "A" and for "a" (None if that case was not searched)
    upper_a: int | None
    lower_a: int | None
    table: dict[int, str] = field(default_factory=dict)

    def decode(self, data: bytes, start: int, end: int) -> str:
        """The bytes in [start, end) as text, unknown ones as [XX]."""
        start = max(0, start - (start - self.offset) % self.stride)
        out = []
        for i in range(start, min(end, len(data)), self.stride):
            out.append(self.table.get(data[i], f"[{data[i]:02X}]"))
        return "".join(out)


def letter_groups(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """(uppercase, lowercase) letters of the text with their positions."""
    upper = [(i, c) for i, c in enumerate(text) if "A" <= c <= "Z"]
    lower = [(i, c) for i, c in enumerate(text) if "a" <= c <= "z"]
    return upper, lower


def check_search_text(text: str) -> str | None:
    """Why the text is not good enough to search for, or None if it is."""
    upper, lower = letter_groups(text)
    if max(len(upper), len(lower)) < MIN_LETTERS:
        return (
            f"Type at least {MIN_LETTERS} letters of the same case "
            f"(a longer piece of the line finds fewer false matches)."
        )
    return None


def _base_table(upper_a: int | None, lower_a: int | None) -> dict[int, str]:
    table: dict[int, str] = {}
    for base, first in ((upper_a, "A"), (lower_a, "a")):
        if base is None:
            continue
        for k in range(26):
            if 0 <= base + k <= 0xFF:
                table.setdefault(base + k, chr(ord(first) + k))
    return table


def relative_search(data: bytes, text: str, stride: int = 1) -> list[SearchHit]:
    """Every place in `data` where the letters of `text` appear with the same spacing."""
    upper, lower = letter_groups(text)
    span = (len(text) - 1) * stride + 1 + (stride - 1)
    count = len(data) - span + 1
    if count <= 0 or not (upper or lower):
        return []
    arr = np.frombuffer(data, dtype=np.uint8).astype(np.int16)

    def at(pos: int) -> np.ndarray:
        return arr[pos * stride : pos * stride + count]

    mask = np.ones(count, dtype=bool)
    bases: dict[str, np.ndarray] = {}
    for name, group, first in (("upper", upper, "A"), ("lower", lower, "a")):
        if not group:
            continue
        pos0, char0 = group[0]
        ref = at(pos0)
        for pos, char in group[1:]:
            mask &= (at(pos) - ref) == (ord(char) - ord(char0))
        base = ref - (ord(char0) - ord(first))
        # The whole alphabet must fit in a byte
        mask &= (base >= 0) & (base + 25 <= 0xFF)
        bases[name] = base

    if stride == 2:
        # In letter + attribute pairs, the attribute stays the same along a line
        positions = [pos for pos, _ in upper + lower]
        attr0 = arr[positions[0] * 2 + 1 : positions[0] * 2 + 1 + count]
        for pos in positions[1:]:
            mask &= arr[pos * 2 + 1 : pos * 2 + 1 + count] == attr0

    hits = []
    for offset in np.flatnonzero(mask):
        upper_a = int(bases["upper"][offset]) if "upper" in bases else None
        lower_a = int(bases["lower"][offset]) if "lower" in bases else None
        hits.append(
            SearchHit(
                offset=int(offset),
                stride=stride,
                upper_a=upper_a,
                lower_a=lower_a,
                table=_base_table(upper_a, lower_a),
            )
        )
    return hits


def dump_ram(
    client: RetroArchMemoryClient,
    size: int,
    chunk: int = 128,
    progress: Callable[[int], None] | None = None,
) -> bytes:
    """Reads `size` bytes from offset 0, stopping early where the memory ends."""
    data = bytearray()
    while len(data) < size:
        try:
            part = client.read(len(data), min(chunk, size - len(data)))
        except RetroArchError:
            if data:
                break  # past the end of the memory
            raise
        if not part:
            break
        data += part
        if progress:
            progress(len(data))
    return bytes(data)


def strip_copier_header(rom: bytes) -> tuple[bytes, int]:
    """SNES ROM dumps sometimes start with a 512-byte copier header. Returns
    (ROM without it, header size)."""
    if len(rom) % 0x8000 == 512:
        return rom[512:], 512
    return rom, 0
