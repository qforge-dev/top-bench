# Copyright (c) 2026 Top Arena contributors

from __future__ import annotations

from pathlib import Path

from platformdirs import user_cache_path

from top_arena._gateway import HttpBenchmarkGateway
from top_arena._models import BenchmarkMetadata, PipelineOptions
from top_arena._pipeline import BenchmarkRun

DEFAULT_SERVER_URL = "http://localhost:8000"


def create(
    *,
    name: str,
    creator: str,
    unique_positions_used: int,
    audio_duration_sum: float,
    turns: int,
    training_time: float,
    description: str,
    parameter_count: int,
    server_url: str = DEFAULT_SERVER_URL,
    cache_dir: str | Path | None = None,
    options: PipelineOptions | None = None,
) -> BenchmarkRun:
    metadata = BenchmarkMetadata(
        name=name,
        creator=creator,
        unique_positions_used=unique_positions_used,
        audio_duration_sum=audio_duration_sum,
        turns=turns,
        training_time=training_time,
        description=description,
        parameter_count=parameter_count,
    )
    resolved_cache_dir = (
        user_cache_path("top-arena", ensure_exists=True)
        if cache_dir is None
        else Path(cache_dir).expanduser()
    )
    return BenchmarkRun(
        gateway=HttpBenchmarkGateway(server_url),
        metadata=metadata,
        cache_dir=resolved_cache_dir,
        options=options,
    )
