# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np

from retrobard.adapters.ocr.glyph_reader import GlyphReader, letter_pixels
from retrobard.config import OcrConfig
from retrobard.domain.models import DialogueEvent, EventType
from retrobard.domain.ports import FrameSource
from retrobard.domain.status import StatusKind, StatusSink, console_status, report


def is_dialogue_box_present(
    bgra_frame: np.ndarray, binary_threshold: int
) -> bool:
    """
    Verifies the capture region looks like an RPG dialogue box (mostly dark/uniform
    background with a modest amount of bright text) rather than overworld map tiles.
    """
    gray = cv2.cvtColor(bgra_frame, cv2.COLOR_BGRA2GRAY)

    # In a real dialogue box, at least 68% of pixels belong to the dark window interior
    dark_cutoff = min(130, int(binary_threshold * 0.72))
    dark_ratio = np.count_nonzero(gray < dark_cutoff) / gray.size
    return dark_ratio >= 0.68


def preprocess_retro_frame(
    bgra_frame: np.ndarray,
    upscale_factor: int,
    binary_threshold: int,
    cursor_mask_ratio: float,
) -> np.ndarray:
    """Converts raw emulator pixels into an upscaled binary image with cursor masked."""
    gray = cv2.cvtColor(bgra_frame, cv2.COLOR_BGRA2GRAY)

    if upscale_factor > 1:
        gray = cv2.resize(
            gray,
            None,
            fx=upscale_factor,
            fy=upscale_factor,
            interpolation=cv2.INTER_NEAREST,
        )

    _, binary = cv2.threshold(gray, binary_threshold, 255, cv2.THRESH_BINARY)
    return _mask_cursor_and_pad(binary, cursor_mask_ratio)


def _mask_cursor_and_pad(binary: np.ndarray, cursor_mask_ratio: float) -> np.ndarray:
    """Blanks the bottom-right corner where a blinking 'more text' cursor sits."""
    if cursor_mask_ratio > 0.0:
        h, w = binary.shape
        mask_h = int(h * (cursor_mask_ratio * 1.5))
        mask_w = int(w * cursor_mask_ratio)
        binary[h - mask_h : h, w - mask_w : w] = 0

    padded = cv2.copyMakeBorder(
        binary, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=0
    )
    return padded


def white_letters_frame(bgra_frame: np.ndarray, cfg: OcrConfig) -> np.ndarray:
    """The near-white letter pixels of the dialogue region, cursor masked.

    For see-through boxes (text_color "white"): scenery moving behind the box
    leaves this picture unchanged, so it can tell when the text has settled."""
    binary = letter_pixels(bgra_frame, cfg).astype(np.uint8) * 255
    return _mask_cursor_and_pad(binary, cfg.cursor_mask_ratio)


def read_ordered_text(
    reader: Any,  # easyocr.Reader, imported lazily
    processed_img: np.ndarray,
    min_word_conf: float = 0.40,
    min_avg_conf: float = 0.50,
) -> str:
    """
    Runs EasyOCR, filters out low-confidence map-tile hallucinations, and
    orders valid words top-to-bottom, left-to-right.
    """
    detections = reader.readtext(
        processed_img, detail=1, paragraph=False, width_ths=0.9
    )
    if not detections:
        return ""

    boxes = []
    confidences = []
    for bbox, text, prob in detections:
        cleaned = text.strip()
        # Drop low-confidence tile hallucinations immediately
        if not cleaned or prob < min_word_conf:
            continue

        xs = [pt[0] for pt in bbox]
        ys = [pt[1] for pt in bbox]
        y_min, y_max = min(ys), max(ys)
        boxes.append(
            {
                "text": cleaned,
                "x_min": min(xs),
                "y_center": (y_min + y_max) / 2.0,
                "height": max(1.0, y_max - y_min),
            }
        )
        confidences.append(prob)

    if not boxes:
        return ""

    # Require average confidence across the box to meet threshold
    if (sum(confidences) / len(confidences)) < min_avg_conf:
        return ""

    boxes.sort(key=lambda b: b["y_center"])

    lines: list[list[dict[str, Any]]] = []
    for box in boxes:
        if not lines:
            lines.append([box])
            continue

        current_line = lines[-1]
        avg_y = sum(b["y_center"] for b in current_line) / len(current_line)
        avg_h = sum(b["height"] for b in current_line) / len(current_line)

        if abs(box["y_center"] - avg_y) <= (avg_h * 0.65):
            current_line.append(box)
        else:
            lines.append([box])

    ordered_lines: list[str] = []
    for line in lines:
        line.sort(key=lambda b: b["x_min"])
        ordered_lines.append(" ".join(b["text"] for b in line))

    return "\n".join(ordered_lines)


