# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
# src/retrobard/adapters/audio/pygame_out.py
import io
import wave

import numpy as np
import pygame

from retrobard.domain.models import AudioEffect


def shift_pitch_ola(pcm: np.ndarray, semitones: float) -> np.ndarray:
    """
    Shifts pitch by `semitones` without altering playback duration: time-stretches
    with waveform-similarity overlap-add (WSOLA), then resamples back to the
    original length.

    Each grain is taken from the spot near its ideal position that best lines up
    with the audio already written. Plain OLA skips that alignment, which smears
    the waveform and lands on the wrong pitch.
    """
    if abs(semitones) < 0.05:
        return pcm

    is_1d = pcm.ndim == 1
    samples = pcm[:, np.newaxis] if is_1d else pcm
    n_samples, n_channels = samples.shape

    pitch_factor = 2.0 ** (semitones / 12.0)
    win_size = 1024
    hop_s = win_size // 2
    hop_a = hop_s / pitch_factor  # fractional on purpose: keeps the stretch ratio exact
    search = win_size // 2  # +/- samples searched for the best-aligned grain

    if n_samples <= win_size:
        return pcm

    hann = np.hanning(win_size).astype(np.float32)[:, np.newaxis]
    n_frames = max(1, int((n_samples - win_size) / hop_a) + 1)
    stretched_len = (n_frames - 1) * hop_s + win_size

    stretched = np.zeros((stretched_len, n_channels), dtype=np.float32)
    norm = np.zeros((stretched_len, 1), dtype=np.float32)

    # Zero-pad so the search window never runs off either end
    src_float = np.zeros((n_samples + 2 * search + win_size, n_channels), dtype=np.float32)
    src_float[search : search + n_samples] = samples
    mono = src_float.mean(axis=1)

    prev_pos = search
    for i in range(n_frames):
        target = search + int(round(i * hop_a))  # noqa: RUF046 - hop_a may be a numpy float
        if i == 0:
            pos = target
        else:
            # What would naturally follow the previous grain in the source...
            template = mono[prev_pos + hop_s : prev_pos + hop_s + win_size]
            # ...and where, near the ideal position, the source matches it best
            region = mono[target - search : target + search + win_size]
            pos = target - search + int(np.argmax(np.correlate(region, template, "valid")))

        out_start = i * hop_s
        stretched[out_start : out_start + win_size] += src_float[pos : pos + win_size] * hann
        norm[out_start : out_start + win_size] += hann
        prev_pos = pos

    norm[norm < 1e-6] = 1.0
    stretched /= norm

    src_idx = np.linspace(0, stretched_len - 1, n_samples, dtype=np.float32)
    idx_floor = np.floor(src_idx).astype(np.int32)
    idx_ceil = np.minimum(idx_floor + 1, stretched_len - 1)
    frac = (src_idx - idx_floor)[:, np.newaxis]

    resampled = stretched[idx_floor] * (1.0 - frac) + stretched[idx_ceil] * frac
    out = np.clip(resampled, -32768, 32767).astype(np.int16)
    return out[:, 0] if is_1d else out


