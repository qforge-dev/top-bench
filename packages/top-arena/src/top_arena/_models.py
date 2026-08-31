# Copyright (c) 2026 Top Arena contributors

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

type PositionMatrix = tuple[tuple[float, ...], ...]
type ModelOutput = Path | str
type ReportFormat = Literal["none", "text", "agent", "json", "jsonl"]
type ModelCallback = Callable[
    [Path, PositionMatrix],
    ModelOutput | Awaitable[ModelOutput],
]


@dataclass(frozen=True, slots=True)
class BenchmarkMetadata:
    name: str
    creator: str
    unique_positions_used: int
    audio_duration_sum: float
    turns: int
    training_time: float
    description: str
    parameter_count: int
    amp_control_count: int | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    id: str
    positions: PositionMatrix
    dry_key: str
    dry_sha256: str
    download_url: str | None = None
    duration_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Final server result returned after every case has been scored."""

    run_id: str
    status: str
    total_cases: int
    completed_cases: int
    metrics: dict[str, object]


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    id: str
    status: str
    total_cases: int
    completed_cases: int
    result: BenchmarkResult | None = None


@dataclass(frozen=True, slots=True)
class PipelineOptions:
    """Concurrency, queue, polling, and timeout controls for a local run."""

    download_concurrency: int = 4
    run_concurrency: int = 1
    upload_concurrency: int = 4
    queue_capacity: int = 8
    poll_interval_seconds: float = 0.5
    completion_timeout_seconds: float = 900.0
    report_format: ReportFormat = "none"
    show_progress: bool = True
    report_min_finding_signal: float = 1.0
    report_min_evidence_signal: float = 1.0

    def __post_init__(self) -> None:
        integer_options = (
            self.download_concurrency,
            self.run_concurrency,
            self.upload_concurrency,
            self.queue_capacity,
        )
        if any(value < 1 for value in integer_options):
            msg = "pipeline concurrency and capacity values must be at least one"
            raise ValueError(msg)
        if self.poll_interval_seconds <= 0:
            msg = "poll_interval_seconds must be greater than zero"
            raise ValueError(msg)
        if self.completion_timeout_seconds <= 0:
            msg = "completion_timeout_seconds must be greater than zero"
            raise ValueError(msg)
        report_thresholds = (
            self.report_min_finding_signal,
            self.report_min_evidence_signal,
        )
        if any(not math.isfinite(value) or value < 0 for value in report_thresholds):
            msg = "report signal thresholds must be finite and non-negative"
            raise ValueError(msg)
