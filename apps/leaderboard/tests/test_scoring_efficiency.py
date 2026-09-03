from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import select
from top_arena_server.app import create_app
from top_arena_server.config import Settings
from top_arena_server.models import BenchmarkCase, RunCase
from top_arena_server.scoring import ScoringService
from top_arena_server.seed import seed_sample_dataset


async def test_nine_concurrent_runs_share_cached_nam_analysis(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.wav"
    samples = np.arange(9_600, dtype=np.float32)
    sf.write(source, 0.1 * np.sin(2 * np.pi * 220 * samples / 48_000), 48_000)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
        score_worker_count=2,
    )
    app = create_app(settings)
    candidate_calculation_count = 0
    baseline_calculation_count = 0
    calculate_candidate = ScoringService._metrics_and_diagnostics_from_audio  # noqa: SLF001
    calculate_baseline = ScoringService._metrics_from_audio  # noqa: SLF001

    def count_candidate_calculation(*args, **kwargs):
        nonlocal candidate_calculation_count
        candidate_calculation_count += 1
        return calculate_candidate(*args, **kwargs)

    def count_baseline_calculation(*args, **kwargs):
        nonlocal baseline_calculation_count
        baseline_calculation_count += 1
        return calculate_baseline(*args, **kwargs)

    monkeypatch.setattr(
        ScoringService,
        "_metrics_and_diagnostics_from_audio",
        staticmethod(count_candidate_calculation),
    )
    monkeypatch.setattr(
        ScoringService,
        "_metrics_from_audio",
        staticmethod(count_baseline_calculation),
    )

    async with app.router.lifespan_context(app):
        await seed_sample_dataset(
            settings,
            source=source,
            amp_id="cache-amp",
            amp_name="Cache Amp",
            amp_type="guitar",
            chunk_count=1,
            chunk_seconds=0.1,
            positions=(((0.1,),), ((0.9,),)),
        )
        async with app.state.services.database.session() as session:
            benchmark_cases = (await session.scalars(select(BenchmarkCase))).all()
            for benchmark_case in benchmark_cases:
                benchmark_case.nam_reference_wet_key = benchmark_case.reference_wet_key

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            manifest = (await client.get("/api/v1/amps/cache-amp/manifest")).json()

            async def complete_run(name: str) -> str:
                created = await client.post(
                    "/api/v1/runs",
                    json={
                        "amp_id": "cache-amp",
                        "name": name,
                        "creator": "tests",
                        "training_positions": [[0.1] * 6, [0.9] * 6],
                        "training_dry_files": ["training-a.wav", "training-b.wav"],
                        "audio_duration_sum": 0.2,
                        "turns": 1,
                        "training_time": 1.0,
                        "description": "NAM cache test",
                        "parameter_count": 1,
                    },
                )
                created.raise_for_status()
                run_id = created.json()["id"]
                for case in manifest["cases"]:
                    dry = await client.get(case["download_url"])
                    dry.raise_for_status()
                    uploaded = await client.put(
                        f"/api/v1/runs/{run_id}/cases/{case['id']}/audio",
                        params={"realtime_x": 1.0},
                        content=dry.content,
                    )
                    uploaded.raise_for_status()
                (await client.post(f"/api/v1/runs/{run_id}/finish")).raise_for_status()
                for _ in range(500):
                    snapshot = await client.get(f"/api/v1/runs/{run_id}")
                    snapshot.raise_for_status()
                    if snapshot.json()["status"] == "completed":
                        return run_id
                    await asyncio.sleep(0.01)
                pytest.fail("run did not complete")

            run_ids = await asyncio.gather(
                *(complete_run(f"cache-concurrent-{index}") for index in range(9))
            )
            selected_run_id = run_ids[-1]

            assert candidate_calculation_count == 18
            assert baseline_calculation_count == 2
            detail = await client.get(
                f"/api/v1/runs/{selected_run_id}/cases/{manifest['cases'][0]['id']}/detail"
            )
            detail.raise_for_status()
            assert detail.json()["analysis"]["nam_points"]

        async with app.state.services.database.session() as session:
            benchmark_cases = (await session.scalars(select(BenchmarkCase))).all()
            selected_run_cases = (
                await session.scalars(select(RunCase).where(RunCase.run_id == selected_run_id))
            ).all()
            assert all(case.nam_cache_signature for case in benchmark_cases)
            assert all(case.nam_metrics_cache for case in benchmark_cases)
            assert all("nam_points" not in run_case.analysis for run_case in selected_run_cases)
