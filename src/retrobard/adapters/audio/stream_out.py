# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plays 16-bit mono PCM as it arrives, so speech can start before the clip is complete."""

import ctypes
import os
import threading

import numpy as np
import pygame
from pygame._sdl2 import audio as sdl_audio

from retrobard.adapters.audio.pygame_out import apply_voice_effect, shift_pitch_ola
from retrobard.domain.models import AudioEffect

BYTES_PER_SAMPLE = 2


def _default_output_device() -> str:
    """Name of the system's default output device (pygame only opens devices by name)."""
    names = sdl_audio.get_audio_device_names(False)
    if not names:
        raise RuntimeError("No audio output device found.")
    try:
        sdl = ctypes.CDLL(os.path.join(os.path.dirname(pygame.__file__), "SDL2.dll"))
        sdl.SDL_GetDefaultAudioInfo.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        sdl.SDL_GetDefaultAudioInfo.restype = ctypes.c_int
        sdl.SDL_free.argtypes = [ctypes.c_void_p]
        name_ptr = ctypes.c_void_p()
        spec = ctypes.create_string_buffer(64)  # SDL_AudioSpec, contents unused
        if sdl.SDL_GetDefaultAudioInfo(ctypes.byref(name_ptr), spec, 0) == 0 and name_ptr.value:
            name = ctypes.cast(name_ptr, ctypes.c_char_p).value.decode("utf-8")
            sdl.SDL_free(name_ptr)
            if name in names:
                return name
    except (OSError, AttributeError):
        pass  # other platforms / older SDL: fall back to the first listed device
    return names[0]


class PcmStreamPlayer:
    """Audio player for raw PCM (signed 16-bit little-endian, mono).

    Chunks handed to `feed` are played back-to-back with no gaps. `stop` silences
    playback immediately and makes any chunk still arriving for that clip be ignored.
    """

    def __init__(self, sample_rate: int = 24000):
        self.sample_rate = sample_rate
        if not pygame.mixer.get_init():
            pygame.mixer.init()  # brings up SDL's audio subsystem

        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._stream_id = 0
        self._device = sdl_audio.AudioDevice(
            _default_output_device(),
            False,
            sample_rate,
            sdl_audio.AUDIO_S16,
            1,
            512,
            0,
            self._fill,
        )
        self._device.pause(0)

    def _fill(self, _device, out) -> None:
        """Called by the audio thread whenever it needs the next few milliseconds."""
        wanted = len(out)
        with self._lock:
            # Only whole samples: a network chunk may end in the middle of one
            take = min(wanted, len(self._buffer) // BYTES_PER_SAMPLE * BYTES_PER_SAMPLE)
            data = bytes(self._buffer[:take])
            del self._buffer[:take]
        out[:take] = data
        if take < wanted:
            out[take:] = bytes(wanted - take)  # silence

    def begin_stream(self) -> int:
        """Starts a new clip and returns its id, to be passed to `feed`."""
        with self._lock:
            self._stream_id += 1
            self._buffer.clear()
            return self._stream_id

    def feed(self, stream_id: int, chunk: bytes) -> None:
        with self._lock:
            if stream_id == self._stream_id:
                self._buffer += chunk

    def play_bytes(
        self,
        audio_data: bytes,
        pitch_semitones: float = 0.0,
        effect: AudioEffect = AudioEffect.NONE,
    ) -> None:
        if not audio_data:
            return
        if abs(pitch_semitones) >= 0.05 or effect != AudioEffect.NONE:
            usable = len(audio_data) // BYTES_PER_SAMPLE * BYTES_PER_SAMPLE
            pcm = np.frombuffer(audio_data[:usable], dtype="<i2")
            if abs(pitch_semitones) >= 0.05:
                pcm = shift_pitch_ola(pcm, pitch_semitones)
            if effect != AudioEffect.NONE:
                pcm = apply_voice_effect(pcm, effect, sample_rate=self.sample_rate)
            audio_data = pcm.astype("<i2").tobytes()
        self.feed(self.begin_stream(), audio_data)

    def stop(self) -> None:
        with self._lock:
            self._stream_id += 1  # late chunks of the stopped clip are dropped
            self._buffer.clear()

    def is_busy(self) -> bool:
        with self._lock:
            return len(self._buffer) >= BYTES_PER_SAMPLE

    def seconds_buffered(self) -> float:
        with self._lock:
            return len(self._buffer) / BYTES_PER_SAMPLE / self.sample_rate

    def close(self) -> None:
        self._device.pause(1)
        self._device.close()
