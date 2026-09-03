from __future__ import annotations

import io
import math
from typing import Any, cast

import numpy as np
import pytest
import soundfile as sf
from top_arena_server.diagnostics import aggregate_diagnostics, calculate_case_diagnostics
from top_arena_server.metrics import calculate_metrics
from top_arena_server.models import Amp, BenchmarkCase, RunCase
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

    calibrated = aggregate_metrics(rows, nam_a2_realtime_x=2.0)
    assert calibrated["nam_a2_speed_ratio"]["mean"] == pytest.approx(7 / 6)
    assert calibrated["nam_a2_speed_ratio"]["best"] == 2.0
    assert calibrated["nam_a2_speed_ratio"]["worst"] == 0.5
    assert calibrated["diagnostics"]["speed"]["nam_a2_realtime_x"] == 2.0
    assert calibrated["diagnostics"]["speed"]["mean_nam_a2_speed_ratio"] == pytest.approx(7 / 6)

    metrics = aggregate_metrics(rows)
    assert metrics["contract"]["version"] == "top-arena-audio-v3"
    assert metrics["contract"]["comparisons"]["nam_a2_full"] == (
        "NAM-A2-FULL baseline vs latency-aligned BIAS X reference"
    )
    assert metrics["contract"]["analysis"] == {
        "version": "top-arena-case-analysis-v1",
        "window_seconds": 0.1,
        "hop_seconds": 0.1,
        "dbfs_floor": -120.0,
    }
    assert metrics["contract"]["diagnostics"]["run_version"] == ("top-arena-run-diagnostics-v7")
    assert metrics["level_db"]["best"] == 1.0
    assert metrics["level_db"]["worst"] == 4.0
    assert metrics["peak_db"]["best"] == 2.0
    assert metrics["peak_db"]["worst"] == 8.0
    assert metrics["correlation"]["best"] == 1.0
    assert metrics["correlation"]["worst"] == 0.25


def test_training_coverage_relates_setting_esr_to_nearest_training_position() -> None:
    amp = Amp(
        id="coverage-amp",
        name="Coverage Amp",
        amp_type="guitar",
        control_names=["gain", "tone", "fixed_switch"],
    )
    rows = []
    for position_index, value in enumerate((0.0, 0.25, 0.5, 0.75, 1.0)):
        for chunk_index, addition in enumerate((0.1, 0.2)):
            benchmark_case = BenchmarkCase(
                id=f"coverage-{chunk_index}-{position_index}",
                amp=amp,
                amp_id=amp.id,
                chunk_index=chunk_index,
                position_index=position_index,
                position_matrix=[[value, value, 0.0]],
                dry_key=f"dry-{chunk_index}.wav",
                dry_sha256=f"hash-{chunk_index}",
                reference_wet_key="reference.wav",
                duration_seconds=1.0,
            )
            rows.append(
                RunCase(
                    run_id="run",
                    benchmark_case_id=benchmark_case.id,
                    benchmark_case=benchmark_case,
                    status="completed",
                    esr=value + addition,
                    analysis={},
                )
            )

    metrics = aggregate_metrics(rows, training_positions=((0.0, 0.0),))
    coverage = metrics["diagnostics"]["training_coverage"]

    assert coverage["version"] == "top-arena-training-coverage-v1"
    assert coverage["available"] is True
    assert coverage["training_position_count"] == 1
    assert coverage["training_control_count"] == 2
    assert coverage["analyzed_settings"] == 5
    assert coverage["distance_definition"]["control_projection"] == (
        "first 2 controls in amp-control order"
    )
    correlation = coverage["esr_distance_correlation"]
    assert correlation["settings"] == 5
    assert correlation["spearman_rho"] == pytest.approx(1.0)
    assert correlation["pearson_r"] == pytest.approx(1.0)
    highest = coverage["highest_esr_positions"][0]
    assert highest["control_setting_id"] == 5
    assert highest["controls"] == {"gain": 1.0, "tone": 1.0, "fixed_switch": 0.0}
    assert highest["case_count"] == 2
    assert highest["mean_esr"] == pytest.approx(1.15)
    assert highest["nearest_training_distance"] == pytest.approx(1.0)
    assert highest["nearest_training_points"] == [
        {
            "measured_step": 1,
            "training_position_id": 1,
            "distance": 1.0,
            "training_position": [0.0, 0.0],
            "controls": {"gain": 0.0, "tone": 0.0},
        }
    ]


def test_case_diagnostics_preserve_the_sign_and_frequency_of_a_tonal_difference() -> None:
    sample_rate = 48_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    bass = 0.1 * np.sin(2 * np.pi * 100 * time)
    presence = 0.1 * np.sin(2 * np.pi * 3_000 * time)
    reference = bass + presence
    candidate = bass + 2.0 * presence

    diagnostics = calculate_case_diagnostics(
        reference,
        reference,
        candidate,
        sample_rate=sample_rate,
    )

    bands = {band["id"]: band for band in diagnostics["bands"]}
    assert bands["presence"]["signed_delta_db"] == pytest.approx(6.0206, abs=0.05)
    assert bands["bass"]["signed_delta_db"] == pytest.approx(0.0, abs=0.05)
    assert diagnostics["signed"]["level_delta_db"] > 3.0
    assert diagnostics["top_regions"][0]["dominant_error_band"] == "presence"
    assert diagnostics["band_regions"]["presence"]["selected_band_delta_db"] == pytest.approx(
        6.0206, abs=0.05
    )
    assert diagnostics["band_regions"]["presence"]["selected_band"] == "presence"


