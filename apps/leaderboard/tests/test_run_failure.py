from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from top_arena_server.app import create_app
from top_arena_server.config import Settings
from top_arena_server.seed import seed_sample_dataset


async def test_scoring_failure_remains_terminal_after_finish(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros(4_800, dtype=np.float32), 48_000)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
        score_poll_interval_seconds=0.01,
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        await seed_sample_dataset(
            settings,
            source=source,
            amp_id="failure-amp",
            amp_name="Failure Amp",
            amp_type="guitar",
            chunk_count=1,
            chunk_seconds=0.1,
            positions=(((0.0,),),),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            manifest = (await client.get("/api/v1/amps/failure-amp/manifest")).json()
            case_id = manifest["cases"][0]["id"]
            response = await client.post(
                "/api/v1/runs",
                json={
                    "amp_id": "failure-amp",
                    "name": "broken-output-model",
                    "creator": "tests",
                    "training_positions": [[0.0] * 6],
                    "training_dry_files": ["training.wav"],
                    "audio_duration_sum": 0.1,
                    "turns": 1,
                    "training_time": 1.0,
                    "description": "Uploads an invalid WAV",
                    "parameter_count": 1,
                },
            )
            response.raise_for_status()
            run_id = response.json()["id"]
            upload = await client.put(
                f"/api/v1/runs/{run_id}/cases/{case_id}/audio",
                params={"realtime_x": 1.0},
                content=b"not a wav",
            )
            upload.raise_for_status()

            for _ in range(100):
                snapshot = (await client.get(f"/api/v1/runs/{run_id}")).json()
                if snapshot["status"] == "failed":
                    break
                await asyncio.sleep(0.01)
            assert snapshot["status"] == "failed"

            finished = await client.post(f"/api/v1/runs/{run_id}/finish")
            finished.raise_for_status()
            assert finished.json()["status"] == "failed"


async def test_client_failure_event_marks_the_run_failed(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros(4_800, dtype=np.float32), 48_000)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'client-failure.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        await seed_sample_dataset(
            settings,
            source=source,
            amp_id="client-failure-amp",
            amp_name="Client Failure Amp",
            amp_type="guitar",
            chunk_count=1,
            chunk_seconds=0.1,
            positions=(((0.0,),),),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/v1/runs",
                json={
                    "amp_id": "client-failure-amp",
                    "name": "callback-crashed",
                    "creator": "tests",
                    "training_positions": [[0.0] * 6],
                    "training_dry_files": ["training.wav"],
                    "audio_duration_sum": 0.1,
                    "turns": 1,
                    "training_time": 1.0,
                    "description": "Callback failure",
                    "parameter_count": 1,
                },
            )
            created.raise_for_status()
            run_id = created.json()["id"]

            event = await client.post(
                f"/api/v1/runs/{run_id}/events",
                json={"kind": "run.client_failed", "payload": {"error": "model exploded"}},
            )
            event.raise_for_status()

            snapshot = await client.get(f"/api/v1/runs/{run_id}")
            snapshot.raise_for_status()
            assert snapshot.json()["status"] == "failed"


async def test_batched_client_failure_event_marks_the_run_failed(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros(4_800, dtype=np.float32), 48_000)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'batched-client-failure.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        await seed_sample_dataset(
            settings,
            source=source,
            amp_id="batched-failure-amp",
            amp_name="Batched Failure Amp",
            amp_type="guitar",
            chunk_count=1,
            chunk_seconds=0.1,
            positions=(((0.0,),),),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/v1/runs",
                json={
                    "amp_id": "batched-failure-amp",
                    "name": "batched-callback-crashed",
                    "creator": "tests",
                    "training_positions": [[0.0] * 6],
                    "training_dry_files": ["training.wav"],
                    "audio_duration_sum": 0.1,
                    "turns": 1,
                    "training_time": 1.0,
                    "description": "Batched callback failure",
                    "parameter_count": 1,
                },
            )
            created.raise_for_status()
            run_id = created.json()["id"]

            events = await client.post(
                f"/api/v1/runs/{run_id}/events/batch",
                json={
                    "events": [
                        {"kind": "run.started", "payload": {}},
                        {
                            "kind": "run.client_failed",
                            "payload": {
                                "error": "RuntimeError: model exploded",
                                "details": {
                                    "type": "RuntimeError",
                                    "message": "model exploded",
                                },
                            },
                        },
                    ]
                },
            )
            events.raise_for_status()
            assert events.json() == {"accepted": 2}

            snapshot = await client.get(f"/api/v1/runs/{run_id}")
            snapshot.raise_for_status()
            assert snapshot.json()["status"] == "failed"
