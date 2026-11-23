# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import urllib.request
from dataclasses import dataclass

from elevenlabs.client import ElevenLabs

from retrobard import __version__


@dataclass(frozen=True)
class LibraryVoiceItem:
    voice_id: str
    name: str
    category: str          # "premade" or "generated" (from Web UI Voice Design)
    labels: dict[str, str] # e.g. {"accent": "british", "gender": "male", "use_case": "characters"}
    preview_url: str | None


@dataclass(frozen=True)
class VoicePreviewItem:
    index: int
    generated_voice_id: str
    audio_bytes: bytes
    duration_secs: float


class ElevenLabsVoiceDesigner:
    """Generates custom character voices from text prompts and saves the chosen preview."""

    DEFAULT_SAMPLE_TEMPLATE = (
        "Halt, travelers! Beyond this gate lies the ancient kingdom of Zeal. "
        "Keep your weapons ready, trust in your companions, and do not lose heart—our journey has only just begun!"
    )

    def __init__(self, api_key: str, model_id: str = "eleven_multilingual_ttv_v2"):
        if not api_key:
            raise ValueError(
                "No ElevenLabs API key. Add yours with the 'ElevenLabs key' button on the Play tab "
                "(or set ELEVENLABS_API_KEY)."
            )
        self.client = ElevenLabs(api_key=api_key)
        self.model_id = model_id
    
    def list_available_voices(self, search: str | None = None) -> list[LibraryVoiceItem]:
        """Fetches all voices accessible to your account (Premade + Web UI custom voices)."""
        response = self.client.voices.get_all()
        items: list[LibraryVoiceItem] = []
        query = search.strip().lower() if search else None

        for v in response.voices:
            labels = dict(v.labels) if getattr(v, "labels", None) else {}
            category = getattr(v, "category", "premade") or "premade"
            searchable_blob = " ".join(
                [v.name, category, *labels.values(), getattr(v, "description", "") or ""]
            ).lower()

            if query and query not in searchable_blob:
                continue

            items.append(
                LibraryVoiceItem(
                    voice_id=v.voice_id,
                    name=v.name,
                    category=category,
                    labels=labels,
                    preview_url=getattr(v, "preview_url", None),
                )
            )
        return items

    @staticmethod
    def download_free_preview(preview_url: str) -> bytes:
        """Downloads the pre-hosted sample MP3 from ElevenLabs CDN (costs 0 API credits)."""
        req = urllib.request.Request(preview_url, headers={"User-Agent": f"RetroBard/{__version__}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()

    def generate_previews(
        self, voice_description: str, sample_text: str | None = None
    ) -> list[VoicePreviewItem]:
        desc = voice_description.strip()
        if len(desc) < 20:
            raise ValueError(
                f"Voice description must be at least 20 characters (got {len(desc)})."
            )

        text = (sample_text or self.DEFAULT_SAMPLE_TEMPLATE).strip()
        # ElevenLabs requires preview text to be at least 100 characters
        while len(text) < 105:
            text = f"{text} {self.DEFAULT_SAMPLE_TEMPLATE}"
        text = text[:950]

        ttv = self.client.text_to_voice
        # Support both modern SDK (.design) and older 1.x SDK (.create_previews)
        design_fn = getattr(ttv, "design", None) or ttv.create_previews

        response = design_fn(
            model_id=self.model_id,
            voice_description=desc,
            text=text,
        )

        previews: list[VoicePreviewItem] = []
        for idx, prev in enumerate(response.previews, start=1):
            raw_audio = base64.b64decode(prev.audio_base_64)
            duration = getattr(prev, "duration_secs", 0.0) or 0.0
            previews.append(
                VoicePreviewItem(
                    index=idx,
                    generated_voice_id=prev.generated_voice_id,
                    audio_bytes=raw_audio,
                    duration_secs=duration,
                )
            )
        return previews

    def save_to_library(
        self,
        voice_name: str,
        voice_description: str,
        generated_voice_id: str,
    ) -> str:
        """Promotes a temporary preview ID into a permanent Voice ID in your library."""
        ttv = self.client.text_to_voice
        create_fn = getattr(ttv, "create", None) or ttv.create_voice_from_preview

        created = create_fn(
            voice_name=voice_name,
            voice_description=voice_description.strip(),
            generated_voice_id=generated_voice_id,
        )
        return created.voice_id