from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from typing import Any, TextIO, cast

from top_arena._models import (
    BenchmarkResult,
    NamA2SpeedCalibration,
    ReportFormat,
    RunSnapshot,
)


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _mapping(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast("dict[str, Any]", item) for item in value if isinstance(item, dict)]


def _signal_strength(item: dict[str, Any]) -> float:
    value = _number(item.get("signal_strength"))
    return value if value is not None else 1.0


def _above_signal(items: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    selected = [item for item in items if _signal_strength(item) >= threshold]
    return sorted(selected, key=_signal_strength, reverse=True)


def _metric_line(name: str, summary: dict[str, Any], *, digits: int = 4) -> str | None:
    mean = _number(summary.get("mean"))
    p90 = _number(summary.get("p90"))
    worst = _number(summary.get("worst"))
    if mean is None or p90 is None or worst is None:
        return None
    return f"  {name:<20} mean {mean:.{digits}f}  p90 {p90:.{digits}f}  worst {worst:.{digits}f}"


def _controls(value: object) -> str:
    controls = _mapping(value)
    return ", ".join(f"{name}={float(level):.2f}" for name, level in controls.items())


def _setting_description(case: dict[str, Any]) -> str:
    if control_text := _controls(case.get("controls")):
        return control_text
    sequence = _items(case.get("control_sequence"))
    return "; ".join(
        f"step {index}: {_controls(step)}" for index, step in enumerate(sequence, start=1)
    )


def _case_line(  # noqa: PLR0912
    case: dict[str, Any], *, include_setting: bool = True
) -> str:
    parts = [str(case.get("case_id", "unknown case"))]
    input_chunk = case.get("input_chunk", case.get("dry_loop"))
    if input_chunk is not None:
        parts.append(f"input chunk {input_chunk}")
    setting_description = _setting_description(case)
    setting_id = case.get("control_setting_id", case.get("position"))
    if include_setting:
        if setting_description:
            suffix = f" (setting ID {setting_id})" if setting_id is not None else ""
            parts.append(f"controls: {setting_description}{suffix}")
        elif setting_id is not None:
            parts.append(f"control setting ID {setting_id}")
    regions = _items(case.get("top_regions"))
    if regions:
        region = regions[0]
        start = _number(region.get("start_seconds"))
        stop = _number(region.get("stop_seconds"))
        if start is not None and stop is not None:
            selected_band = region.get("selected_band")
            dominant_band = region.get("dominant_error_band")
            if selected_band:
                parts.append(f"{start:.2f}-{stop:.2f}s; strongest {selected_band} window")
            elif dominant_band:
                parts.append(f"{start:.2f}-{stop:.2f}s; dominant error band {dominant_band}")
            else:
                parts.append(f"{start:.2f}-{stop:.2f}s")
    if (esr := _number(case.get("esr"))) is not None:
        parts.append(f"ESR {esr:.4f}")
    if (delta := _number(case.get("signed_delta_db"))) is not None:
        parts.append(f"signed band delta {delta:+.2f} dB")
    if (realtime := _number(case.get("realtime_x"))) is not None:
        parts.append(f"speed {realtime:.2f}x realtime")
    if (signal_strength := _number(case.get("signal_strength"))) is not None:
        parts.append(f"signal {signal_strength:.2f}x threshold")
    return " | ".join(parts)


def _setting_key(case: dict[str, Any]) -> str | None:
    if not (case.get("controls") or case.get("control_sequence")):
        return None
    return json.dumps(
        {
            "controls": case.get("controls"),
            "control_sequence": case.get("control_sequence"),
        },
        sort_keys=True,
    )


def _range(values: list[float], *, digits: int, signed: bool = False) -> str:
    low, high = min(values), max(values)
    specifier = f"{'+' if signed else ''}.{digits}f"
    if abs(high - low) < 10 ** (-(digits + 1)):
        return format(low, specifier)
    return f"{format(low, specifier)} to {format(high, specifier)}"


def _setting_evidence_lines(
    selected_cases: list[dict[str, Any]],
    all_cases: list[dict[str, Any]],
) -> list[str]:
    selected_groups: dict[str, list[dict[str, Any]]] = {}
    all_groups: dict[str, list[dict[str, Any]]] = {}
    ungrouped: list[dict[str, Any]] = []
    for case in all_cases:
        if (key := _setting_key(case)) is not None:
            all_groups.setdefault(key, []).append(case)
    for case in selected_cases:
        if (key := _setting_key(case)) is None:
            ungrouped.append(case)
        else:
            selected_groups.setdefault(key, []).append(case)

    ordered_groups = sorted(
        selected_groups.items(),
        key=lambda item: max(_signal_strength(case) for case in item[1]),
        reverse=True,
    )
    lines: list[str] = []
    for key, unordered_cases in ordered_groups:
        grouped_cases = sorted(unordered_cases, key=_signal_strength, reverse=True)
        setting_ids = sorted(
            {
                int(setting_id)
                for case in grouped_cases
                if (setting_id := case.get("control_setting_id", case.get("position"))) is not None
            }
        )
        id_text = (
            f" (case locator: setting {setting_ids[0]})"
            if len(setting_ids) == 1
            else (
                f" (case locators: settings {', '.join(map(str, setting_ids))})"
                if setting_ids
                else ""
            )
        )
        lines.append(f"     - Controls: {_setting_description(grouped_cases[0])}{id_text}")

        chunks = sorted(
            {
                int(chunk)
                for case in grouped_cases
                if (chunk := case.get("input_chunk", case.get("dry_loop"))) is not None
            }
        )
        total = len(all_groups.get(key, grouped_cases))
        summary_parts = [f"{len(grouped_cases)}/{total} tested cases above the evidence threshold"]
        if chunks:
            summary_parts.append(f"input chunks {', '.join(map(str, chunks))}")
        deltas = [
            value
            for case in grouped_cases
            if (value := _number(case.get("signed_delta_db"))) is not None
        ]
        if deltas:
            summary_parts.append(f"signed band error {_range(deltas, digits=2, signed=True)} dB")
        esr_values = [
            value for case in grouped_cases if (value := _number(case.get("esr"))) is not None
        ]
        if esr_values:
            summary_parts.append(f"ESR {_range(esr_values, digits=4)}")
        speed_values = [
            value
            for case in grouped_cases
            if (value := _number(case.get("realtime_x"))) is not None
        ]
        if speed_values:
            summary_parts.append(f"speed {_range(speed_values, digits=2)}x realtime")
        lines.append(f"       {'; '.join(summary_parts)}.")
        lines.extend(
            f"       · {_case_line(case, include_setting=False)}" for case in grouped_cases
        )

    lines.extend(f"     - {_case_line(case)}" for case in ungrouped)
    return lines


def _finding_lines(  # noqa: PLR0912
    finding: dict[str, Any],
    *,
    numbered: int | None = None,
    evidence_signal_threshold: float,
) -> list[str]:
    prefix = f"{numbered}." if numbered is not None else "+"
    lines = [f"{prefix} {finding.get('title', 'Finding')}"]
    signal_components = _items(finding.get("signal_components"))
    if (signal_strength := _number(finding.get("signal_strength"))) is not None:
        definition = finding.get("signal_definition") if not signal_components else None
        suffix = f" — {definition}" if definition else ""
        lines.append(f"   Signal: {signal_strength:.2f}x default threshold{suffix}")
    if signal_components:
        lines.append("   Why reported:")
        for component in signal_components:
            value = _number(component.get("value"))
            threshold = _number(component.get("threshold"))
            normalized = _number(component.get("normalized"))
            if value is None or threshold is None or normalized is None:
                continue
            unit = str(component.get("unit", ""))
            digits = 0 if unit == "%" else 2
            separator = "" if unit == "%" or not unit else " "
            lines.append(
                f"     - {component.get('label')}: "
                f"{value:.{digits}f}{separator}{unit} / "
                f"{threshold:.{digits}f}{separator}{unit} "
                f"= {normalized:.2f}x"
            )
        if finding.get("signal_combination") and signal_strength is not None:
            lines.append(
                f"     - Combined: {finding['signal_combination']} = {signal_strength:.2f}x"
            )
    for label, key in (
        ("Evidence", "evidence"),
        ("Interpretation", "interpretation"),
        ("Scope", "scope"),
    ):
        value = finding.get(key)
        if value:
            lines.append(f"   {label}: {value}")
    basis = finding.get("basis", finding.get("confidence"))
    if basis:
        lines.append(f"   Basis: {basis}")
    patterns = _above_signal(_items(finding.get("condition_patterns")), 1.0)
    if patterns:
        lines.append("   Control-setting pattern:")
        for pattern in patterns:
            lines.append(f"     - {pattern.get('summary')}")
            if pattern.get("basis"):
                lines.append(f"       Basis: {pattern['basis']}.")
            if pattern.get("caveat"):
                lines.append(f"       Limit: {pattern['caveat']}.")
    cases = _above_signal(_items(finding.get("cases")), evidence_signal_threshold)
    if cases:
        case_label = "case" if len(cases) == 1 else "cases"
        lines.append(
            "   Strongest affected settings and exact supporting regions "
            f"({len(cases)} {case_label} at or above {evidence_signal_threshold:g}x signal):"
        )
        lines.extend(_setting_evidence_lines(cases, _items(finding.get("cases"))))
    return lines


def _agent_report(  # noqa: PLR0912
    result: BenchmarkResult,
    elapsed: float,
    *,
    finding_signal_threshold: float,
    evidence_signal_threshold: float,
) -> str:
    metrics = _mapping(result.metrics)
    diagnostics = _mapping(metrics.get("diagnostics"))
    findings = _mapping(diagnostics.get("findings"))
    coverage = _mapping(diagnostics.get("coverage"))
    lines = [
        f"COMPLETED  {result.completed_cases}/{result.total_cases} cases scored in {elapsed:.1f}s",
        f"Run   {result.run_id}",
        "",
        "FIT  (error metrics are lower-is-better; correlation is higher-is-better)",
    ]
    for key, label, digits in (
        ("esr", "ESR", 4),
        ("human_weighted_esr", "Human-weighted ESR", 4),
        ("mrstft", "MRSTFT", 4),
        ("level_db", "Level delta (dB)", 2),
        ("peak_db", "Peak delta (dB)", 2),
        ("correlation", "Correlation", 4),
    ):
        line = _metric_line(label, _mapping(metrics.get(key)), digits=digits)
        if line is not None:
            lines.append(line)

    speed = _mapping(diagnostics.get("speed"))
    if speed:
        status_labels = {
            "target_met": "TARGET MET",
            "acceptable": "ACCEPTABLE",
            "below_acceptable": "BELOW ACCEPTABLE",
        }
        mean_ratio = _number(speed.get("mean_nam_a2_speed_ratio"))
        slowest_ratio = _number(speed.get("slowest_nam_a2_speed_ratio"))
        if mean_ratio is not None and slowest_ratio is not None:
            title = (
                "SPEED  (candidate/native NAM-A2 on this machine; higher is better; "
                "1.0x matches NAM-A2)"
            )
            summary = (
                f"  {mean_ratio:.2f}x NAM-A2 mean  {slowest_ratio:.2f}x slowest  "
                f"{float(speed['mean_realtime_x']):.2f}x absolute realtime mean  "
                f"{speed['target_met_cases']}/{speed['cases']} match NAM-A2  "
                f"[{status_labels.get(str(speed.get('status')), 'UNCLASSIFIED')}]"
            )
        else:
            title = "SPEED  (higher is better; NAM-FULL target 31x, acceptable floor 15.5x)"
            summary = (
                f"  {float(speed['mean_realtime_x']):.2f}x mean  "
                f"{float(speed['slowest_realtime_x']):.2f}x slowest  "
                f"{speed['target_met_cases']}/{speed['cases']} meet target  "
                f"{speed['acceptable_cases']}/{speed['cases']} meet acceptable floor  "
                f"[{status_labels.get(str(speed.get('status')), 'UNCLASSIFIED')}]"
            )
        lines.extend(["", title, summary])

    paired = _mapping(diagnostics.get("paired_nam"))
    if paired:
        lines.extend(["", "PAIRED BASELINE  (same cases; candidate versus NAM-A2-FULL)"])
        for comparison in paired.values():
            item = _mapping(comparison)
            cases = int(item.get("cases", 0))
            wins = int(item.get("candidate_better_cases", 0))
            improvement = _number(item.get("median_candidate_improvement_percent"))
            if improvement is None:
                continue
            interval = item.get("cluster_bootstrap_95_percent")
            interval_text = ""
            if isinstance(interval, list) and len(interval) == 2:
                low, high = float(interval[0]), float(interval[1])
                interval_text = f"; input-chunk-clustered 95% [{low:+.1f}%, {high:+.1f}%]"
            lines.append(
                f"  {item.get('label')}: {improvement:+.1f}% median improvement; "
                f"lower in {wins}/{cases} cases{interval_text}"
            )

    strengths = _items(findings.get("strengths"))
    significant = _items(findings.get("significant"))
    if not significant:
        significant = _items(findings.get("priorities"))
    if not significant:
        significant = _items(findings.get("suggestions"))
    if strengths:
        lines.extend(["", "STRENGTHS"])
        for finding in strengths:
            lines.extend(
                _finding_lines(
                    finding,
                    evidence_signal_threshold=evidence_signal_threshold,
                )
            )
    selected_findings = _above_signal(significant, finding_signal_threshold)
    if selected_findings:
        lines.extend(
            [
                "",
                (
                    "SIGNIFICANT FINDINGS  "
                    f"(signal >= {finding_signal_threshold:g}x; strongest first)"
                ),
            ]
        )
        for index, finding in enumerate(selected_findings, start=1):
            lines.extend(
                _finding_lines(
                    finding,
                    numbered=index,
                    evidence_signal_threshold=evidence_signal_threshold,
                )
            )

    signed = _mapping(diagnostics.get("signed"))
    bands = _items(diagnostics.get("tone_bands"))
    phases = _mapping(diagnostics.get("phases"))
    concentration = _mapping(diagnostics.get("error_concentration"))
    lines.extend(["", "MEASURED SIGNATURE"])
    for key, label in (
        ("level_delta_db", "Signed level"),
        ("peak_delta_db", "Signed peak"),
        ("crest_delta_db", "Crest factor"),
    ):
        item = _mapping(signed.get(key))
        median = _number(item.get("median"))
        p10 = _number(item.get("p10"))
        p90 = _number(item.get("p90"))
        if median is not None and p10 is not None and p90 is not None:
            lines.append(f"  {label:<16} {median:+.2f} median [{p10:+.2f}, {p90:+.2f}] P10-P90 dB")
    if bands:
        lines.append("  Tone (candidate/reference energy; signed dB):")
        for band in bands:
            median = _number(band.get("median"))
            p10 = _number(band.get("p10"))
            p90 = _number(band.get("p90"))
            if median is None or p10 is None or p90 is None:
                continue
            hz = cast("list[float]", band.get("hz", [0, 0]))
            cases = int(band.get("cases", 0))
            positive_cases = int(band.get("positive_cases", 0))
            if median >= 0:
                direction = "higher"
                directional_cases = positive_cases
            else:
                direction = "lower"
                directional_cases = cases - positive_cases
            lines.append(
                f"    {band.get('name')!s:<13} {hz[0]:g}-{hz[1]:g} Hz  "
                f"{median:+.2f} [{p10:+.2f}, {p90:+.2f}]  "
                f"{direction} in {directional_cases}/{cases} cases"
            )
    attack = _mapping(phases.get("attack_time_delta_ms"))
    if (attack_median := _number(attack.get("median"))) is not None:
        lines.append(
            f"  Attack 20%-90%  {attack_median:+.2f} ms median over "
            f"{phases.get('attack_time_events', 0)} dry-anchored events"
        )
    if concentration:
        lines.append(
            "  ESR concentration "
            f"{concentration.get('cases_for_50_percent', 0)} cases carry 50% of summed case ESR; "
            f"top five {100 * float(concentration.get('top_5_share', 0.0)):.1f}%"
        )

    diagnostic_cases = int(coverage.get("diagnostic_cases", 0))
    lines.extend(
        [
            "",
            "READING RULES",
            "  Signed dB is candidate relative to the BIAS X reference: + means more, - less.",
            "  The BIAS X reference is the target; 0 signed error means an energy match.",
            "  Control patterns use one outcome per distinct setting; repeated chunks do not",
            "  count as additional settings, and correlated controls are not claimed as causes.",
            (
                f"  Diagnostic coverage: {diagnostic_cases}/{result.total_cases} cases. "
                "Full machine-readable evidence is in result.metrics.diagnostics."
            ),
        ]
    )
    return "\n".join(lines)


def _text_report(
    result: BenchmarkResult, elapsed: float, *, finding_signal_threshold: float
) -> str:
    metrics = _mapping(result.metrics)
    diagnostics = _mapping(metrics.get("diagnostics"))
    findings = _mapping(diagnostics.get("findings"))
    lines = [
        f"COMPLETED {result.completed_cases}/{result.total_cases} cases in {elapsed:.1f}s",
        f"run: {result.run_id}",
    ]
    for key, label in (("esr", "ESR"), ("human_weighted_esr", "HW-ESR"), ("mrstft", "MRSTFT")):
        summary = _mapping(metrics.get(key))
        mean = _number(summary.get("mean"))
        worst = _number(summary.get("worst"))
        if mean is not None and worst is not None:
            lines.append(f"{label}: mean {mean:.4f}, worst {worst:.4f}")
    speed = _mapping(diagnostics.get("speed"))
    if speed:
        mean_ratio = _number(speed.get("mean_nam_a2_speed_ratio"))
        slowest_ratio = _number(speed.get("slowest_nam_a2_speed_ratio"))
        if mean_ratio is not None and slowest_ratio is not None:
            lines.append(
                f"speed: {mean_ratio:.2f}x NAM-A2 mean, {slowest_ratio:.2f}x slowest; "
                f"absolute mean {float(speed['mean_realtime_x']):.2f}x realtime; "
                f"status {speed['status']}"
            )
        else:
            lines.append(
                f"speed: {float(speed['mean_realtime_x']):.2f}x mean, "
                f"{float(speed['slowest_realtime_x']):.2f}x slowest; "
                f"status {speed['status']} against legacy 31x target / 15.5x floor"
            )
    significant = _items(findings.get("significant"))
    if not significant:
        significant = _items(findings.get("priorities"))
    if not significant:
        significant = _items(findings.get("suggestions"))
    selected_findings = _above_signal(significant, finding_signal_threshold)
    if selected_findings:
        lines.append("significant findings:")
        lines.extend(
            f"  {index}. {item.get('title')}: {item.get('evidence')}"
            for index, item in enumerate(selected_findings, start=1)
        )
    return "\n".join(lines)


class ConsoleReporter:
    """Append-only scored-case progress plus one final report."""

    def __init__(
        self,
        report_format: ReportFormat,
        *,
        show_progress: bool,
        min_finding_signal: float = 1.0,
        min_evidence_signal: float = 1.0,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self._format = report_format
        self._show_progress = show_progress and report_format != "none"
        self._min_finding_signal = min_finding_signal
        self._min_evidence_signal = min_evidence_signal
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self._completed = 0
        self._total = 0
        self._started_at = 0.0
        self._line_open = False

    @property
    def enabled(self) -> bool:
        return self._format != "none"

    def start(
        self,
        model_name: str,
        amp_id: str,
        *,
        calibration: NamA2SpeedCalibration | None = None,
    ) -> None:
        self._started_at = time.monotonic()
        if self._format == "jsonl":
            payload: dict[str, object] = {
                "type": "run_started",
                "model": model_name,
                "amp_id": amp_id,
            }
            if calibration is not None:
                payload["nam_a2_calibration_realtime_x"] = calibration.realtime_x
                payload["nam_a2_calibration_platform"] = calibration.platform
            self._json_line(payload)
        elif self._show_progress:
            print(f"Top Arena  {model_name} -> {amp_id}", file=self._stderr, flush=True)
            if calibration is not None:
                print(
                    f"Local native NAM-A2 baseline  {calibration.realtime_x:.2f}x realtime",
                    file=self._stderr,
                    flush=True,
                )
            print("Scoring ", end="", file=self._stderr, flush=True)
            self._line_open = True

    def update(self, snapshot: RunSnapshot) -> None:
        self._total = snapshot.total_cases
        previous = self._completed
        completed = max(self._completed, min(snapshot.completed_cases, snapshot.total_cases))
        added = completed - self._completed
        self._completed = completed
        if self._format == "jsonl" and added:
            self._json_line(
                {
                    "type": "progress",
                    "run_id": snapshot.id,
                    "status": snapshot.status,
                    "completed_cases": completed,
                    "total_cases": snapshot.total_cases,
                }
            )
        elif self._show_progress and added:
            for case_number in range(previous + 1, completed + 1):
                print(".", end="", file=self._stderr)
                if case_number % 50 == 0 and case_number < snapshot.total_cases:
                    print(
                        f" {case_number}/{snapshot.total_cases}\n        ",
                        end="",
                        file=self._stderr,
                    )
            self._stderr.flush()

    def finish(self, result: BenchmarkResult) -> None:
        elapsed = max(0.0, time.monotonic() - self._started_at)
        if self._line_open:
            missing = max(0, result.completed_cases - self._completed)
            if missing:
                print("." * missing, end="", file=self._stderr)
            print(
                f" {result.completed_cases}/{result.total_cases}  {elapsed:.1f}s",
                file=self._stderr,
                flush=True,
            )
            self._line_open = False
        if self._format == "agent":
            print(
                _agent_report(
                    result,
                    elapsed,
                    finding_signal_threshold=self._min_finding_signal,
                    evidence_signal_threshold=self._min_evidence_signal,
                ),
                file=self._stdout,
                flush=True,
            )
        elif self._format == "text":
            print(
                _text_report(
                    result,
                    elapsed,
                    finding_signal_threshold=self._min_finding_signal,
                ),
                file=self._stdout,
                flush=True,
            )
        elif self._format == "json":
            print(
                json.dumps(asdict(result), indent=2, sort_keys=True),
                file=self._stdout,
                flush=True,
            )
        elif self._format == "jsonl":
            self._json_line({"type": "result", **asdict(result)})

    def fail(self, error: BaseException) -> None:
        if self._line_open:
            print(" FAIL", file=self._stderr, flush=True)
            self._line_open = False
        if self._format == "jsonl":
            self._json_line({"type": "run_failed", "error": str(error)})

    def _json_line(self, value: dict[str, Any]) -> None:
        print(json.dumps(value, sort_keys=True), file=self._stdout, flush=True)