def apply_voice_effect(
    pcm: np.ndarray, effect: AudioEffect, sample_rate: int = 44100
) -> np.ndarray:
    """Applies real-time NumPy DSP voice effects (robot, monster, cave)."""
    if effect == AudioEffect.NONE:
        return pcm

    is_1d = pcm.ndim == 1
    samples = pcm[:, np.newaxis] if is_1d else pcm
    n_samples, n_channels = samples.shape

    # Normalize to [-1.0, 1.0] float32 for clean DSP math
    sig = samples.astype(np.float32) / 32768.0
    t = np.arange(n_samples, dtype=np.float32)[:, np.newaxis] / sample_rate

    if effect == AudioEffect.ROBOT:
        # 1. 55 Hz Ring Modulation (55% dry + 45% carrier keeps speech intelligible)
        carrier = np.sin(2.0 * np.pi * 55.0 * t)
        modulated = sig * (0.55 + 0.45 * carrier)

        # 2. 6.5 ms Metallic Comb Filter (simulates chassis resonance)
        comb_delay = int(0.0065 * sample_rate)
        out = modulated.copy()
        if n_samples > comb_delay:
            out[comb_delay:] += 0.35 * modulated[:-comb_delay]
        out *= 0.85

    elif effect == AudioEffect.MONSTER:
        # 1. 28 Hz Sub-Growl amplitude modulation
        growl = 0.78 + 0.22 * np.sin(2.0 * np.pi * 28.0 * t)
        out = sig * growl

        # 2. 14 ms Dual-Throat Doubler (demonic chorus)
        double_delay = int(0.014 * sample_rate)
        if n_samples > double_delay:
            out[double_delay:] += 0.40 * sig[:-double_delay]

        # 3. Soft-clipping tanh overdrive for gravelly harmonic grit
        drive = 1.65
        out = np.tanh(out * drive) / np.tanh(drive)

    elif effect == AudioEffect.CAVE:
        # Pad buffer by 450 ms so the final word's echo tail isn't cut off
        tail_samples = int(0.45 * sample_rate)
        out = np.zeros((n_samples + tail_samples, n_channels), dtype=np.float32)
        out[:n_samples] = sig

        # 4 prime-spaced decaying echo taps: (delay_seconds, decay_gain)
        taps = [
            (0.095, 0.40),
            (0.185, 0.25),
            (0.280, 0.15),
            (0.385, 0.08),
        ]
        for delay_s, gain in taps:
            d_idx = int(delay_s * sample_rate)
            if d_idx < out.shape[0]:
                end_idx = min(d_idx + n_samples, out.shape[0])
                out[d_idx:end_idx] += gain * sig[: end_idx - d_idx]

        out *= 0.82
    else:
        return pcm

    pcm_out = np.clip(out * 32767.0, -32768, 32767).astype(np.int16)
    return pcm_out[:, 0] if is_1d else pcm_out


class PygameAudioPlayer:
    def __init__(self) -> None:
        pygame.mixer.init(frequency=44100, size=-16, channels=2)
        self._active_stream: io.BytesIO | None = None

    def play_bytes(
        self,
        audio_data: bytes,
        pitch_semitones: float = 0.0,
        effect: AudioEffect = AudioEffect.NONE,
    ) -> None:
        if not audio_data:
            return
        self.stop()

        # Fast-path: no local DSP needed -> stream original MP3 directly
        if abs(pitch_semitones) < 0.05 and effect == AudioEffect.NONE:
            self._active_stream = io.BytesIO(audio_data)
            pygame.mixer.music.load(self._active_stream)
            pygame.mixer.music.play()
            return

        # Decode MP3 to PCM, run Pitch Shift + Voice Effect in NumPy, and wrap as WAV
        freq, _fmt, _channels = pygame.mixer.get_init()
        sound = pygame.mixer.Sound(io.BytesIO(audio_data))
        pcm_array = pygame.sndarray.array(sound)

        if abs(pitch_semitones) >= 0.05:
            pcm_array = shift_pitch_ola(pcm_array, pitch_semitones)

        if effect != AudioEffect.NONE:
            pcm_array = apply_voice_effect(pcm_array, effect, sample_rate=freq)

        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            wf.setnchannels(1 if pcm_array.ndim == 1 else pcm_array.shape[1])
            wf.setsampwidth(2)
            wf.setframerate(freq)
            wf.writeframes(pcm_array.tobytes())

        wav_io.seek(0)
        self._active_stream = wav_io
        pygame.mixer.music.load(self._active_stream, namehint="wav")
        pygame.mixer.music.play()

    def stop(self) -> None:
        if pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()

    def is_busy(self) -> bool:
        return bool(pygame.mixer.music.get_busy())