# Copyright (c) 2026 Top Arena contributors

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

type PositionMatrix = tuple[tuple[float, ...], ...]
type TrainingPosition = tuple[float, ...]
type ModelOutput = Path | str
type ReportFormat = Literal["none", "text", "agent", "json", "jsonl"]
type ModelCallback = Callable[
    [Path, PositionMatrix],
    ModelOutput | Awaitable[ModelOutput],
]


@dataclass(frozen=True, slots=True)
class NamA2CalibrationAssets:
    """Pinned native NAM-A2 model and runner supplied by the benchmark server."""

    version: str
    platform: str
    runner_url: str
    runner_sha256: str
    model_url: str
    model_sha256: str
    audio_seconds: float


@dataclass(frozen=True, slots=True)
class NamA2SpeedCalibration:
    """Machine-local speed measured with NeuralAmpModelerCore."""

    version: str
    platform: str
    runner_sha256: str
    model_sha256: str
    audio_seconds: float
    elapsed_seconds: float
    realtime_x: float
    measurements_seconds: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class BenchmarkMetadata:
    name: str
    creator: str
    training_positions: tuple[TrainingPosition, ...]
    training_dry_files: tuple[str, ...]
    audio_duration_sum: float
    turns: int
    training_time: float
    description: str
    parameter_count: int
    amp_control_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "training_positions",
            normalize_training_positions(self.training_positions),
        )
        object.__setattr__(
            self,
            "training_dry_files",
            normalize_training_dry_files(self.training_dry_files),
        )
        if self.amp_control_count is not None and any(
            len(position) != self.amp_control_count for position in self.training_positions
        ):
            msg = (
                "each training position must contain exactly "
                f"{self.amp_control_count} values in amp-control order"
            )
            raise ValueError(msg)

    @property
    def unique_positions_used(self) -> int:
        """Number of exact, distinct training positions supplied by the author."""
        return len(self.training_positions)


def normalize_training_positions(
    positions: Sequence[Sequence[float]],
) -> tuple[TrainingPosition, ...]:
    normalized = tuple(tuple(float(control) for control in position) for position in positions)
    if not normalized:
        msg = "at least one training position is required"
        raise ValueError(msg)
    if any(not position for position in normalized):
        msg = "training positions cannot be empty"
        raise ValueError(msg)
    if len({len(position) for position in normalized}) > 1:
        msg = "all training positions must contain the same number of controls"
        raise ValueError(msg)
    if any(
        not math.isfinite(control) or not 0 <= control <= 1
        for position in normalized
        for control in position
    ):
        msg = "training position controls must be finite values between 0 and 1"
        raise ValueError(msg)
    if len(set(normalized)) != len(normalized):
        msg = "training positions must be unique"
        raise ValueError(msg)
    return normalized


def normalize_training_dry_files(files: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(file_name.strip() for file_name in files)
    if not normalized:
        msg = "at least one training dry-file identifier is required"
        raise ValueError(msg)
    if any(not file_name for file_name in normalized):
        msg = "training dry-file identifiers cannot be empty"
        raise ValueError(msg)
    if any(len(file_name) > 1024 for file_name in normalized):
        msg = "training dry-file identifiers cannot exceed 1024 characters"
        raise ValueError(msg)
    if len(set(normalized)) != len(normalized):
        msg = "training dry-file identifiers must be unique"
        raise ValueError(msg)
    return normalized


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
    calibrate_nam_a2_speed: bool = True
    nam_a2_calibration_cache_seconds: float = 300.0

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
        if self.nam_a2_calibration_cache_seconds < 0:
            msg = "nam_a2_calibration_cache_seconds must be non-negative"
            raise ValueError(msg)
        report_thresholds = (
            self.report_min_finding_signal,
            self.report_min_evidence_signal,
        )
        if any(not math.isfinite(value) or value < 0 for value in report_thresholds):
            msg = "report signal thresholds must be finite and non-negative"
            raise ValueError(msg)
