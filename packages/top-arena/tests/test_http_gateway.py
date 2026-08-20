# Copyright (c) 2026 Top Arena contributors
# ruff: noqa: INP001

from __future__ import annotations

import json
from pathlib import Path

import httpx
from top_arena._gateway import HttpBenchmarkGateway
from top_arena._models import BenchmarkMetadata


async def test_http_gateway_uses_the_server_rest_contract(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            body = json.loads(request.content)
            assert body["amp_id"] == "demo-amp"
            return httpx.Response(201, json={"id": "run-1"})
        if request.url.path == "/api/v1/amps/demo-amp/manifest":
            return httpx.Response(
                200,
                json={
                    "cases": [
                        {
                            "id": "case-1",
                            "positions": [[0.1, 0.2]],
                            "dry_key": "dry/example.wav",
                            "dry_sha256": "a" * 64,
                            "download_url": "/objects/example.wav",
                            "duration_seconds": 5.0,
                        }
                    ]
                },
            )
        if request.url.path == "/objects/example.wav":
            return httpx.Response(200, content=b"dry audio")
        if request.url.path == "/api/v1/runs/run-1/events":
            return httpx.Response(201, json={"id": "event-1"})
        if request.url.path == "/api/v1/runs/run-1/cases/case-1/audio":
            assert request.url.params["realtime_x"] == "2.5"
            assert request.content == b"wet audio"
            return httpx.Response(202, json={"status": "accepted"})
        if request.url.path == "/api/v1/runs/run-1/finish":
            return httpx.Response(202, json={"status": "processing"})
        if request.url.path == "/api/v1/runs/run-1":
            return httpx.Response(
                200,
                json={
                    "id": "run-1",
                    "status": "completed",
                    "total_cases": 1,
                    "completed_cases": 1,
                    "metrics": {"esr": {"mean": 0.01}},
                },
            )
        return httpx.Response(404)

    gateway = HttpBenchmarkGateway(
        "https://arena.test",
        transport=httpx.MockTransport(respond),
    )
    metadata = BenchmarkMetadata(
        name="model-v1",
        creator="tests",
        unique_positions_used=1,
        audio_duration_sum=5.0,
        turns=1,
        training_time=10.0,
        description="test model",
        parameter_count=100,
    )

    run_id = await gateway.create_run(metadata, "demo-amp")
    case = (await gateway.get_manifest("demo-amp"))[0]
    dry_path = tmp_path / "dry.wav"
    await gateway.download_dry(case, dry_path)
    await gateway.emit_event(run_id, "download.completed", case.id)
    wet_path = tmp_path / "wet.wav"
    wet_path.write_bytes(b"wet audio")
    await gateway.upload_wet(run_id, case.id, wet_path, 2.5)
    await gateway.finish_run(run_id)
    snapshot = await gateway.get_run(run_id)
    await gateway.aclose()

    assert dry_path.read_bytes() == b"dry audio"
    assert snapshot.result is not None
    assert snapshot.result.metrics == {"esr": {"mean": 0.01}}
    assert ("GET", "/objects/example.wav") in calls
