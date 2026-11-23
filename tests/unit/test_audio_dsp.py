# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from retrobard.adapters.audio.pygame_out import apply_voice_effect, shift_pitch_ola
from retrobard.domain.models import AudioEffect

SAMPLE_RATE = 44100


def _make_sine_pcm(
    freq_hz: float = 440.0, duration_s: float = 0.5, stereo: bool = True
) -> np.ndarray:
    """Generates a clean 16-bit PCM sine wave for DSP testing."""
    t = np.linspace(0.0, duration_s, int(SAMPLE_RATE * duration_s), endpoint=False)
    mono = (np.sin(2.0 * np.pi * freq_hz * t) * 16000.0).astype(np.int16)
    if stereo:
        return np.column_stack([mono, mono])
    return mono


def _dominant_freq_hz(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    """Computes the dominant frequency in Hz using a Hann-windowed real FFT."""
    mono = pcm[:, 0].astype(np.float32) if pcm.ndim == 2 else pcm.astype(np.float32)
    windowed = mono * np.hanning(len(mono))
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(mono), d=1.0 / sample_rate)
    return float(freqs[np.argmax(spectrum)])


class TestShiftPitchOla:
    def test_zero_shift_returns_identical_array(self) -> None:
        pcm = _make_sine_pcm(440.0)
        out = shift_pitch_ola(pcm, 0.0)
        assert np.array_equal(pcm, out)

    def test_pitch_up_one_octave_doubles_frequency_and_preserves_duration(self) -> None:
        pcm = _make_sine_pcm(440.0, duration_s=0.5, stereo=True)
        shifted = shift_pitch_ola(pcm, semitones=12.0)

        assert shifted.shape == pcm.shape
        assert shifted.dtype == np.int16

        peak_hz = _dominant_freq_hz(shifted)
        # +12 semitones on 440 Hz should land near 880 Hz (within 5% OLA tolerance)
        assert 835.0 <= peak_hz <= 925.0

    def test_pitch_down_one_octave_halves_frequency_and_supports_1d_mono(self) -> None:
        pcm_mono = _make_sine_pcm(440.0, duration_s=0.5, stereo=False)
        shifted = shift_pitch_ola(pcm_mono, semitones=-12.0)

        assert shifted.shape == pcm_mono.shape
        assert shifted.dtype == np.int16

        peak_hz = _dominant_freq_hz(shifted)
        # -12 semitones on 440 Hz should land near 220 Hz
        assert 205.0 <= peak_hz <= 235.0


class TestApplyVoiceEffect:
    def test_none_effect_is_noop(self) -> None:
        pcm = _make_sine_pcm(440.0)
        out = apply_voice_effect(pcm, AudioEffect.NONE, sample_rate=SAMPLE_RATE)
        assert np.array_equal(pcm, out)

    @pytest.mark.parametrize("effect", [AudioEffect.ROBOT, AudioEffect.MONSTER])
    def test_robot_and_monster_preserve_length_and_modify_signal(
        self, effect: AudioEffect
    ) -> None:
        pcm = _make_sine_pcm(440.0)
        out = apply_voice_effect(pcm, effect, sample_rate=SAMPLE_RATE)

        assert out.shape == pcm.shape
        assert out.dtype == np.int16
        assert not np.array_equal(pcm, out)

    def test_cave_effect_appends_echo_tail(self) -> None:
        pcm = _make_sine_pcm(440.0, duration_s=0.2, stereo=True)
        # Silence the second half of the input so we can verify echo energy in the tail
        pcm[len(pcm) // 2 :] = 0

        out = apply_voice_effect(pcm, AudioEffect.CAVE, sample_rate=SAMPLE_RATE)
        expected_tail_samples = int(0.45 * SAMPLE_RATE)

        assert out.shape == (pcm.shape[0] + expected_tail_samples, 2)
        assert out.dtype == np.int16
        # Verify the appended echo tail contains non-zero decaying audio
        assert np.max(np.abs(out[pcm.shape[0] :])) > 500