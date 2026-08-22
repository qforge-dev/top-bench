from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from top_arena_server.app import create_app
from top_arena_server.config import Settings
from top_arena_server.database import Database
from top_arena_server.models import BenchmarkCase, BenchmarkRun, RunCase
from top_arena_server.seed import seed_sample_dataset
from top_arena_server.storage import create_storage


async def _seed_run(settings: Settings, source: Path) -> tuple[str, list[RunCase]]:
    await seed_sample_dataset(
        settings,
        source=source,
        amp_id="detail-amp",
        amp_name="Detail Amp",
        amp_type="guitar",
        chunk_count=2,
        chunk_seconds=0.1,
        positions=(((0.1, 0.2),), ((0.7, 0.8),)),
    )

    database = Database(settings.database_url)
    storage = create_storage(settings)
    try:
        async with database.session() as session:
            run = BenchmarkRun(
                amp_id="detail-amp",
                name="case-detail-model",
                creator="tests",
                unique_positions_used=2,
                audio_duration_sum=0.4,
                turns=1,
                training_time=12.0,
                description="Case detail API test",
                parameter_count=40_000,
                status="completed",
                client_finished=True,
                total_cases=4,
                completed_cases=4,
                metrics={"esr": {"mean": 0.025}},
            )
            session.add(run)
            await session.flush()
            benchmark_cases = (
                await session.scalars(
                    select(BenchmarkCase)
                    .where(BenchmarkCase.amp_id == "detail-amp")
                    .order_by(BenchmarkCase.chunk_index, BenchmarkCase.position_index)
                )
            ).all()
            run_cases: list[RunCase] = []
            for index, benchmark_case in enumerate(benchmark_cases):
                benchmark_case.nam_reference_wet_key = benchmark_case.reference_wet_key
                candidate_key = f"runs/{run.id}/candidates/{benchmark_case.id}.wav"
                reference = await storage.get(benchmark_case.reference_wet_key)
                await storage.put(candidate_key, reference)
                run_case = RunCase(
                    run_id=run.id,
                    benchmark_case_id=benchmark_case.id,
                    status="completed",
                    candidate_wet_key=candidate_key,
                    realtime_x=10.0 + index,
                    esr=0.01 + index / 100,
                    human_weighted_esr=0.02 + index / 100,
                    mrstft=0.03 + index / 100,
                    level_db=0.1 + index,
                    peak_db=0.2 + index,
                    correlation=0.99 - index / 100,
                    nam_esr=0.04 + index / 100,
                    nam_human_weighted_esr=0.05 + index / 100,
                    nam_mrstft=0.06 + index / 100,
                    nam_level_db=0.3 + index,
                    nam_peak_db=0.4 + index,
                    nam_correlation=0.97 - index / 100,
                    analysis={
                        "version": "top-arena-case-analysis-v1",
                        "window_seconds": 0.1,
                        "hop_seconds": 0.1,
                        "points": [
                            {
                                "time_seconds": 0.0,
                                "esr": 0.01 + index / 100,
                                "reference_level_db": -18.0,
                                "candidate_level_db": -17.9,
                                "level_delta_db": 0.1,
                                "reference_peak_db": -3.0,
                                "candidate_peak_db": -2.8,
                                "peak_delta_db": 0.2,
                                "correlation": 0.99 - index / 100,
                            }
                        ],
                        "nam_points": [
                            {
                                "time_seconds": 0.0,
                                "esr": 0.04 + index / 100,
                                "reference_level_db": -18.2,
                                "candidate_level_db": -17.9,
                                "level_delta_db": 0.3,
                                "reference_peak_db": -3.2,
                                "candidate_peak_db": -2.8,
                                "peak_delta_db": 0.4,
                                "correlation": 0.97 - index / 100,
                            }
                        ],
                    },
                )
                session.add(run_case)
                run_cases.append(run_case)
            run_id = run.id
        async with database.session() as session:
            persisted = (
                await session.scalars(
                    select(RunCase)
                    .join(RunCase.benchmark_case)
                    .options(joinedload(RunCase.benchmark_case))
                    .where(RunCase.run_id == run_id)
                    .order_by(BenchmarkCase.chunk_index, BenchmarkCase.position_index)
                )
            ).all()
        return run_id, list(persisted)
    finally:
        await database.close()


