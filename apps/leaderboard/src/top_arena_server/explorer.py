from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from .diagnostics import training_coverage_diagnostics
from .models import RunCase

_MODEL_METRICS = {
    "esr": ("esr", False),
    "human_weighted_esr": ("human_weighted_esr", False),
    "mrstft": ("mrstft", False),
    "level_db": ("level_db", False),
    "peak_db": ("peak_db", False),
    "correlation": ("correlation", True),
    "realtime_x": ("realtime_x", True),
}
_NAM_METRICS = {
    "esr": ("nam_esr", False),
    "human_weighted_esr": ("nam_human_weighted_esr", False),
    "mrstft": ("nam_mrstft", False),
    "level_db": ("nam_level_db", False),
    "peak_db": ("nam_peak_db", False),
    "correlation": ("nam_correlation", True),
}


def _percentile(sorted_values: Sequence[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    index = (len(sorted_values) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(sorted_values[lower])
    weight = index - lower
    return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


def _distribution(
    rows: Sequence[RunCase],
    attribute: str,
    *,
    higher_is_better: bool,
) -> dict[str, int | float | None]:
    values = sorted(
        float(value)
        for row in rows
        if (value := getattr(row, attribute, None)) is not None and math.isfinite(float(value))
    )
    if not values:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "p90": None,
            "best": None,
            "worst": None,
        }
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "median": _percentile(values, 0.5),
        "p90": _percentile(values, 0.9),
        "best": max(values) if higher_is_better else min(values),
        "worst": min(values) if higher_is_better else max(values),
    }


def metric_distributions(
    rows: Sequence[RunCase],
    *,
    nam: bool = False,
) -> dict[str, dict[str, int | float | None]]:
    definitions = _NAM_METRICS if nam else _MODEL_METRICS
    return {
        key: _distribution(rows, attribute, higher_is_better=higher_is_better)
        for key, (attribute, higher_is_better) in definitions.items()
    }


def _case_metrics(row: RunCase) -> dict[str, float | None]:
    return {
        "realtime_x": row.realtime_x,
        "esr": row.esr,
        "human_weighted_esr": row.human_weighted_esr,
        "mrstft": row.mrstft,
        "level_db": row.level_db,
        "peak_db": row.peak_db,
        "correlation": row.correlation,
        "nam_esr": row.nam_esr,
        "nam_human_weighted_esr": row.nam_human_weighted_esr,
        "nam_mrstft": row.nam_mrstft,
        "nam_level_db": row.nam_level_db,
        "nam_peak_db": row.nam_peak_db,
        "nam_correlation": row.nam_correlation,
    }


def build_run_explorer(
    rows: Sequence[RunCase],
    *,
    run_id: str,
    training_positions: Sequence[Sequence[float]],
) -> dict[str, Any]:
    ordered_rows = sorted(
        rows,
        key=lambda row: (
            row.benchmark_case.chunk_index,
            row.benchmark_case.position_index,
            row.benchmark_case_id,
        ),
    )
    coverage = training_coverage_diagnostics(ordered_rows, training_positions)
    coverage_positions = {
        int(position["control_setting_id"]): position
        for position in coverage.get("positions", [])
        if isinstance(position, dict) and isinstance(position.get("control_setting_id"), int)
    }
    grouped: dict[int, list[RunCase]] = defaultdict(list)
    for row in ordered_rows:
        grouped[int(row.benchmark_case.position_index) + 1].append(row)

    position_metrics = {
        position_id: metric_distributions(position_rows)
        for position_id, position_rows in grouped.items()
    }
    ranked_position_ids = sorted(
        (
            position_id
            for position_id, metrics in position_metrics.items()
            if metrics["esr"]["mean"] is not None
        ),
        key=lambda position_id: float(position_metrics[position_id]["esr"]["mean"] or 0.0),
        reverse=True,
    )
    error_rank = {
        position_id: rank for rank, position_id in enumerate(ranked_position_ids, start=1)
    }

    positions: list[dict[str, Any]] = []
    for position_id, position_rows in sorted(grouped.items()):
        benchmark_case = position_rows[0].benchmark_case
        positions.append(
            {
                "position_id": position_id,
                "position_index": position_id - 1,
                "positions": benchmark_case.position_matrix,
                "control_names": benchmark_case.amp.control_names,
                "total_cases": len(position_rows),
                "completed_cases": sum(row.status == "completed" for row in position_rows),
                "esr_error_rank": error_rank.get(position_id),
                "metrics": position_metrics[position_id],
                "nam_metrics": metric_distributions(position_rows, nam=True),
                "training_coverage": coverage_positions.get(position_id, {}),
                "url": f"/runs/{run_id}/positions/{position_id}",
            }
        )

    cases = []
    for index, row in enumerate(ordered_rows, start=1):
        benchmark_case = row.benchmark_case
        position_id = int(benchmark_case.position_index) + 1
        cases.append(
            {
                "case_id": row.benchmark_case_id,
                "index": index,
                "chunk_index": benchmark_case.chunk_index,
                "position_index": benchmark_case.position_index,
                "position_id": position_id,
                "status": row.status,
                "duration_seconds": benchmark_case.duration_seconds,
                "dry_file": benchmark_case.dry_key,
                "metrics": _case_metrics(row),
                "url": f"/runs/{run_id}/cases/{row.benchmark_case_id}",
                "position_url": f"/runs/{run_id}/positions/{position_id}",
            }
        )

    return {
        "metric_distributions": metric_distributions(ordered_rows),
        "nam_metric_distributions": metric_distributions(ordered_rows, nam=True),
        "training_coverage": coverage,
        "positions": positions,
        "cases": cases,
    }
