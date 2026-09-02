from __future__ import annotations

import json
from pathlib import Path

import httpx
from top_arena_server.app import SIMPLE_AMP_IDS, create_app
from top_arena_server.config import Settings
from top_arena_server.models import Amp, BenchmarkRun


def _run(index: int, *, amp_id: str, creator: str) -> BenchmarkRun:
    return BenchmarkRun(
        amp_id=amp_id,
        name=f"model-{index:02d}",
        creator=creator,
        unique_positions_used=100 - index,
        audio_duration_sum=30.0,
        turns=1,
        training_time=60.0,
        description="target model" if index == 17 else "pagination model",
        parameter_count=1_000 + index,
        status="completed",
        client_finished=True,
        total_cases=1,
        completed_cases=1,
        metrics={
            "esr": {"mean": 0.01 + index / 1_000},
            "human_weighted_esr": {"mean": 0.02 + index / 1_000},
            "mrstft": {"mean": 0.1 + index / 1_000},
            "realtime_x": {"mean": 10.0 + index},
            "nam_a2_full": {
                "esr": {"mean": 0.1},
                "human_weighted_esr": {"mean": 0.2},
                "mrstft": {"mean": 0.3},
            },
            "diagnostics": {"large_unused_value": "x" * 100_000},
        },
    )


def test_simple_amp_set_includes_quiet_training_level_variant() -> None:
    assert {
        "blackface63-simple",
        "blackface63-simple-quiet",
        "genome-artisan-100-ch1-simple",
        "genome-brit1959-ch1-simple",
        "genome-calibro-normal-simple",
        "genome-eldorado-syn-simple",
        "genome-flatback-dual-ch3-simple",
        "genome-fried-r50-dirty-simple",
        "genome-hektor-lead-simple",
        "genome-lyndon-lion-clean-simple",
        "genome-petaluma-rockrider-clean-simple",
        "genome-revelation-120-ch4-simple",
    } == SIMPLE_AMP_IDS


