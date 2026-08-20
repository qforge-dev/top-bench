from __future__ import annotations

import numpy as np
from top_arena_server.metrics import calculate_metrics


def test_identical_audio_has_zero_error() -> None:
    reference = np.linspace(-0.5, 0.5, 48_000, dtype=np.float32)

    metrics = calculate_metrics(reference, reference.copy(), sample_rate=48_000)

    assert metrics.esr == 0.0
    assert metrics.human_weighted_esr == 0.0
    assert metrics.mrstft == 0.0


def test_metrics_increase_for_a_different_signal() -> None:
    time = np.arange(48_000, dtype=np.float32) / 48_000
    reference = np.sin(2 * np.pi * 220 * time).astype(np.float32)
    candidate = np.sin(2 * np.pi * 440 * time).astype(np.float32)

    metrics = calculate_metrics(reference, candidate, sample_rate=48_000)

    assert metrics.esr > 0.5
    assert metrics.human_weighted_esr > 0.5
    assert metrics.mrstft > 0.1


def test_missing_candidate_tail_is_scored_as_silence() -> None:
    reference = np.ones(48_000, dtype=np.float32)
    candidate = np.ones(24_000, dtype=np.float32)

    metrics = calculate_metrics(reference, candidate, sample_rate=48_000)

    assert metrics.esr == 0.5
