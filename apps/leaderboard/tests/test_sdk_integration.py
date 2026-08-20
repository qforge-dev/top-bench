from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf
from top_arena._gateway import HttpBenchmarkGateway
from top_arena._models import BenchmarkMetadata, PipelineOptions
from top_arena._pipeline import BenchmarkRun
from top_arena_server.app import create_app
from top_arena_server.config import Settings
from top_arena_server.seed import seed_sample_dataset


async def test_sdk_and_server_complete_a_real_http_run(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    samples = np.arange(9_600, dtype=np.float32)
    sf.write(source, 0.1 * np.sin(2 * np.pi * 220 * samples / 48_000), 48_000)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'integration.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
        score_worker_count=4,
        score_poll_interval_seconds=0.01,
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        await seed_sample_dataset(
            settings,
            source=source,
            amp_id="integration-amp",
            amp_name="Integration Amp",
            amp_type="guitar",
            chunk_count=1,
            chunk_seconds=0.1,
            positions=(((0.1,),), ((0.3,),), ((0.6,),), ((0.9,),)),
        )
        gateway = HttpBenchmarkGateway(
            "http://test",
            transport=httpx.ASGITransport(app=app),
        )
        run = BenchmarkRun(
            gateway=gateway,
            metadata=BenchmarkMetadata(
                name="sdk-server-integration",
                creator="tests",
                unique_positions_used=1,
                audio_duration_sum=0.1,
                turns=1,
                training_time=1.0,
                description="Full SDK/server contract test",
                parameter_count=1,
            ),
            cache_dir=tmp_path / "cache",
            options=PipelineOptions(
                download_concurrency=4,
                run_concurrency=4,
                upload_concurrency=4,
                poll_interval_seconds=0.01,
                completion_timeout_seconds=5.0,
            ),
        )

        result = await run.run_async("integration-amp", lambda dry, _positions: dry)

        assert result.status == "completed"
        assert result.total_cases == 4
        assert result.completed_cases == 4
        assert result.metrics["contract"]["version"] == "top-arena-audio-v2"  # type: ignore[index]

        failed_run = BenchmarkRun(
            gateway=HttpBenchmarkGateway(
                "http://test",
                transport=httpx.ASGITransport(app=app),
            ),
            metadata=BenchmarkMetadata(
                name="sdk-callback-failure",
                creator="tests",
                unique_positions_used=1,
                audio_duration_sum=0.1,
                turns=1,
                training_time=1.0,
                description="Callback failure contract test",
                parameter_count=1,
            ),
            cache_dir=tmp_path / "failed-cache",
            options=PipelineOptions(poll_interval_seconds=0.01),
        )

        def broken_model(_dry: Path, _positions: tuple[tuple[float, ...], ...]) -> Path:
            msg = "model exploded"
            raise RuntimeError(msg)

        with pytest.raises(ExceptionGroup) as raised:
            await failed_run.run_async("integration-amp", broken_model)
        assert "model exploded" in str(raised.value.exceptions[0])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            leaderboard = (await client.get("/api/v1/leaderboard")).json()
        failed_snapshot = next(
            run for run in leaderboard["runs"] if run["name"] == "sdk-callback-failure"
        )
        assert failed_snapshot["status"] == "failed"
