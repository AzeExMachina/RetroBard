# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Speech from any server that speaks the OpenAI speech API.

That is OpenAI itself and most text-to-speech servers people run on their own
computer (Kokoro-FastAPI, Speaches, LocalAI, openedai-speech...). They all take
POST <base_url>/audio/speech with {model, voice, input, response_format, speed}.
"""

import json
import time
from collections.abc import Iterator

import httpx

from retrobard import __version__
from retrobard.config import CustomTTSConfig
from retrobard.domain.models import VoiceProfile

MAX_ERROR_CHARS = 300


class CustomTTSError(RuntimeError):
    """The server could not be reached or refused the request."""


def _error_detail(response: httpx.Response) -> str:
    """The server's own explanation, kept short (OpenAI and FastAPI shapes)."""
    try:
        body = response.read().decode("utf-8", errors="replace")
    except httpx.HTTPError:
        return ""
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:MAX_ERROR_CHARS]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:MAX_ERROR_CHARS]
        if data.get("detail"):
            return str(data["detail"])[:MAX_ERROR_CHARS]
    return body.strip()[:MAX_ERROR_CHARS]


class OpenAICompatibleTTS:
    """Streams speech from an OpenAI-compatible server.

    With response_format "pcm" the bytes are raw 16-bit mono samples (24 kHz for
    OpenAI and Kokoro) that can be played the moment they arrive.
    """

    def __init__(
        self,
        config: CustomTTSConfig,
        api_key: str = "",
        transport: httpx.BaseTransport | None = None,
    ):
        self.config = config
        headers = {"User-Agent": f"RetroBard/{__version__}"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # One client for the whole session keeps the connection open between lines
        self.client = httpx.Client(
            base_url=config.base_url,
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=5.0),
            transport=transport,
        )

    def warm_up(self) -> float | None:
        """Opens the connection ahead of the first line. Any answer will do (not
        every server has /models); returns how long it took, or None if the server
        could not be reached."""
        start = time.perf_counter()
        try:
            self.client.get("/models")
        except httpx.HTTPError:
            return None
        return time.perf_counter() - start

    def _request_body(self, text: str, voice: VoiceProfile) -> dict:
        body = {
            "model": self.config.model,
            "voice": voice.voice_id or self.config.default_voice,
            "input": text,
            "response_format": self.config.response_format,
        }
        # Left out at normal speed: not every server accepts the field
        if abs(voice.speed - 1.0) >= 0.01:
            body["speed"] = round(voice.speed, 2)
        return body

    def synthesize_stream(self, text: str, voice: VoiceProfile) -> Iterator[bytes]:
        try:
            with self.client.stream(
                "POST", "/audio/speech", json=self._request_body(text, voice)
            ) as response:
                if response.status_code >= 400:
                    detail = _error_detail(response)
                    raise CustomTTSError(
                        f"The voice server answered {response.status_code}"
                        + (f": {detail}" if detail else "")
                    )
                for chunk in response.iter_bytes():
                    if chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            raise CustomTTSError(
                f"Could not reach the voice server at {self.config.base_url}: {exc}"
            ) from exc

    def close(self) -> None:
        self.client.close()
