# Copyright (c) 2026 Top Arena contributors

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from platformdirs import user_cache_path

from top_arena._gateway import HttpBenchmarkGateway
from top_arena._models import BenchmarkMetadata, PipelineOptions
from top_arena._pipeline import BenchmarkRun

DEFAULT_SERVER_URL = "https://top-arena.labqoat.com"


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
    amp_control_count: int | None = None,
    server_url: str | None = None,
    cache_dir: str | Path | None = None,
    options: PipelineOptions | None = None,
) -> BenchmarkRun:
    """Describe a model and create a benchmark run that can be executed later.

    The returned object does not contact the server until :meth:`BenchmarkRun.run`
    or :meth:`BenchmarkRun.run_async` is called. ``server_url`` overrides both the
    public service and the ``TOP_ARENA_SERVER_URL`` environment variable.
    """
    metadata = BenchmarkMetadata(
        name=name,
        creator=creator,
        unique_positions_used=unique_positions_used,
        audio_duration_sum=audio_duration_sum,
        turns=turns,
        training_time=training_time,
        description=description,
        parameter_count=parameter_count,
        amp_control_count=amp_control_count,
    )
    resolved_cache_dir = (
        user_cache_path("top-arena", ensure_exists=True)
        if cache_dir is None
        else Path(cache_dir).expanduser()
    )
    return BenchmarkRun(
        gateway=HttpBenchmarkGateway(
            server_url or os.environ.get("TOP_ARENA_SERVER_URL", DEFAULT_SERVER_URL)
        ),
        metadata=metadata,
        cache_dir=resolved_cache_dir,
        options=options,
    )


def update_metadata(
    run_id: str,
    *,
    name: str | None = None,
    creator: str | None = None,
    amp_control_count: int | None = None,
    unique_positions_used: int | None = None,
    audio_duration_sum: float | None = None,
    turns: int | None = None,
    training_time: float | None = None,
    description: str | None = None,
    parameter_count: int | None = None,
    server_url: str | None = None,
) -> None:
    """Correct metadata for an existing run without changing its audio scores."""
    try:
        _ = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(
            update_metadata_async(
                run_id,
                name=name,
                creator=creator,
                amp_control_count=amp_control_count,
                unique_positions_used=unique_positions_used,
                audio_duration_sum=audio_duration_sum,
                turns=turns,
                training_time=training_time,
                description=description,
                parameter_count=parameter_count,
                server_url=server_url,
            )
        )
        return
    msg = (
        "benchmark.update_metadata() cannot be used from an active event loop; "
        "use update_metadata_async()"
    )
    raise RuntimeError(msg)


async def update_metadata_async(
    run_id: str,
    *,
    name: str | None = None,
    creator: str | None = None,
    amp_control_count: int | None = None,
    unique_positions_used: int | None = None,
    audio_duration_sum: float | None = None,
    turns: int | None = None,
    training_time: float | None = None,
    description: str | None = None,
    parameter_count: int | None = None,
    server_url: str | None = None,
) -> None:
    """Asynchronously correct metadata for an existing run."""
    updates: dict[str, object] = {
        field: value
        for field, value in {
            "name": name,
            "creator": creator,
            "amp_control_count": amp_control_count,
            "unique_positions_used": unique_positions_used,
            "audio_duration_sum": audio_duration_sum,
            "turns": turns,
            "training_time": training_time,
            "description": description,
            "parameter_count": parameter_count,
        }.items()
        if value is not None
    }
    if not updates:
        msg = "at least one metadata field must be supplied"
        raise ValueError(msg)
    gateway = HttpBenchmarkGateway(
        server_url or os.environ.get("TOP_ARENA_SERVER_URL", DEFAULT_SERVER_URL)
    )
    try:
        await gateway.update_run_metadata(run_id, updates)
    finally:
        await gateway.aclose()
