# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Brings the user's own game profiles up to date when RetroBard is upgraded.

The packaged app copies a shipped profile only once (see install_default_profiles),
so later voice changes in the shipped profiles never reach existing users. On
startup this swaps voices ElevenLabs has retired for their replacements, and adds
characters a newer shipped profile voices that the user's profile does not have
yet. Characters the user already has are never changed, apart from retired voices.
"""

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from retrobard.config import CharacterVoiceConfig, GameProfile, VoicesConfig

log = logging.getLogger(__name__)

# ElevenLabs Default voices stop working on 2026-12-31. Each maps to the
# replacement ElevenLabs suggests (an Infinity voice, which does not expire).
RETIRED_VOICES: dict[str, str] = {
    "CwhRBWXzGAHq8TQ4Fs17": "gOupLcAkjEnguROwi4oS",  # Roger -> Darian
    "EXAVITQu4vr4xnSDxMaL": "OZ0L6eISlOejga3XjDFt",  # Sarah -> Talia
    "FGY2WhTYpPnrIDTdsKH5": "WQP7cQUF5aAS6Axh5yaa",  # Laura -> Elara
    "IKne3meq5aSn9XLyUdCD": "jSuBIjxMKhqIfb0wCK1F",  # Charlie -> Baxter
    "JBFqnCBsd6RMkjVDRZzb": "6WwXjDDEMyNmFG95zycZ",  # George -> Eldrin
    "N2lVS1w4EtoT3dr4eOWO": "fs2OqxduwXgp9foh2xjK",  # Callum -> Callum (Husky Trickster)
    "SAz9YHcvj6GT2YYXdXww": "dvbL7qkNGZY1IqPGZAjM",  # River -> Elowen
    "SOYHLrjzK2X1ezoPC6cr": "10NkTYmU7tSz3Kkl3Lex",  # Harry -> Kaelen
    "TX3LPaxmHKxFdv7VOQHJ": "ktkP7Nsj67dw2zcplQYt",  # Liam -> Lawrence
    "Xb7hH8MSUJpSbSDYk0k2": "BFd5oBc2DDna33pSi4Gf",  # Alice -> Alicia
    "XrExE9yKIg1WjnnlVkGX": "QtY3JBOUKEB5xzrRfOKc",  # Matilda -> Maisie
    "bIHbv24MWmeRgasZH58o": "7QN34D2r3hCNwbOYIeK0",  # Will -> Warren
    "cgSgspJ2msm6clMCkdW9": "g7LVvkPWALzPxOQbF6OE",  # Jessica -> Jade
    "cjVigY5qzO86Huf0OWal": "l7kNoIfnJKPg7779LI2t",  # Eric -> Eddie
    "iP95p4xoKVk53GoZ742B": "AaOhDHYJ1XLZk74lXhdE",  # Chris -> Caleb
    "nPczCjzI2devNBz1zQrb": "8dEUmyPMdDdK91vboYih",  # Brian -> Sawyer
    "onwK4e9ZLuTAKqWW03F9": "fnYMz3F5gMEDGMWcH1ex",  # Daniel -> Finley
    "pFZP5JQG7iQjIQuC4Bku": "22N9cF8z0o7y23njdyaY",  # Lily -> Florence
    "pqHfZKP75CvOlQylNhV4": "FrS6cKLB1wg4WYgPa9GW",  # Bill -> Wyatt
    # Charlotte is no longer available at all
    "XB0fDUnXU5powFXDhCwa": "dvbL7qkNGZY1IqPGZAjM",  # Charlotte -> Elowen
}


def _current(voice_id: str) -> str:
    return RETIRED_VOICES.get(voice_id, voice_id)


def replace_retired_voices(voices: VoicesConfig) -> int:
    """Swaps retired voices for their replacements. Returns how many it swapped."""
    swapped = 0

    def swap(voice_id: str) -> str:
        nonlocal swapped
        new = _current(voice_id)
        if new != voice_id:
            swapped += 1
        return new

    voices.default_narrator = swap(voices.default_narrator)
    for key, entry in voices.characters.items():
        if isinstance(entry, CharacterVoiceConfig):
            entry.voice_id = swap(entry.voice_id)
        else:
            voices.characters[key] = swap(entry)
    # A pool can end up holding the same voice twice once two retired voices
    # share a replacement, or the replacement was already there
    for name in ("female_pool", "male_pool"):
        pool = [swap(voice_id) for voice_id in getattr(voices, name)]
        setattr(voices, name, list(dict.fromkeys(pool)))
    return swapped


def add_shipped_characters(voices: VoicesConfig, shipped: VoicesConfig) -> list[str]:
    """Adds the characters `shipped` voices that `voices` does not have yet."""
    added = []
    for key, entry in shipped.characters.items():
        if key not in voices.characters:
            if isinstance(entry, CharacterVoiceConfig):
                entry = entry.model_copy()
            voices.characters[key] = entry
            added.append(key)
    return added


def update_profiles(bundled: Path | None, profiles_dir: Path) -> list[str]:
    """Updates the voices in every profile in `profiles_dir` and saves the ones
    that changed. Returns the games it updated.

    `bundled` is the folder of shipped profiles, None when running from source
    (where `profiles_dir` is the shipped folder, so there is nothing to add)."""
    if not profiles_dir.is_dir():
        return []
    same_folder = bundled is not None and bundled.resolve() == profiles_dir.resolve()
    updated = []
    for path in sorted(profiles_dir.glob("*.yaml")):
        try:
            profile = GameProfile.from_yaml(path)
            changed = replace_retired_voices(profile.voices) > 0
            shipped_path = bundled / path.name if bundled is not None else None
            if shipped_path is not None and not same_folder and shipped_path.exists():
                shipped = GameProfile.from_yaml(shipped_path)
                changed = bool(add_shipped_characters(profile.voices, shipped.voices)) or changed
            if changed:
                profile.save_to_yaml(path)
                updated.append(path.stem)
        except (OSError, yaml.YAMLError, ValidationError, AttributeError) as error:
            # A broken profile must not stop the app from starting; the Setup
            # page reports it when the user opens that game
            log.warning("Could not update voices in %s: %s", path, error)
    return updated
