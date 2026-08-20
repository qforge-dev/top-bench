from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

import numpy as np
from numpy.typing import NDArray
from scipy import signal

Audio = NDArray[np.float32] | NDArray[np.float64]

_EPSILON = 1e-12
_DBFS_FLOOR = -120.0
_DBFS_LINEAR_FLOOR = 10.0 ** (_DBFS_FLOOR / 20.0)
_ANALYSIS_WINDOW_SECONDS = 0.1
_MRSTFT_RESOLUTIONS = (
    (512, 50, 240),
    (1024, 120, 600),
    (2048, 240, 1200),
)


class CaseAnalysisPoint(TypedDict):
    time_seconds: float
    esr: float
    reference_level_db: float
    candidate_level_db: float
    level_delta_db: float
    reference_peak_db: float
    candidate_peak_db: float
    peak_delta_db: float
    correlation: float


class CaseAnalysis(TypedDict):
    version: str
    window_seconds: float
    hop_seconds: float
    points: list[CaseAnalysisPoint]


@dataclass(frozen=True, slots=True)
class AudioMetrics:
    """Scalar benchmark scores and a compact time-domain analysis."""

    esr: float
    human_weighted_esr: float
    mrstft: float
    level_db: float
    peak_db: float
    correlation: float
    analysis: CaseAnalysis


def _mono(value: Audio) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim == 2:
        array = array.mean(axis=1)
    if array.ndim != 1:
        msg = "audio must be mono samples or samples-by-channels"
        raise ValueError(msg)
    return np.asarray(np.nan_to_num(array, nan=0.0, posinf=1.0, neginf=-1.0), dtype=np.float64)


def _rms_dbfs(value: NDArray[np.float64]) -> float:
    rms = float(np.sqrt(np.mean(value * value)))
    return max(_DBFS_FLOOR, 20.0 * float(np.log10(max(rms, _DBFS_LINEAR_FLOOR))))


def _peak_dbfs(value: NDArray[np.float64]) -> float:
    peak = float(np.max(np.abs(value)))
    return max(_DBFS_FLOOR, 20.0 * float(np.log10(max(peak, _DBFS_LINEAR_FLOOR))))


def _correlation(reference: NDArray[np.float64], candidate: NDArray[np.float64]) -> float:
    if np.array_equal(reference, candidate):
        return 1.0
    reference_centered = reference - float(np.mean(reference))
    candidate_centered = candidate - float(np.mean(candidate))
    reference_rms = float(np.sqrt(np.mean(reference_centered * reference_centered)))
    candidate_rms = float(np.sqrt(np.mean(candidate_centered * candidate_centered)))
    if reference_rms <= _DBFS_LINEAR_FLOOR or candidate_rms <= _DBFS_LINEAR_FLOOR:
        if reference_rms <= _DBFS_LINEAR_FLOOR and candidate_rms <= _DBFS_LINEAR_FLOOR:
            difference = float(np.max(np.abs(reference - candidate)))
            return 1.0 if difference <= _DBFS_LINEAR_FLOOR else 0.0
        return 0.0
    coefficient = float(
        np.mean(reference_centered * candidate_centered) / (reference_rms * candidate_rms)
    )
    return float(np.clip(coefficient, -1.0, 1.0))


def _analysis(
    reference: NDArray[np.float64],
    candidate: NDArray[np.float64],
    *,
    sample_rate: int,
) -> CaseAnalysis:
    window_samples = max(1, round(sample_rate * _ANALYSIS_WINDOW_SECONDS))
    points: list[CaseAnalysisPoint] = []
    for start in range(0, len(reference), window_samples):
        reference_window = reference[start : start + window_samples]
        candidate_window = candidate[start : start + window_samples]
        reference_level_db = _rms_dbfs(reference_window)
        candidate_level_db = _rms_dbfs(candidate_window)
        reference_peak_db = _peak_dbfs(reference_window)
        candidate_peak_db = _peak_dbfs(candidate_window)
        error = candidate_window - reference_window
        esr = float(
            np.sum(error * error)
            / max(float(np.sum(reference_window * reference_window)), _EPSILON)
        )
        points.append(
            {
                "time_seconds": round(start / sample_rate, 6),
                "esr": esr,
                "reference_level_db": reference_level_db,
                "candidate_level_db": candidate_level_db,
                "level_delta_db": abs(candidate_level_db - reference_level_db),
                "reference_peak_db": reference_peak_db,
                "candidate_peak_db": candidate_peak_db,
                "peak_delta_db": abs(candidate_peak_db - reference_peak_db),
                "correlation": _correlation(reference_window, candidate_window),
            }
        )
    return {
        "version": "top-arena-case-analysis-v1",
        "window_seconds": _ANALYSIS_WINDOW_SECONDS,
        "hop_seconds": _ANALYSIS_WINDOW_SECONDS,
        "points": points,
    }


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
        missing_samples = max(0, window_size - len(reference))
        leading_padding = missing_samples // 2
        trailing_padding = missing_samples - leading_padding
        if missing_samples:
            reference_input = np.pad(reference, (leading_padding, trailing_padding))
            candidate_input = np.pad(candidate, (leading_padding, trailing_padding))
        else:
            reference_input = reference
            candidate_input = candidate
        overlap = window_size - hop_size
        _, _, reference_stft = signal.stft(
            reference_input,
            window="hann",
            nperseg=window_size,
            noverlap=overlap,
            nfft=fft_size,
            boundary=None,
            padded=False,
        )
        _, _, candidate_stft = signal.stft(
            candidate_input,
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
    """Calculate aligned scalar metrics and 100 ms analysis points."""
    if sample_rate <= 0:
        msg = "sample rate must be positive"
        raise ValueError(msg)
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
    reference_level_db = _rms_dbfs(reference_mono)
    candidate_level_db = _rms_dbfs(candidate_mono)
    reference_peak_db = _peak_dbfs(reference_mono)
    candidate_peak_db = _peak_dbfs(candidate_mono)
    return AudioMetrics(
        esr=esr,
        human_weighted_esr=_a_weighted_esr(reference_mono, candidate_mono, sample_rate),
        mrstft=_mrstft(reference_mono, candidate_mono),
        level_db=abs(candidate_level_db - reference_level_db),
        peak_db=abs(candidate_peak_db - reference_peak_db),
        correlation=_correlation(reference_mono, candidate_mono),
        analysis=_analysis(reference_mono, candidate_mono, sample_rate=sample_rate),
    )
