from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from top_arena_server.app import create_app
from top_arena_server.config import Settings
from top_arena_server.database import Database
from top_arena_server.models import Amp, BenchmarkCase, BenchmarkRun, RunCase, RunEvent
from top_arena_server.scoring import ScoringService
from top_arena_server.storage import create_storage


async def test_scoring_queue_round_robins_between_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'fair-queue.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
        score_worker_count=1,
    )
    database = Database(settings.database_url)
    await database.initialize()
    scoring = ScoringService(database, create_storage(settings), settings)
    scored: list[str] = []
    all_scored = asyncio.Event()

    async def record_score(run_case_id: str, *, queue_wait_ms: float) -> None:
        del queue_wait_ms
        scored.append(run_case_id)
        if len(scored) == 6:
            all_scored.set()

    monkeypatch.setattr(scoring, "_score", record_score)
    for run_case_id in ("a-1", "a-2", "a-3"):
        await scoring.enqueue(run_case_id, run_id="run-a")
    for run_case_id in ("b-1", "b-2", "b-3"):
        await scoring.enqueue(run_case_id, run_id="run-b")

    await scoring.start()
    try:
        async with asyncio.timeout(1):
            await all_scored.wait()
    finally:
        await scoring.stop()
        await database.close()

    assert scored == ["a-1", "b-1", "a-2", "b-2", "a-3", "b-3"]
    assert scoring.queue_depth == 0


async def test_startup_finalizes_a_committed_finished_run(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
    )
    database = Database(settings.database_url)
    await database.initialize()
    async with database.session() as session:
        session.add(Amp(id="amp", name="Amp", amp_type="guitar", control_names=["gain"]))
        session.add(
            BenchmarkCase(
                id="case",
                amp_id="amp",
                chunk_index=0,
                position_index=0,
                position_matrix=[[0.5]],
                dry_key="dry.wav",
                dry_sha256="a" * 64,
                reference_wet_key="wet.wav",
                duration_seconds=5.0,
                sample_rate=48_000,
            )
        )
        run = BenchmarkRun(
            id="run",
            name="recover-me",
            creator="tests",
            amp_id="amp",
            unique_positions_used=1,
            audio_duration_sum=5.0,
            turns=1,
            training_time=1.0,
            description="Committed immediately before a process crash",
            parameter_count=1,
            status="finalizing",
            client_finished=True,
            total_cases=1,
            completed_cases=0,
            metrics={},
        )
        session.add(run)
        session.add(
            RunCase(
                run_id="run",
                benchmark_case_id="case",
                status="completed",
                realtime_x=2.0,
                esr=0.1,
                human_weighted_esr=0.2,
                mrstft=0.3,
            )
        )
    await database.close()

    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/api/v1/runs/run")
        response.raise_for_status()
        snapshot = response.json()

    assert snapshot["status"] == "completed"
    assert snapshot["completed_cases"] == 1
    assert snapshot["metrics"]["esr"]["mean"] == 0.1


async def test_concurrent_finalization_is_idempotent_on_sqlite(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'concurrent-finalization.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
    )
    database = Database(settings.database_url)
    await database.initialize()
    async with database.session() as session:
        session.add(Amp(id="amp", name="Amp", amp_type="guitar", control_names=["gain"]))
        session.add(
            BenchmarkCase(
                id="case",
                amp_id="amp",
                chunk_index=0,
                position_index=0,
                position_matrix=[[0.5]],
                dry_key="dry.wav",
                dry_sha256="a" * 64,
                reference_wet_key="wet.wav",
                duration_seconds=5.0,
                sample_rate=48_000,
            )
        )
        session.add(
            BenchmarkRun(
                id="run",
                name="finalize-once",
                creator="tests",
                amp_id="amp",
                unique_positions_used=1,
                audio_duration_sum=5.0,
                turns=1,
                training_time=1.0,
                description="Concurrent finalization regression",
                parameter_count=1,
                status="finalizing",
                client_finished=True,
                total_cases=1,
                completed_cases=0,
                metrics={},
            )
        )
        session.add(
            RunCase(
                run_id="run",
                benchmark_case_id="case",
                status="completed",
                realtime_x=2.0,
                esr=0.1,
                human_weighted_esr=0.2,
                mrstft=0.3,
            )
        )

    scoring = ScoringService(database, create_storage(settings), settings)
    results = await asyncio.gather(*(scoring.finalize_if_ready("run") for _ in range(4)))

    async with database.session() as session:
        run = await session.get(BenchmarkRun, "run")
        completion_events = await session.scalar(
            select(func.count(RunEvent.id)).where(
                RunEvent.run_id == "run",
                RunEvent.kind == "run.completed",
            )
        )
    await database.close()

    assert results == [True] * 4
    assert run is not None
    assert run.status == "completed"
    assert run.completed_cases == 1
    assert completion_events == 1