async def test_case_routes_are_ordered_linkable_lazy_and_navigable(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    samples = np.arange(24_000, dtype=np.float32)
    sf.write(source, 0.1 * np.sin(2 * np.pi * 220 * samples / 48_000), 48_000)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'case-detail.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
        public_base_url="http://test",
    )
    run_id, run_cases = await _seed_run(settings, source)
    app = create_app(settings)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client,
    ):
        first_id = run_cases[0].benchmark_case_id
        last_id = run_cases[-1].benchmark_case_id
        first_url = f"/runs/{run_id}/cases/{first_id}"
        last_url = f"/runs/{run_id}/cases/{last_id}"

        redirect = await client.get(f"/runs/{run_id}")
        assert redirect.status_code == 307
        assert redirect.headers["location"] == first_url

        index_response = await client.get(f"/api/v1/runs/{run_id}/case-index")
        index_response.raise_for_status()
        case_index = index_response.json()
        assert case_index["run"]["id"] == run_id
        assert case_index["run"]["cases"] == []
        assert [item["case_id"] for item in case_index["cases"]] == [
            run_case.benchmark_case_id for run_case in run_cases
        ]
        assert [item["index"] for item in case_index["cases"]] == [1, 2, 3, 4]
        assert [(item["chunk_index"], item["position_index"]) for item in case_index["cases"]] == [
            (0, 0),
            (0, 1),
            (1, 0),
            (1, 1),
        ]
        assert case_index["cases"][0]["url"] == first_url

        direct_page = await client.get(last_url)
        direct_page.raise_for_status()
        assert f'data-run-id="{run_id}"' in direct_page.text
        assert f'data-case-id="{last_id}"' in direct_page.text
        assert "case-detail-model" in direct_page.text
        assert "<title>case-detail-model · Case detail · Top Arena</title>" in direct_page.text
        assert "/static/case_detail.js?v=20260822-audio-lab-v3" in direct_page.text
        assert 'class="lab-layout"' in direct_page.text
        assert 'class="lab-sidebar"' in direct_page.text
        assert 'id="play-sequence"' in direct_page.text
        assert 'class="audition-sequence"' in direct_page.text
        assert 'id="nam-audio"' in direct_page.text
        assert 'class="breadcrumb"' not in direct_page.text
        assert "Listen side by side" not in direct_page.text
        assert "Windowed analysis" not in direct_page.text

        first_response = await client.get(f"/api/v1/runs/{run_id}/cases/{first_id}/detail")
        first_response.raise_for_status()
        first = first_response.json()
        assert first["index"] == 1
        assert first["total"] == 4
        assert first["previous_url"] is None
        assert first["next_url"] == case_index["cases"][1]["url"]
        assert first["url"] == first_url
        assert first["positions"] == [[0.1, 0.2]]
        assert first["control_names"] == [
            "volume",
            "bright",
            "bass",
            "middle",
            "treble",
            "master",
        ]
        assert first["metrics"] == {
            "realtime_x": 10.0,
            "esr": 0.01,
            "human_weighted_esr": 0.02,
            "mrstft": 0.03,
            "level_db": 0.1,
            "peak_db": 0.2,
            "correlation": 0.99,
            "nam_esr": 0.04,
            "nam_human_weighted_esr": 0.05,
            "nam_mrstft": 0.06,
            "nam_level_db": 0.3,
            "nam_peak_db": 0.4,
            "nam_correlation": 0.97,
        }
        assert first["analysis"]["version"] == "top-arena-case-analysis-v1"
        assert first["analysis"]["points"][0]["time_seconds"] == 0.0
        assert first["analysis"]["nam_points"][0]["esr"] == 0.04
        assert first["audio"] == {
            "dry": f"/api/v1/runs/{run_id}/cases/{first_id}/audio/dry",
            "reference": f"/api/v1/runs/{run_id}/cases/{first_id}/audio/reference",
            "candidate": f"/api/v1/runs/{run_id}/cases/{first_id}/audio/candidate",
            "nam": f"/api/v1/runs/{run_id}/cases/{first_id}/audio/nam",
        }

        last_response = await client.get(f"/api/v1/runs/{run_id}/cases/{last_id}/detail")
        last_response.raise_for_status()
        last = last_response.json()
        assert last["index"] == 4
        assert last["previous_url"] == case_index["cases"][-2]["url"]
        assert last["next_url"] is None

        for kind in ("dry", "reference", "candidate", "nam"):
            audio = await client.get(f"/api/v1/runs/{run_id}/cases/{first_id}/audio/{kind}")
            audio.raise_for_status()
            assert audio.headers["content-type"] == "audio/wav"
            assert audio.headers["accept-ranges"] == "bytes"
            assert audio.content.startswith(b"RIFF")

        partial_audio = await client.get(
            f"/api/v1/runs/{run_id}/cases/{first_id}/audio/candidate",
            headers={"Range": "bytes=0-3"},
        )
        assert partial_audio.status_code == 206
        assert partial_audio.content == b"RIFF"
        assert partial_audio.headers["accept-ranges"] == "bytes"
        assert partial_audio.headers["content-range"].startswith("bytes 0-3/")
        assert partial_audio.headers["content-length"] == "4"

        missing_page = await client.get(f"/runs/{run_id}/cases/not-a-case")
        assert missing_page.status_code == 404
        missing_detail = await client.get(f"/api/v1/runs/{run_id}/cases/not-a-case/detail")
        assert missing_detail.status_code == 404


async def test_missing_candidate_audio_returns_not_found(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    samples = np.arange(9_600, dtype=np.float32)
    sf.write(source, 0.05 * np.sin(2 * np.pi * 110 * samples / 48_000), 48_000)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'missing-candidate.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
    )
    await seed_sample_dataset(
        settings,
        source=source,
        amp_id="pending-amp",
        amp_name="Pending Amp",
        amp_type="guitar",
        chunk_count=1,
        chunk_seconds=0.1,
        positions=(((0.5,),),),
    )
    app = create_app(settings)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/v1/runs",
            json={
                "amp_id": "pending-amp",
                "name": "pending-candidate",
                "creator": "tests",
                "unique_positions_used": 1,
                "audio_duration_sum": 0.1,
                "turns": 1,
                "training_time": 1.0,
                "description": "No candidate yet",
                "parameter_count": 1,
            },
        )
        created.raise_for_status()
        run_id = created.json()["id"]
        case_index = (await client.get(f"/api/v1/runs/{run_id}/case-index")).json()
        case_id = case_index["cases"][0]["case_id"]

        response = await client.get(f"/api/v1/runs/{run_id}/cases/{case_id}/audio/candidate")
        assert response.status_code == 404

        detail = (await client.get(f"/api/v1/runs/{run_id}/cases/{case_id}/detail")).json()
        assert detail["audio"]["candidate"] is None
