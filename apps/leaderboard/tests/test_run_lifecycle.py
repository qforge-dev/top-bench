from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from sqlalchemy import select
from top_arena_server.app import create_app
from top_arena_server.config import Settings
from top_arena_server.models import BenchmarkCase, RunCase
from top_arena_server.seed import seed_sample_dataset


async def test_uploaded_audio_is_scored_aggregated_and_visible(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    signal = (0.2 * np.sin(2 * np.pi * 220 * np.arange(48_000) / 48_000)).astype(np.float32)
    sf.write(source, signal, 48_000, subtype="FLOAT")
    database_url = os.environ.get(
        "TOP_ARENA_TEST_DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / 'arena.db'}",
    )
    settings = Settings(
        database_url=database_url,
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
        async with app.state.services.database.session() as session:
            benchmark_case = await session.scalar(
                select(BenchmarkCase).where(BenchmarkCase.amp_id == "demo-bias-x")
            )
            assert benchmark_case is not None
            benchmark_case.nam_reference_wet_key = benchmark_case.reference_wet_key
        await seed_sample_dataset(
            settings,
            source=source,
            amp_id="pg-clean",
            amp_name="PG Clean",
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

            detail_response = await client.get(f"/api/v1/runs/{run_id}/cases/{case['id']}/detail")
            detail_response.raise_for_status()
            detail = detail_response.json()
            assert detail["analysis"]["nam_points"]
            assert detail["metrics"]["nam_esr"] == 0.0
            assert detail["analysis"]["nam_points"][0]["esr"] == 0.0
            assert detail["audio"]["nam"].endswith("/audio/nam")

            retry = await client.put(
                f"/api/v1/runs/{run_id}/cases/{case['id']}/audio",
                params={"realtime_x": 99.0},
                content=dry_response.content,
                headers={"content-type": "audio/wav"},
            )
            assert retry.status_code == 409

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
            assert "/static/dashboard.js?v=20260827-amp-pages" in dashboard_response.text
            assert ">Amp params <" in dashboard_response.text
            assert (
                'data-label="Amp parameters" class="numeric-cell">6</td>' in dashboard_response.text
            )
            assert 'class="amp-link" href="/amps/demo-bias-x"' in dashboard_response.text
            assert 'class="hero"' not in dashboard_response.text
            assert 'class="hero-stats"' not in dashboard_response.text
            assert "Hear less hype" not in dashboard_response.text

            amp_page_response = await client.get("/amps/demo-bias-x")
            amp_page_response.raise_for_status()
            assert "Demo Bias-X results · Top Arena" in amp_page_response.text
            assert "lifecycle-model" in amp_page_response.text
            assert 'data-amp-id="demo-bias-x"' in amp_page_response.text
            assert "/static/amp_detail.js?v=20260827-amp-pages" in amp_page_response.text
            assert 'data-chart-mode="positions"' in amp_page_response.text
            assert 'data-chart-mode="budget"' in amp_page_response.text
            assert (await client.get("/amps/not-a-real-amp")).status_code == 404

            leaderboard_response = await client.get("/api/v1/leaderboard")
            leaderboard_response.raise_for_status()
            leaderboard = leaderboard_response.json()
            assert leaderboard["runs"][0]["cases"] == []
            assert leaderboard["runs"][0]["amp_control_count"] == 6
            assert [(amp["id"], amp["name"]) for amp in leaderboard["amps"]] == [
                ("demo-bias-x", "Demo Bias-X"),
                ("pg-clean", "PG Clean"),
            ]

            selected_amp = await client.get("/api/v1/leaderboard", params={"amp_id": "demo-bias-x"})
            selected_amp.raise_for_status()
            assert [run["name"] for run in selected_amp.json()["runs"]] == ["lifecycle-model"]

            unused_amp = await client.get("/api/v1/leaderboard", params={"amp_id": "pg-clean"})
            unused_amp.raise_for_status()
            assert unused_amp.json()["runs"] == []
            assert [amp["id"] for amp in unused_amp.json()["amps"]] == [
                "demo-bias-x",
                "pg-clean",
            ]

            async with app.state.services.database.session() as session:
                candidate_key = await session.scalar(
                    select(RunCase.candidate_wet_key).where(RunCase.run_id == run_id)
                )
            assert candidate_key is not None
            assert await app.state.services.storage.exists(candidate_key)
            delete_response = await client.delete(f"/api/v1/runs/{run_id}")
            assert delete_response.status_code == 204
            assert not await app.state.services.storage.exists(candidate_key)
            assert (await client.get(f"/api/v1/runs/{run_id}")).status_code == 404
            assert "lifecycle-model" not in (await client.get("/api/v1/leaderboard")).text
