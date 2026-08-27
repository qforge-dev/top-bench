# ruff: noqa: INP001

from __future__ import annotations

import io
import json

from top_arena._models import BenchmarkResult, RunSnapshot
from top_arena._reporting import ConsoleReporter


def _result() -> BenchmarkResult:
    return BenchmarkResult(
        run_id="run-123",
        status="completed",
        total_cases=3,
        completed_cases=3,
        metrics={
            "esr": {"mean": 0.1, "p90": 0.2, "worst": 0.3},
            "correlation": {"mean": 0.98, "p90": 0.99, "worst": 0.95},
            "diagnostics": {
                "coverage": {"diagnostic_cases": 3, "total_cases": 3},
                "paired_nam": {
                    "esr": {
                        "label": "ESR",
                        "cases": 3,
                        "candidate_better_cases": 2,
                        "median_candidate_improvement_percent": 12.5,
                        "cluster_bootstrap_95_percent": [2.0, 20.0],
                    }
                },
                "signed": {},
                "tone_bands": [],
                "phases": {},
                "error_concentration": {
                    "cases_for_50_percent": 1,
                    "top_5_share": 1.0,
                },
                "speed": {
                    "status": "acceptable",
                    "cases": 3,
                    "target_met_cases": 0,
                    "acceptable_cases": 3,
                    "mean_realtime_x": 20.0,
                    "slowest_realtime_x": 18.0,
                },
                "findings": {
                    "strengths": [],
                    "significant": [
                        {
                            "title": "Presence mismatch",
                            "signal_strength": 2.0,
                            "signal_definition": "test finding signal",
                            "signal_components": [
                                {
                                    "label": "absolute error",
                                    "value": 1.5,
                                    "threshold": 0.75,
                                    "unit": "dB",
                                    "normalized": 2.0,
                                }
                            ],
                            "signal_combination": "single normalized component",
                            "evidence": "+1.2 dB from 2-4 kHz in 3/3 cases.",
                            "scope": "3 cases",
                            "confidence": "descriptive",
                            "condition_patterns": [
                                {
                                    "summary": (
                                        "Across 5 tested control settings, absolute presence "
                                        "reference error increases as gain increases "
                                        "(Spearman rho +0.90)."
                                    ),
                                    "basis": "one median per distinct control setting",
                                    "caveat": "controls may covary",
                                    "signal_strength": 1.8,
                                }
                            ],
                            "cases": [
                                {
                                    "case_id": "case-a",
                                    "input_chunk": 1,
                                    "control_setting_id": 2,
                                    "controls": {"gain": 0.8},
                                    "signal_strength": 1.5,
                                    "top_regions": [
                                        {
                                            "start_seconds": 0.4,
                                            "stop_seconds": 0.5,
                                            "selected_band": "presence",
                                        }
                                    ],
                                },
                                {"case_id": "case-b", "signal_strength": 0.5},
                            ],
                        },
                        {
                            "title": "Second experiment",
                            "signal_strength": 0.5,
                            "evidence": "Separate evidence.",
                        },
                    ],
                },
            },
        },
    )


def test_agent_report_emits_one_dot_per_newly_scored_case_and_complete_evidence() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    reporter = ConsoleReporter(
        "agent",
        show_progress=True,
        min_finding_signal=1.0,
        min_evidence_signal=1.0,
        stdout=stdout,
        stderr=stderr,
    )
    reporter.start("model", "amp")
    reporter.update(RunSnapshot("run-123", "running", 3, 1))
    reporter.update(RunSnapshot("run-123", "running", 3, 1))
    reporter.update(RunSnapshot("run-123", "completed", 3, 3, _result()))
    reporter.finish(_result())

    assert "Scoring ... 3/3" in stderr.getvalue()
    report = stdout.getvalue()
    assert "lower in 2/3 cases" in report
    assert "Presence mismatch" in report
    assert "Controls: gain=0.80 (case locator: setting 2)" in report
    assert "case-a | input chunk 1 | 0.40-0.50s; strongest presence window" in report
    assert "absolute presence reference error increases as gain increases" in report
    assert "1 case at or above 1x signal" in report
    assert "Signal: 2.00x default threshold" in report
    assert "absolute error: 1.50 dB / 0.75 dB = 2.00x" in report
    assert "case-b" not in report
    assert "Second experiment" not in report
    assert "signal >= 1x; strongest first" in report
    assert "SIGNIFICANT FINDINGS" in report
    assert "Next step:" not in report
    assert "NAM-FULL target 31x, acceptable floor 15.5x" in report
    assert "[ACCEPTABLE]" in report
    assert "Lost" not in report


def test_json_report_is_only_the_result_object() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    reporter = ConsoleReporter("json", show_progress=True, stdout=stdout, stderr=stderr)
    reporter.start("model", "amp")
    reporter.update(RunSnapshot("run-123", "completed", 3, 3, _result()))
    reporter.finish(_result())

    assert json.loads(stdout.getvalue())["run_id"] == "run-123"
    assert "... 3/3" in stderr.getvalue()


def test_jsonl_report_uses_machine_readable_progress_and_result_events() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    reporter = ConsoleReporter("jsonl", show_progress=True, stdout=stdout, stderr=stderr)
    reporter.start("model", "amp")
    reporter.update(RunSnapshot("run-123", "running", 3, 2))
    reporter.update(RunSnapshot("run-123", "completed", 3, 3, _result()))
    reporter.finish(_result())

    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [event["type"] for event in events] == [
        "run_started",
        "progress",
        "progress",
        "result",
    ]
    assert stderr.getvalue() == ""
