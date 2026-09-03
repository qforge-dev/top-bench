from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
import pytest
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
                amp_control_count_override=2,
                unique_positions_used=2,
                training_positions=[[0.0, 0.0], [0.2, 0.2]],
                training_dry_files=["training/clean.wav", "training/drive.wav"],
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

        overview_page = await client.get(f"/runs/{run_id}")
        overview_page.raise_for_status()
        assert f'data-run-id="{run_id}"' in overview_page.text
        assert "case-detail-model · Run report · Top Arena" in overview_page.text
        assert "/static/run_overview.css?v=20260903-run-report" in overview_page.text
        assert "/static/run_overview.js?v=20260903-run-report" in overview_page.text
        assert 'id="coverage-chart"' in overview_page.text
        assert 'id="position-body"' in overview_page.text
        assert 'id="case-body"' in overview_page.text

        overview_response = await client.get(f"/api/v1/runs/{run_id}/overview")
        overview_response.raise_for_status()
        overview = overview_response.json()
        assert overview["run"]["id"] == run_id
        assert overview["run"]["cases"] == []
        esr_distribution = overview["metric_distributions"]["esr"]
        assert esr_distribution["count"] == 4
        assert esr_distribution["mean"] == pytest.approx(0.025)
        assert esr_distribution["median"] == pytest.approx(0.025)
        assert esr_distribution["p90"] == pytest.approx(0.037)
        assert esr_distribution["best"] == pytest.approx(0.01)
        assert esr_distribution["worst"] == pytest.approx(0.04)
        assert overview["nam_metric_distributions"]["esr"]["mean"] == 0.055
        assert overview["training_coverage"]["available"] is True
        assert overview["training_coverage"]["analyzed_settings"] == 2
        assert overview["training_coverage"]["training_control_count"] == 2
        assert len(overview["positions"]) == 2
        assert overview["positions"][0]["esr_error_rank"] == 2
        assert overview["positions"][0]["metrics"]["esr"]["mean"] == 0.02
        assert overview["positions"][0]["metrics"]["esr"]["p90"] == 0.028
        assert overview["positions"][1]["esr_error_rank"] == 1
        assert overview["positions"][1]["metrics"]["esr"]["mean"] == 0.03
        assert overview["positions"][1]["url"] == f"/runs/{run_id}/positions/2"
        assert [item["index"] for item in overview["cases"]] == [1, 2, 3, 4]
        assert overview["cases"][0]["url"] == first_url
        assert overview["cases"][0]["position_url"] == f"/runs/{run_id}/positions/1"

        position_response = await client.get(f"/api/v1/runs/{run_id}/positions/1")
        position_response.raise_for_status()
        position = position_response.json()
        assert position["position"]["position_id"] == 1
        assert position["position"]["positions"] == [[0.1, 0.2]]
        assert position["position"]["total_cases"] == 2
        assert position["position"]["training_coverage"][
            "nearest_training_distance"
        ] == pytest.approx(0.070710678)
        assert (
            position["position"]["training_coverage"]["nearest_training_points"][0][
                "training_position_id"
            ]
            == 2
        )
        assert position["training_coverage"]["analyzed_settings"] == 2
        assert [item["index"] for item in position["cases"]] == [1, 3]

        position_page = await client.get(f"/runs/{run_id}/positions/1")
        position_page.raise_for_status()
        assert f'data-run-id="{run_id}"' in position_page.text
        assert 'data-position-id="1"' in position_page.text
        assert "/static/position_detail.js?v=20260903-run-report" in position_page.text
        assert 'id="position-case-chart"' in position_page.text
        assert 'id="nearest-training-controls"' in position_page.text

        assert (await client.get(f"/api/v1/runs/{run_id}/positions/99")).status_code == 404
        assert (await client.get(f"/runs/{run_id}/positions/99")).status_code == 404

        index_response = await client.get(f"/api/v1/runs/{run_id}/case-index")
        index_response.raise_for_status()
        case_index = index_response.json()
        assert case_index["run"]["id"] == run_id
        assert case_index["run"]["cases"] == []
        assert case_index["run"]["training_provenance_included"] is True
        assert case_index["run"]["training_positions"] == [[0.0, 0.0], [0.2, 0.2]]
        assert case_index["run"]["training_dry_files"] == [
            "training/clean.wav",
            "training/drive.wav",
        ]
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
        assert 'id="run-description"' in direct_page.text
        assert "Case detail API test" in direct_page.text
        assert "<title>case-detail-model · Case detail · Top Arena</title>" in direct_page.text
        assert "/static/case_detail.css?v=20260903-training-provenance" in direct_page.text
        assert "/static/case_detail.js?v=20260903-training-provenance" in direct_page.text
        assert f'id="run-report-link" class="back-link" href="/runs/{run_id}"' in direct_page.text
        assert 'id="position-report-link"' in direct_page.text
        assert 'id="training-positions-body"' in direct_page.text
        assert 'id="training-files"' in direct_page.text
        assert 'id="run-started"' in direct_page.text
        assert "Benchmark started · UTC" in direct_page.text
        assert direct_page.text.count(' controls loop preload="none"') == 4
        assert 'class="lab-layout"' in direct_page.text
        assert 'class="lab-sidebar"' in direct_page.text
        assert 'id="play-sequence"' in direct_page.text
        assert 'class="audition-sequence"' in direct_page.text
        assert 'id="waveform-chart"' in direct_page.text
        assert 'id="nam-audio"' in direct_page.text
        assert 'class="aggregate-heading"' in direct_page.text
        assert 'class="aggregate-group aggregate-group-model"' in direct_page.text
        assert 'class="aggregate-group aggregate-group-nam"' in direct_page.text
        assert direct_page.text.index('id="waveform-chart"') < direct_page.text.index(
            'id="play-sequence"'
        )
        assert direct_page.text.index('id="play-sequence"') < direct_page.text.index(
            'id="case-chart"'
        )
        assert 'class="breadcrumb"' not in direct_page.text
        assert "Listen side by side" not in direct_page.text
        assert "Windowed analysis" not in direct_page.text

        amp_page = await client.get("/amps/detail-amp")
        amp_page.raise_for_status()
        assert "2 positions · normalized inputs from 0 to 1" in amp_page.text
        assert amp_page.text.count("<span>Position</span>") == 2
        assert amp_page.text.count('class="amp-parameter-order"') == 6
        assert 'data-label="volume"' in amp_page.text
        assert 'title="0.1">0.100000</span>' in amp_page.text
        assert 'title="0.7">0.700000</span>' in amp_page.text

        first_response = await client.get(f"/api/v1/runs/{run_id}/cases/{first_id}/detail")
        first_response.raise_for_status()
        first = first_response.json()
        assert first["run"]["training_provenance_included"] is False
        assert first["run"]["training_positions"] == []
        assert first["run"]["training_dry_files"] == []
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
        assert first["waveform_url"] == f"/api/v1/runs/{run_id}/cases/{first_id}/waveform"

        waveform_response = await client.get(first["waveform_url"])
        waveform_response.raise_for_status()
        waveform = waveform_response.json()
        assert waveform["duration_seconds"] == 0.1
        assert [series["key"] for series in waveform["series"]] == [
            "reference",
            "nam",
            "model",
        ]
        assert [series["label"] for series in waveform["series"]] == [
            "BIAS X wet",
            "NAM-A2-FULL",
            "Model",
        ]
        assert all(len(series["values"]) == 720 for series in waveform["series"])
        assert all(
            0.0 <= value <= 1.0 for series in waveform["series"] for value in series["values"]
        )

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
                "training_positions": [[0.5] * 6],
                "training_dry_files": ["training.wav"],
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
