from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy import signal

Audio = NDArray[np.float32] | NDArray[np.float64]

_EPSILON = 1e-12
_MRSTFT_RESOLUTIONS = (
    (512, 50, 240),
    (1024, 120, 600),
    (2048, 240, 1200),
)


@dataclass(frozen=True, slots=True)
class AudioMetrics:
    """The three per-case benchmark scores. Lower is better."""

    esr: float
    human_weighted_esr: float
    mrstft: float


def _mono(value: Audio) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim == 2:
        array = array.mean(axis=1)
    if array.ndim != 1:
        msg = "audio must be mono samples or samples-by-channels"
        raise ValueError(msg)
    return array


def _a_weighted_esr(
    reference: NDArray[np.float64],
    candidate: NDArray[np.float64],
    sample_rate: int,
) -> float:
    fft_samples = 1 << max(len(reference) - 1, 0).bit_length()
    frequencies = np.fft.rfftfreq(fft_samples, 1.0 / sample_rate)
    squared = frequencies * frequencies
    numerator = (12_194.0**2) * squared * squared
    denominator = (
        (squared + 20.6**2)
        * np.sqrt((squared + 107.7**2) * (squared + 737.9**2))
        * (squared + 12_194.0**2)
    )
    response = numerator / np.maximum(denominator, 1e-30)
    weighting_db = 20.0 * np.log10(np.maximum(response, 1e-12)) + 2.0
    weights = np.clip(np.power(10.0, weighting_db / 10.0), 0.1, None)
    weights /= max(float(weights.max()), _EPSILON)
    error_spectrum = np.fft.rfft(candidate - reference, n=fft_samples)
    reference_spectrum = np.fft.rfft(reference, n=fft_samples)
    error_energy = float(np.sum(weights * np.abs(error_spectrum) ** 2))
    reference_energy = float(np.sum(weights * np.abs(reference_spectrum) ** 2))
    return error_energy / max(reference_energy, _EPSILON)


def _mrstft(reference: NDArray[np.float64], candidate: NDArray[np.float64]) -> float:
    losses: list[float] = []
    for fft_size, hop_size, window_size in _MRSTFT_RESOLUTIONS:
        overlap = window_size - hop_size
        _, _, reference_stft = signal.stft(
            reference,
            window="hann",
            nperseg=window_size,
            noverlap=overlap,
            nfft=fft_size,
            boundary=None,
            padded=False,
        )
        _, _, candidate_stft = signal.stft(
            candidate,
            window="hann",
            nperseg=window_size,
            noverlap=overlap,
            nfft=fft_size,
            boundary=None,
            padded=False,
        )
        reference_magnitude = np.abs(reference_stft)
        candidate_magnitude = np.abs(candidate_stft)
        spectral_convergence = float(
            np.linalg.norm(reference_magnitude - candidate_magnitude)
            / max(float(np.linalg.norm(reference_magnitude)), _EPSILON)
        )
        log_magnitude = float(
            np.mean(np.abs(np.log(reference_magnitude + 1e-7) - np.log(candidate_magnitude + 1e-7)))
        )
        losses.append(spectral_convergence + log_magnitude)
    return float(np.mean(losses))


def calculate_metrics(reference: Audio, candidate: Audio, *, sample_rate: int) -> AudioMetrics:
    """Calculate aligned ESR, A-weighted ESR, and QLAmp-contract MRSTFT."""
    reference_mono = _mono(reference)
    candidate_mono = _mono(candidate)
    reference_samples = len(reference_mono)
    if reference_samples == 0:
        msg = "reference audio must contain at least one sample"
        raise ValueError(msg)
    if len(candidate_mono) < reference_samples:
        candidate_mono = np.pad(candidate_mono, (0, reference_samples - len(candidate_mono)))
    else:
        candidate_mono = candidate_mono[:reference_samples]
    error = candidate_mono - reference_mono
    esr = float(np.sum(error * error) / max(float(np.sum(reference_mono**2)), _EPSILON))
    return AudioMetrics(
        esr=esr,
        human_weighted_esr=_a_weighted_esr(reference_mono, candidate_mono, sample_rate),
        mrstft=_mrstft(reference_mono, candidate_mono),
    )
