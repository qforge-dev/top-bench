from __future__ import annotations

import io
import math
from typing import cast

import numpy as np
import pytest
import soundfile as sf
from top_arena_server.metrics import calculate_metrics
from top_arena_server.models import RunCase
from top_arena_server.scoring import ScoringService, aggregate_metrics


def test_identical_audio_has_zero_error() -> None:
    reference = np.linspace(-0.5, 0.5, 48_000, dtype=np.float32)

    metrics = calculate_metrics(reference, reference.copy(), sample_rate=48_000)

    assert metrics.esr == 0.0
    assert metrics.human_weighted_esr == 0.0
    assert metrics.mrstft == 0.0
    assert metrics.level_db == 0.0
    assert metrics.peak_db == 0.0
    assert metrics.correlation == 1.0
    assert metrics.analysis["version"] == "top-arena-case-analysis-v1"
    assert metrics.analysis["window_seconds"] == 0.1
    assert metrics.analysis["hop_seconds"] == 0.1
    assert len(metrics.analysis["points"]) == 10
    assert metrics.analysis["points"][0] == {
        "time_seconds": 0.0,
        "esr": 0.0,
        "reference_level_db": pytest.approx(-6.917_733, abs=1e-5),
        "candidate_level_db": pytest.approx(-6.917_733, abs=1e-5),
        "level_delta_db": 0.0,
        "reference_peak_db": pytest.approx(-6.0206, abs=1e-5),
        "candidate_peak_db": pytest.approx(-6.0206, abs=1e-5),
        "peak_delta_db": 0.0,
        "correlation": 1.0,
    }
    assert all(
        math.isfinite(cast("float", value))
        for point in metrics.analysis["points"]
        for value in point.values()
    )


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


def test_bias_reference_latency_is_removed_before_scoring() -> None:
    latency_samples = 9
    time = np.arange(4_800, dtype=np.float32) / 48_000
    candidate = (0.5 * np.sin(2 * np.pi * 997 * time)).astype(np.float32)
    reference = np.concatenate(
        (np.zeros(latency_samples, dtype=np.float32), candidate[:-latency_samples])
    )

    def encode(samples: np.ndarray) -> bytes:
        destination = io.BytesIO()
        sf.write(destination, samples, 48_000, format="FLAC", subtype="PCM_24")
        return destination.getvalue()

    unaligned = ScoringService._metrics_from_audio(  # noqa: SLF001
        encode(reference), encode(candidate), reference_latency_samples=0
    )
    aligned = ScoringService._metrics_from_audio(  # noqa: SLF001
        encode(reference), encode(candidate), reference_latency_samples=latency_samples
    )

    assert unaligned.esr > 0.5
    assert aligned.esr == pytest.approx(0.0, abs=1e-10)


def test_level_peak_and_correlation_describe_gain_and_polarity() -> None:
    time = np.arange(48_000, dtype=np.float32) / 48_000
    reference = (0.8 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)

    quieter = calculate_metrics(reference, reference * 0.5, sample_rate=48_000)
    inverted = calculate_metrics(reference, -reference, sample_rate=48_000)

    assert quieter.level_db == pytest.approx(6.0206, abs=1e-4)
    assert quieter.peak_db == pytest.approx(6.0206, abs=1e-4)
    assert quieter.correlation == pytest.approx(1.0)
    assert inverted.level_db == pytest.approx(0.0, abs=1e-10)
    assert inverted.peak_db == pytest.approx(0.0, abs=1e-10)
    assert inverted.correlation == pytest.approx(-1.0)


def test_analysis_uses_100ms_windows_and_includes_the_final_partial_window() -> None:
    sample_rate = 4_800
    reference = np.linspace(-0.5, 0.5, 4_801, dtype=np.float32)

    metrics = calculate_metrics(reference, reference, sample_rate=sample_rate)

    assert len(metrics.analysis["points"]) == 11
    assert [point["time_seconds"] for point in metrics.analysis["points"]] == [
        pytest.approx(index / 10) for index in range(11)
    ]


@pytest.mark.parametrize("sample_count", [1, 100, 600])
def test_nonempty_short_audio_has_finite_metrics(sample_count: int) -> None:
    reference = np.linspace(-0.25, 0.25, sample_count, dtype=np.float32)

    metrics = calculate_metrics(reference, reference.copy(), sample_rate=48_000)

    assert metrics.mrstft == 0.0
    assert all(
        math.isfinite(float(value))
        for value in (
            metrics.esr,
            metrics.human_weighted_esr,
            metrics.mrstft,
            metrics.level_db,
            metrics.peak_db,
            metrics.correlation,
        )
    )


def test_silence_has_finite_floor_and_stable_correlation() -> None:
    silence = np.zeros(4_800, dtype=np.float32)
    signal = np.ones(4_800, dtype=np.float32) * 0.1

    both_silent = calculate_metrics(silence, silence, sample_rate=4_800)
    missing_signal = calculate_metrics(signal, silence, sample_rate=4_800)

    assert both_silent.level_db == 0.0
    assert both_silent.peak_db == 0.0
    assert both_silent.correlation == 1.0
    assert both_silent.analysis["points"][0]["reference_level_db"] == -120.0
    assert both_silent.analysis["points"][0]["reference_peak_db"] == -120.0
    assert missing_signal.level_db == pytest.approx(100.0)
    assert missing_signal.peak_db == pytest.approx(100.0)
    assert missing_signal.correlation == 0.0
    assert all(
        math.isfinite(cast("float", value))
        for metrics in (both_silent, missing_signal)
        for point in metrics.analysis["points"]
        for value in point.values()
    )


def test_realtime_summary_treats_more_realtime_x_as_better() -> None:
    rows = [
        RunCase(
            run_id="run",
            benchmark_case_id=f"case-{value}",
            status="completed",
            realtime_x=value,
            esr=0.1,
            human_weighted_esr=0.1,
            mrstft=0.1,
            level_db=value,
            peak_db=value * 2,
            correlation=value / 4,
            analysis={
                "version": "top-arena-case-analysis-v1",
                "window_seconds": 0.1,
                "hop_seconds": 0.1,
                "points": [],
            },
        )
        for value in (1.0, 2.0, 4.0)
    ]

    summary = aggregate_metrics(rows)["realtime_x"]

    assert summary["best"] == 4.0
    assert summary["worst"] == 1.0

    metrics = aggregate_metrics(rows)
    assert metrics["contract"]["version"] == "top-arena-audio-v2"
    assert metrics["contract"]["analysis"] == {
        "version": "top-arena-case-analysis-v1",
        "window_seconds": 0.1,
        "hop_seconds": 0.1,
        "dbfs_floor": -120.0,
    }
    assert metrics["level_db"]["best"] == 1.0
    assert metrics["level_db"]["worst"] == 4.0
    assert metrics["peak_db"]["best"] == 2.0
    assert metrics["peak_db"]["worst"] == 8.0
    assert metrics["correlation"]["best"] == 1.0
    assert metrics["correlation"]["worst"] == 0.25