class OcrWatcher:
    """Watches the dialogue region of the bound game window and emits SPEAK events
    once text settles. It only ever sees frames handed out by its FrameSource."""

    def __init__(
        self,
        ocr_config: OcrConfig,
        source: FrameSource,
        glyph_reader: GlyphReader | None = None,
        languages: list[str] | None = None,
        gpu: bool = True,
        poll_interval_ms: int = 80,
        debug: bool = False,
        status: StatusSink = console_status,
    ):
        if ocr_config.region is None:
            raise ValueError("Profile has no ocr.region. Run `retrobard calibrate` first.")
        self.config = ocr_config
        self.source = source
        self.poll_interval_s = poll_interval_ms / 1000.0
        self.debug = debug
        self.status = status
        # With a taught font the game's letters are matched directly; the generic
        # OCR model (and its heavy dependencies) is only loaded as a fallback.
        self.glyph_reader = glyph_reader
        self.reader = None
        self._last_unknown_hint = 0.0
        if glyph_reader is None:
            import easyocr

            self.reader = easyocr.Reader(languages or ["en"], gpu=gpu, verbose=False)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, emit: Callable[[DialogueEvent], None]) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, args=(emit,), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    @property
    def _see_through_box(self) -> bool:
        """Letters over a see-through box (scenery shows between them): settling
        and box detection go by the white letter pixels instead of brightness."""
        return self.glyph_reader is not None and self.config.text_color == "white"

    @property
    def _max_ink(self) -> float:
        # Above this share of lit pixels the region shows a scene, not dialogue. A
        # see-through box is told apart by its lines of text instead (_box_present),
        # and a full box of its white letters comes close to the usual limit.
        return 0.25 if self._see_through_box else 0.12

    def _box_present(self, window_frame: np.ndarray, raw_bgra: np.ndarray) -> bool:
        if self._see_through_box:
            # The box is not mostly dark, so ask whether lines of text are showing
            return self.glyph_reader.is_text(window_frame)
        return is_dialogue_box_present(raw_bgra, self.config.binary_threshold)

    def _preprocess_dialogue(self, window_frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        raw_bgra = self.config.region.crop(window_frame)
        if self._see_through_box:
            return raw_bgra, white_letters_frame(raw_bgra, self.config)
        processed = preprocess_retro_frame(
            raw_bgra,
            upscale_factor=self.config.upscale_factor,
            binary_threshold=self.config.binary_threshold,
            cursor_mask_ratio=self.config.cursor_mask_ratio,
        )
        return raw_bgra, processed

    def _read_dialogue(self, window_frame: np.ndarray, processed: np.ndarray) -> str:
        if self.glyph_reader is None:
            return read_ordered_text(self.reader, processed)

        result = self.glyph_reader.read(window_frame)
        now = time.monotonic()
        if result.unknown and (now - self._last_unknown_hint) > 30.0:
            self._last_unknown_hint = now
            report(
                self.status,
                StatusKind.UNKNOWN_LETTERS,
                f"[OCR] {result.unknown} letter shape(s) not recognised. "
                f"Run `retrobard teach` to add them.",
                count=result.unknown,
            )
        return result.text

    def _extract_speaker_name(self, window_frame: np.ndarray) -> str | None:
        # Taught letters read "NAME:" prefixes exactly, so no separate nameplate pass
        if self.glyph_reader is not None or not self.config.speaker_region:
            return None

        raw_speaker_bgra = self.config.speaker_region.crop(window_frame)

        spk_threshold = (
            self.config.speaker_binary_threshold
            if self.config.speaker_binary_threshold is not None
            else self.config.binary_threshold
        )

        processed_spk = preprocess_retro_frame(
            raw_speaker_bgra,
            upscale_factor=self.config.upscale_factor,
            binary_threshold=spk_threshold,
            cursor_mask_ratio=0.0,
        )

        ink_ratio = np.count_nonzero(processed_spk) / processed_spk.size
        if not (0.002 <= ink_ratio <= 0.55):
            return None

        speaker_text = read_ordered_text(self.reader, processed_spk).replace("\n", " ")
        return speaker_text if speaker_text else None

    def _capture_loop(self, emit: Callable[[DialogueEvent], None]) -> None:
        try:
            prev_frame: np.ndarray | None = None
            prev_ink_ratio: float = 0.0
            stable_ms = 0.0
            box_absent_ms = 0.0
            context_is_active = False
            has_spoken_current_box = False
            window_was_available = True
            last_tick = time.monotonic()

            while not self._stop_event.is_set():
                now = time.monotonic()
                elapsed_ms = (now - last_tick) * 1000.0
                last_tick = now

                window_frame = self.source.grab()
                if window_frame is None:
                    # Game window closed or minimised: nothing is read at all.
                    if window_was_available:
                        report(
                            self.status,
                            StatusKind.WINDOW,
                            "[OCR] Game window not available - paused.",
                            available=False,
                        )
                        window_was_available = False
                    if context_is_active:
                        context_is_active = False
                        emit(DialogueEvent(event_type=EventType.CLEAR_CONTEXT, source="ocr"))
                    prev_frame = None
                    stable_ms = 0.0
                    has_spoken_current_box = False
                    self._stop_event.wait(0.5)
                    continue
                if not window_was_available:
                    report(
                        self.status,
                        StatusKind.WINDOW,
                        "[OCR] Game window found - resumed.",
                        available=True,
                        # Shown on screen only; never part of the message or the log
                        title=getattr(self.source, "current_title", lambda: None)(),
                    )
                    window_was_available = True

                raw_bgra, processed = self._preprocess_dialogue(window_frame)
                box_present = self._box_present(window_frame, raw_bgra)

                # Track when the dialogue window closes so we reset speaker memory
                if not box_present:
                    box_absent_ms += elapsed_ms
                    if context_is_active and box_absent_ms >= 1000:
                        context_is_active = False
                        emit(DialogueEvent(event_type=EventType.CLEAR_CONTEXT, source="ocr"))
                else:
                    box_absent_ms = 0.0

                white_pixel_ratio = np.count_nonzero(processed) / processed.size

                # The game window was resized: restart the stability measurement
                if prev_frame is not None and prev_frame.shape != processed.shape:
                    prev_frame = None
                    stable_ms = 0.0
                    has_spoken_current_box = False

                if prev_frame is not None:
                    diff = cv2.absdiff(processed, prev_frame)
                    changed_ratio = np.count_nonzero(diff) / diff.size
                    ink_delta = abs(white_pixel_ratio - prev_ink_ratio)

                    if changed_ratio < 0.0006 and ink_delta < 0.0003:
                        stable_ms += elapsed_ms
                    else:
                        stable_ms = 0.0
                        has_spoken_current_box = False

                    if stable_ms >= self.config.debounce_ms and not has_spoken_current_box:
                        if 0.0005 < white_pixel_ratio < self._max_ink and box_present:
                            full_text = self._read_dialogue(window_frame, processed)

                            # Re-grab to make sure the text crawl had really finished
                            verify_frame = self.source.grab()
                            if verify_frame is None:
                                continue
                            _, verify_proc = self._preprocess_dialogue(verify_frame)
                            if verify_proc.shape != processed.shape:
                                prev_frame = None
                                continue
                            verify_ink = np.count_nonzero(verify_proc) / verify_proc.size

                            if (verify_ink - white_pixel_ratio) > 0.0005:
                                if self.debug:
                                    report(
                                        self.status,
                                        StatusKind.DEBUG,
                                        f"[OCR] Discarded partial crawl "
                                        f"({white_pixel_ratio:.2%} -> {verify_ink:.2%}): {full_text!r}",
                                    )
                                stable_ms = 0.0
                                has_spoken_current_box = False
                                prev_frame = verify_proc
                                prev_ink_ratio = verify_ink
                                last_tick = time.monotonic()
                                continue

                            has_spoken_current_box = True
                            speaker_name = (
                                self._extract_speaker_name(window_frame) if full_text else None
                            )

                            if full_text:
                                context_is_active = True
                                if self.debug:
                                    report(
                                        self.status,
                                        StatusKind.DEBUG,
                                        f"[OCR] Captured (ink={white_pixel_ratio:.2%}): "
                                        f"speaker={speaker_name!r}, text={full_text!r}",
                                    )
                                emit(
                                    DialogueEvent(
                                        event_type=EventType.SPEAK,
                                        raw_text=full_text,
                                        explicit_speaker=speaker_name,
                                        source="ocr",
                                    )
                                )
                            # Don't count the OCR pass itself as "stable time"
                            last_tick = time.monotonic()
                        else:
                            has_spoken_current_box = True

                prev_frame = processed
                prev_ink_ratio = white_pixel_ratio
                self._stop_event.wait(self.poll_interval_s)
        except Exception:  # noqa: BLE001 - reported to the user instead of killing the thread
            report(
                self.status,
                StatusKind.ERROR,
                "[OCR ERROR] Reading stopped because of an unexpected error:\n"
                + traceback.format_exc(),
            )