async def test_leaderboard_filters_and_sorts_before_paginating(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'leaderboard.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        async with app.state.services.database.session() as session:
            session.add_all(
                [
                    Amp(
                        id="normal-amp",
                        name="Normal Amp",
                        amp_type="guitar",
                        control_names=["Gain", "Bass", "Middle", "Treble", "Master"],
                    ),
                    Amp(
                        id="blackface63-simple",
                        name="Simple Amp",
                        amp_type="guitar",
                        control_names=["Gain", "Tone"],
                    ),
                ]
            )
            session.add_all(
                _run(
                    index,
                    amp_id="normal-amp" if index < 30 else "blackface63-simple",
                    creator="Lab A" if index % 2 == 0 else "Lab B",
                )
                for index in range(35)
            )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            unpaginated = (await client.get("/api/v1/leaderboard")).json()
            assert unpaginated["total_runs"] == 35
            assert len(unpaginated["runs"]) == 35
            assert unpaginated["chart_runs"] == []

            default_parameters = {"amp_scope": "normal", "page_size": 25}
            first_response = await client.get(
                "/api/v1/leaderboard",
                params=default_parameters,
            )
            first = first_response.json()
            assert first["page"] == 1
            assert first["page_size"] == 25
            assert first["total_runs"] == 30
            assert first["total_pages"] == 2
            assert len(first["runs"]) == 25
            assert len(first["chart_runs"]) == 30
            assert first["runs"][0]["name"] == "model-00"
            assert first["run_ranks"][first["runs"][0]["id"]] == 1
            assert set(first["runs"][0]["metrics"]) == {
                "esr",
                "human_weighted_esr",
                "mrstft",
                "nam_a2_full",
                "nam_a2_speed_ratio",
                "realtime_x",
            }
            assert "diagnostics" not in first["runs"][0]["metrics"]
            assert len(first_response.content) < 100_000
            etag = first_response.headers["etag"]
            unchanged = await client.get(
                "/api/v1/leaderboard",
                params=default_parameters,
                headers={"If-None-Match": etag},
            )
            assert unchanged.status_code == 304
            assert unchanged.content == b""
            etag_digest = etag.strip('"')
            caddy_etag = f'W/"{etag_digest}-gzip"'
            compressed_unchanged = await client.get(
                "/api/v1/leaderboard",
                params=default_parameters,
                headers={"If-None-Match": caddy_etag},
            )
            assert compressed_unchanged.status_code == 304
            assert set(first["chart_runs"][0]) == {
                "id",
                "name",
                "amp_id",
                "amp_name",
                "amp_control_count",
                "unique_positions_used",
                "esr",
            }

            second = (
                await client.get(
                    "/api/v1/leaderboard",
                    params={**default_parameters, "page": 2},
                )
            ).json()
            assert [run["name"] for run in second["runs"]] == [
                "model-25",
                "model-26",
                "model-27",
                "model-28",
                "model-29",
            ]

            descending = (
                await client.get(
                    "/api/v1/leaderboard",
                    params={
                        "sort": "name",
                        "direction": "desc",
                        "amp_scope": "normal",
                        "page_size": 10,
                    },
                )
            ).json()
            assert [run["name"] for run in descending["runs"]] == [
                f"model-{index:02d}" for index in range(29, 19, -1)
            ]

            leanest = (
                await client.get(
                    "/api/v1/leaderboard",
                    params={
                        "sort": "positionsPerControl",
                        "amp_scope": "normal",
                        "page_size": 5,
                    },
                )
            ).json()
            assert [run["name"] for run in leanest["runs"]] == [
                "model-29",
                "model-28",
                "model-27",
                "model-26",
                "model-25",
            ]

            searched = (
                await client.get(
                    "/api/v1/leaderboard",
                    params={
                        "search": "TARGET",
                        "amp_scope": "normal",
                        "page_size": 25,
                    },
                )
            ).json()
            assert searched["total_runs"] == 1
            assert searched["runs"][0]["name"] == "model-17"
            assert len(searched["chart_runs"]) == 1

            by_creator = (
                await client.get(
                    "/api/v1/leaderboard",
                    params={"creator": "Lab A", "amp_scope": "normal"},
                )
            ).json()
            assert by_creator["total_runs"] == 15
            assert all(run["creator"] == "Lab A" for run in by_creator["runs"])

            simple = (
                await client.get("/api/v1/leaderboard", params={"amp_scope": "simple"})
            ).json()
            assert simple["total_runs"] == 5
            assert {run["amp_id"] for run in simple["runs"]} == {"blackface63-simple"}
            assert simple["creators"] == ["Lab A", "Lab B"]

            clamped = (
                await client.get(
                    "/api/v1/leaderboard",
                    params={**default_parameters, "page": 99},
                )
            ).json()
            assert clamped["page"] == 2
            assert len(clamped["runs"]) == 5

            dashboard = await client.get(
                "/",
                params={
                    "amp_scope": "normal",
                    "amp_id": "normal-amp",
                    "creator": "Lab B",
                    "search": "TARGET",
                    "sort": "name",
                    "direction": "desc",
                    "page": 1,
                    "page_size": 10,
                },
            )
            dashboard.raise_for_status()
            opening = '<script id="leaderboard-initial-data" type="application/json">'
            serialized = dashboard.text.split(opening, maxsplit=1)[1].split(
                "</script>", maxsplit=1
            )[0]
            initial = json.loads(serialized)
            assert initial["total_runs"] == 1
            assert [run["name"] for run in initial["runs"]] == ["model-17"]
            assert 'value="TARGET"' in dashboard.text
            assert '<option value="normal" selected>Normal amps</option>' in dashboard.text
            assert '<option value="normal-amp" selected>Normal Amp</option>' in dashboard.text
            assert '<option value="Lab B" selected>Lab B</option>' in dashboard.text
