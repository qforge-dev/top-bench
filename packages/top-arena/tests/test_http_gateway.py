# Copyright (c) 2026 Top Arena contributors
# ruff: noqa: INP001

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from top_arena._gateway import HttpBenchmarkGateway
from top_arena._models import BenchmarkCase, BenchmarkMetadata


async def test_http_gateway_uses_the_server_rest_contract(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    event_batches: list[list[dict[str, object]]] = []

    def respond(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            body = json.loads(request.content)
            assert body["amp_id"] == "demo-amp"
            assert body["amp_control_count"] == 5
            return httpx.Response(201, json={"id": "run-1"})
        if request.method == "PATCH" and request.url.path == "/api/v1/runs/run-1":
            assert json.loads(request.content) == {"amp_control_count": 5}
            return httpx.Response(200, json={"id": "run-1"})
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
        if request.url.path == "/api/v1/runs/run-1/events/batch":
            body = json.loads(request.content)
            event_batches.append(body["events"])
            return httpx.Response(201, json={"accepted": len(body["events"])})
        if request.url.path == "/api/v1/runs/run-1/cases/case-1/audio":
            assert request.url.params["realtime_x"] == "2.5"
            assert request.content == b"wet audio"
            assert request.headers["content-type"] == "audio/flac"
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
        amp_control_count=5,
    )

    run_id = await gateway.create_run(metadata, "demo-amp")
    await gateway.update_run_metadata(run_id, {"amp_control_count": 5})
    case = (await gateway.get_manifest("demo-amp"))[0]
    dry_path = tmp_path / "dry.wav"
    await gateway.download_dry(case, dry_path)
    await gateway.emit_event(run_id, "download.completed", case.id)
    wet_path = tmp_path / "wet.flac"
    wet_path.write_bytes(b"wet audio")
    await gateway.upload_wet(run_id, case.id, wet_path, 2.5)
    await gateway.finish_run(run_id)
    snapshot = await gateway.get_run(run_id)
    await gateway.aclose()

    assert dry_path.read_bytes() == b"dry audio"
    assert snapshot.result is not None
    assert snapshot.result.metrics == {"esr": {"mean": 0.01}}
    assert ("GET", "/objects/example.wav") in calls
    assert ("PATCH", "/api/v1/runs/run-1") in calls
    assert event_batches == [[{"kind": "download.completed", "case_id": "case-1", "payload": {}}]]


@pytest.mark.parametrize("transient_status", [503, 521])
async def test_http_gateway_retries_a_transient_upload(
    tmp_path: Path,
    transient_status: int,
) -> None:
    attempts = 0
    uploaded_bodies: list[bytes] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        uploaded_bodies.append(await request.aread())
        return httpx.Response(transient_status if attempts == 1 else 202)

    wet_path = tmp_path / "wet.flac"
    wet_path.write_bytes(b"retryable wet audio")
    gateway = HttpBenchmarkGateway(
        "https://arena.test",
        transport=httpx.MockTransport(respond),
    )

    await gateway.upload_wet("run-1", "case-1", wet_path, 2.5)
    await gateway.aclose()

    assert attempts == 2
    assert uploaded_bodies == [b"retryable wet audio", b"retryable wet audio"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "JSON object"),
        ({"cases": {}}, "cases list"),
        ({"cases": [{"positions": "invalid"}]}, "positions must be a list"),
        ({"cases": [{"positions": ["invalid"]}]}, "matrix row must be a list"),
        ({"cases": [{"positions": [[True]]}]}, "position values must be numbers"),
        ({"cases": [{"positions": [[0.5]]}]}, "field 'id' must be a string"),
    ],
)
async def test_manifest_rejects_malformed_payloads(payload: object, message: str) -> None:
    gateway = HttpBenchmarkGateway(
        "https://arena.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
    )

    with pytest.raises(TypeError, match=message):
        await gateway.get_manifest("demo-amp")
    await gateway.aclose()


async def test_download_requires_a_manifest_url(tmp_path: Path) -> None:
    gateway = HttpBenchmarkGateway("https://arena.test")
    case = BenchmarkCase("case-1", ((0.0,),), "dry.wav", "a" * 64)

    with pytest.raises(ValueError, match="does not contain a download_url"):
        await gateway.download_dry(case, tmp_path / "dry.wav")
    await gateway.aclose()
