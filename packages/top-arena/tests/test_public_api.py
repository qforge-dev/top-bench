# Copyright (c) 2026 Top Arena contributors
# ruff: noqa: INP001

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import pytest
from top_arena import PipelineOptions, __version__, benchmark


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
        amp_control_count=5,
        server_url="https://arena.test",
        cache_dir=tmp_path,
        options=options,
    )

    assert run.metadata.name == "super-model-v1"
    assert run.metadata.parameter_count == 40_000
    assert run.metadata.amp_control_count == 5
    assert run.cache_dir == tmp_path


def test_default_server_is_the_online_leaderboard() -> None:
    assert benchmark.DEFAULT_SERVER_URL == "https://top-arena.labqoat.com"


def test_metadata_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValueError, match="at least one metadata field"):
        benchmark.update_metadata("run-1")


async def test_async_metadata_update_uses_the_configured_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    class StubGateway:
        def __init__(self, server_url: str) -> None:
            calls.append(("init", server_url))

        async def update_run_metadata(
            self,
            run_id: str,
            updates: dict[str, object],
        ) -> None:
            calls.append(("update", run_id, updates))

        async def aclose(self) -> None:
            calls.append(("close",))

    monkeypatch.setattr(benchmark, "HttpBenchmarkGateway", StubGateway)

    await benchmark.update_metadata_async(
        "run-1",
        amp_control_count=5,
        unique_positions_used=50,
        server_url="https://arena.test",
    )

    assert calls == [
        ("init", "https://arena.test"),
        ("update", "run-1", {"amp_control_count": 5, "unique_positions_used": 50}),
        ("close",),
    ]


async def test_sync_metadata_update_rejects_an_active_event_loop() -> None:
    with pytest.raises(RuntimeError, match="active event loop"):
        benchmark.update_metadata("run-1", amp_control_count=5)


def test_installed_distribution_exposes_its_version() -> None:
    assert __version__ == version("top-arena")


def test_report_signal_thresholds_are_non_negative_and_finite() -> None:
    permissive = PipelineOptions(
        report_min_finding_signal=0.0,
        report_min_evidence_signal=0.5,
    )

    assert permissive.report_min_finding_signal == 0.0
    assert permissive.report_min_evidence_signal == 0.5
    with pytest.raises(ValueError, match="signal thresholds"):
        PipelineOptions(report_min_finding_signal=-1.0)
    with pytest.raises(ValueError, match="signal thresholds"):
        PipelineOptions(report_min_evidence_signal=float("inf"))