def test_tonal_finding_explains_control_settings_and_setting_level_pattern() -> None:
    amp = Amp(
        id="amp",
        name="Amp",
        amp_type="guitar",
        control_names=["volume", "bright"],
    )
    rows = []
    for index, volume in enumerate((0.1, 0.3, 0.5, 0.7, 0.9)):
        delta = -(index + 1.0)
        benchmark_case = BenchmarkCase(
            id=f"case-{index + 1}",
            amp=amp,
            amp_id=amp.id,
            chunk_index=0,
            position_index=index,
            position_matrix=[[volume, 1.0]],
            dry_key="dry.wav",
            dry_sha256="hash",
            reference_wet_key="reference.wav",
            duration_seconds=1.0,
        )
        rows.append(
            RunCase(
                run_id="run",
                benchmark_case_id=benchmark_case.id,
                benchmark_case=benchmark_case,
                status="completed",
                esr=0.1,
                analysis={
                    "diagnostics": {
                        "signed": {},
                        "bands": [
                            {
                                "id": "upper_mids",
                                "signed_delta_db": delta,
                                "error_energy_share": 0.5,
                            }
                        ],
                        "onsets": {},
                        "phases": {},
                        "band_regions": {
                            "upper_mids": {
                                "start_seconds": 0.1,
                                "stop_seconds": 0.2,
                                "selected_band": "upper_mids",
                                "selected_band_delta_db": delta,
                            }
                        },
                        "input": {},
                    }
                },
            )
        )

    diagnostics = aggregate_diagnostics(rows)
    finding = next(
        item
        for item in diagnostics["findings"]["significant"]
        if item["title"].endswith("Upper mids")
    )

    assert finding["evidence"].endswith("candidate is lower in 5/5 cases.")
    assert finding["condition_patterns"][0]["control"] == "volume"
    assert finding["condition_patterns"][0]["spearman_rho"] == pytest.approx(1.0)
    assert finding["condition_patterns"][0]["settings"] == 5
    assert finding["cases"][0]["input_chunk"] == 1
    assert finding["cases"][0]["control_setting_id"] == 5
    assert finding["cases"][0]["controls"] == {"volume": 0.9, "bright": 1.0}
    assert "control_sequence" not in finding["cases"][0]
    assert "dry_loop" not in finding["cases"][0]
    assert "position" not in finding["cases"][0]
    assert "action" not in finding


def test_run_diagnostics_report_paired_baseline_without_duplicate_loss_count() -> None:
    samples = np.arange(4_800, dtype=np.float64) / 48_000
    reference = np.sin(2 * np.pi * 1_000 * samples)
    candidate = 0.8 * reference
    case_diagnostics = calculate_case_diagnostics(
        reference,
        reference,
        candidate,
        sample_rate=48_000,
    )
    row = RunCase(
        run_id="run",
        benchmark_case_id="case-a",
        status="completed",
        esr=0.04,
        human_weighted_esr=0.04,
        mrstft=0.2,
        level_db=1.9,
        peak_db=1.9,
        correlation=1.0,
        nam_esr=0.08,
        nam_human_weighted_esr=0.08,
        nam_mrstft=0.3,
        analysis={"diagnostics": case_diagnostics},
    )

    diagnostics = aggregate_diagnostics([row])

    paired = diagnostics["paired_nam"]["esr"]
    assert paired["candidate_better_cases"] == 1
    assert paired["cases"] == 1
    assert paired["median_candidate_improvement_percent"] == pytest.approx(50.0)
    assert "candidate_worse_cases" not in paired
    assert diagnostics["error_concentration"]["cases_for_50_percent"] == 1
    assert diagnostics["findings"]["strengths"][0]["evidence_level"] == "derived"
    assert diagnostics["version"] == "top-arena-run-diagnostics-v7"
    assert "unsupported" not in diagnostics


def test_matching_audio_does_not_generate_significant_findings() -> None:
    samples = np.arange(4_800, dtype=np.float64) / 48_000
    reference = np.sin(2 * np.pi * 1_000 * samples)
    row = RunCase(
        run_id="run",
        benchmark_case_id="matched-case",
        status="completed",
        esr=0.0,
        human_weighted_esr=0.0,
        mrstft=0.0,
        level_db=0.0,
        peak_db=0.0,
        correlation=1.0,
        analysis={
            "diagnostics": calculate_case_diagnostics(
                reference,
                reference,
                reference,
                sample_rate=48_000,
            )
        },
    )

    diagnostics = aggregate_diagnostics([row] * 15)

    assert all(
        finding["signal_strength"] < 1.0 for finding in diagnostics["findings"]["significant"]
    )
    assert diagnostics["error_concentration"]["cases_for_50_percent"] == 0


def test_speed_uses_nam_full_target_and_half_target_floor() -> None:
    def diagnostics_for(values: tuple[float, ...]) -> dict[str, Any]:
        rows = [
            RunCase(
                run_id="run",
                benchmark_case_id=f"speed-{index}",
                status="completed",
                realtime_x=value,
                analysis={},
            )
            for index, value in enumerate(values)
        ]
        return aggregate_diagnostics(rows)

    target = diagnostics_for((31.0, 40.0))
    acceptable = diagnostics_for((15.5, 20.0))
    slow = diagnostics_for((10.0, 20.0))

    assert target["speed"]["status"] == "target_met"
    assert target["findings"]["strengths"][0]["title"] == (
        "NAM-FULL speed target met on every case"
    )
    assert acceptable["speed"]["status"] == "acceptable"
    assert acceptable["findings"] == {"strengths": [], "significant": []}
    assert slow["speed"]["status"] == "below_acceptable"
    finding = slow["findings"]["significant"][0]
    assert finding["title"] == "Cases below the acceptable speed floor"
    assert finding["cases"][0]["realtime_x"] == 10.0
