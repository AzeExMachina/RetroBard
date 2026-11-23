# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""The user's API keys: ElevenLabs, and the optional one for a custom voice server.

Keys are kept in the operating system's credential store (Windows Credential
Manager), never in the app's folder or in a plain file. ELEVENLABS_API_KEY and
CUSTOM_TTS_API_KEY in the environment or .env still win, for development.
"""

import keyring
import keyring.errors

from retrobard.config import AppSettings

SERVICE = "RetroBard"
ACCOUNT = "elevenlabs_api_key"
CUSTOM_ACCOUNT = "custom_tts_api_key"


def stored_api_key(account: str = ACCOUNT) -> str:
    try:
        return keyring.get_password(SERVICE, account) or ""
    except (keyring.errors.KeyringError, OSError, RuntimeError):  # no usable credential store
        return ""


def store_api_key(key: str, account: str = ACCOUNT) -> None:
    keyring.set_password(SERVICE, account, key.strip())


def forget_api_key(account: str = ACCOUNT) -> None:
    try:
        keyring.delete_password(SERVICE, account)
    except keyring.errors.PasswordDeleteError:
        pass  # nothing was saved


def elevenlabs_api_key(settings: AppSettings) -> str:
    return settings.elevenlabs_api_key.strip() or stored_api_key()


def custom_tts_api_key(settings: AppSettings) -> str:
    """May be empty: servers on this computer usually need no key."""
    return settings.custom_tts_api_key.strip() or stored_api_key(CUSTOM_ACCOUNT)
