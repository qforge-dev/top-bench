from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from top_arena_server.app import create_app
from top_arena_server.config import Settings
from top_arena_server.seed import seed_sample_dataset


async def test_uploaded_audio_is_scored_aggregated_and_visible(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    signal = (0.2 * np.sin(2 * np.pi * 220 * np.arange(48_000) / 48_000)).astype(np.float32)
    sf.write(source, signal, 48_000, subtype="FLOAT")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'arena.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
        public_base_url="http://test",
        score_poll_interval_seconds=0.01,
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        await seed_sample_dataset(
            settings,
            source=source,
            amp_id="demo-bias-x",
            amp_name="Demo Bias-X",
            amp_type="guitar",
            chunk_count=1,
            chunk_seconds=0.1,
            positions=(((0.0, 0.0),),),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            manifest_response = await client.get("/api/v1/amps/demo-bias-x/manifest")
            manifest_response.raise_for_status()
            manifest = manifest_response.json()
            assert len(manifest["cases"]) == 1
            case = manifest["cases"][0]

            run_response = await client.post(
                "/api/v1/runs",
                json={
                    "amp_id": "demo-bias-x",
                    "name": "lifecycle-model",
                    "creator": "tests",
                    "unique_positions_used": 1,
                    "audio_duration_sum": 0.1,
                    "turns": 1,
                    "training_time": 12.0,
                    "description": "Lifecycle test",
                    "parameter_count": 40_000,
                },
            )
            run_response.raise_for_status()
            run_id = run_response.json()["id"]

            dry_response = await client.get(case["download_url"])
            dry_response.raise_for_status()
            upload_response = await client.put(
                f"/api/v1/runs/{run_id}/cases/{case['id']}/audio",
                params={"realtime_x": 12.5},
                content=dry_response.content,
                headers={"content-type": "audio/wav"},
            )
            upload_response.raise_for_status()
            finish_response = await client.post(f"/api/v1/runs/{run_id}/finish")
            finish_response.raise_for_status()

            snapshot: dict[str, object] = {}
            for _ in range(200):
                response = await client.get(f"/api/v1/runs/{run_id}")
                response.raise_for_status()
                snapshot = response.json()
                if snapshot["status"] == "completed":
                    break
                await asyncio.sleep(0.01)

            assert snapshot["status"] == "completed"
            assert snapshot["completed_cases"] == 1
            assert snapshot["metrics"]["esr"]["mean"] is not None  # type: ignore[index]
            assert snapshot["metrics"]["mrstft"]["p90"] is not None  # type: ignore[index]

            events_response = await client.get(f"/api/v1/runs/{run_id}/events")
            events_response.raise_for_status()
            kinds = {event["kind"] for event in events_response.json()["events"]}
            assert "score.completed" in kinds
            assert "run.completed" in kinds

            dashboard_response = await client.get("/")
            dashboard_response.raise_for_status()
            assert "lifecycle-model" in dashboard_response.text
            assert 'id="amp-filter"' in dashboard_response.text
            assert 'id="creator-filter"' in dashboard_response.text
            assert 'id="pareto-chart"' in dashboard_response.text
