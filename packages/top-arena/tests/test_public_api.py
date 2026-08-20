# Copyright (c) 2026 Top Arena contributors
# ruff: noqa: INP001

from __future__ import annotations

from pathlib import Path

from top_arena import PipelineOptions, benchmark


def test_benchmark_create_builds_a_typed_run(tmp_path: Path) -> None:
    options = PipelineOptions(
        download_concurrency=2,
        run_concurrency=1,
        upload_concurrency=2,
    )
    run = benchmark.create(
        name="super-model-v1",
        creator="tests",
        unique_positions_used=1,
        audio_duration_sum=50.0,
        turns=1,
        training_time=5_000.0,
        description="Model description",
        parameter_count=40_000,
        server_url="https://arena.test",
        cache_dir=tmp_path,
        options=options,
    )

    assert run.metadata.name == "super-model-v1"
    assert run.metadata.parameter_count == 40_000
    assert run.cache_dir == tmp_path
