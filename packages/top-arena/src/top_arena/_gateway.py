# Copyright (c) 2026 Top Arena contributors

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Protocol, Self, cast
from urllib.parse import quote

import httpx

from top_arena._models import (
    BenchmarkCase,
    BenchmarkMetadata,
    BenchmarkResult,
    RunSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


class BenchmarkGateway(Protocol):
    async def create_run(self, metadata: BenchmarkMetadata, amp_id: str) -> str: ...

    async def get_manifest(self, amp_id: str) -> tuple[BenchmarkCase, ...]: ...

    async def download_dry(self, case: BenchmarkCase, destination: Path) -> None: ...

    async def emit_event(
        self,
        run_id: str,
        kind: str,
        case_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None: ...

    async def upload_wet(
        self,
        run_id: str,
        case_id: str,
        wet_path: Path,
        realtime_x: float,
    ) -> None: ...

    async def finish_run(self, run_id: str) -> None: ...

    async def get_run(self, run_id: str) -> RunSnapshot: ...


class HttpBenchmarkGateway:
    def __init__(
        self,
        server_url: str,
        *,
        timeout: httpx.Timeout | float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._server_url: str = server_url.rstrip("/") + "/"
        self._timeout: httpx.Timeout | float = timeout
        self._transport: httpx.AsyncBaseTransport | None = transport
        self._client: httpx.AsyncClient | None = None

    async def create_run(self, metadata: BenchmarkMetadata, amp_id: str) -> str:
        response = await self._get_client().post(
            "api/v1/runs",
            json={
                "amp_id": amp_id,
                "name": metadata.name,
                "creator": metadata.creator,
                "unique_positions_used": metadata.unique_positions_used,
                "audio_duration_sum": metadata.audio_duration_sum,
                "turns": metadata.turns,
                "training_time": metadata.training_time,
                "description": metadata.description,
                "parameter_count": metadata.parameter_count,
            },
        )
        _ = response.raise_for_status()
        return _required_str(_response_object(response), "id")

    async def get_manifest(self, amp_id: str) -> tuple[BenchmarkCase, ...]:
        response = await self._get_client().get(f"api/v1/amps/{quote(amp_id, safe='')}/manifest")
        _ = response.raise_for_status()
        payload = _response_object(response)
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list):
            msg = "manifest response must contain a cases list"
            raise TypeError(msg)

        return tuple(_parse_case(raw_case) for raw_case in cast("list[object]", raw_cases))

    async def download_dry(self, case: BenchmarkCase, destination: Path) -> None:
        if case.download_url is None:
            msg = f"manifest case {case.id!r} does not contain a download_url"
            raise ValueError(msg)

        async with self._get_client().stream("GET", case.download_url) as response:
            _ = response.raise_for_status()
            with destination.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    _ = output.write(chunk)

    async def emit_event(
        self,
        run_id: str,
        kind: str,
        case_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        response = await self._get_client().post(
            f"api/v1/runs/{quote(run_id, safe='')}/events",
            json={
                "kind": kind,
                "case_id": case_id,
                "payload": payload or {},
            },
        )
        _ = response.raise_for_status()

    async def upload_wet(
        self,
        run_id: str,
        case_id: str,
        wet_path: Path,
        realtime_x: float,
    ) -> None:
        response = await self._get_client().put(
            (f"api/v1/runs/{quote(run_id, safe='')}/cases/{quote(case_id, safe='')}/audio"),
            params={"realtime_x": realtime_x},
            content=_file_chunks(wet_path),
            headers={"content-type": "audio/flac"},
        )
        _ = response.raise_for_status()

    async def finish_run(self, run_id: str) -> None:
        response = await self._get_client().post(f"api/v1/runs/{quote(run_id, safe='')}/finish")
        _ = response.raise_for_status()

    async def get_run(self, run_id: str) -> RunSnapshot:
        response = await self._get_client().get(f"api/v1/runs/{quote(run_id, safe='')}")
        _ = response.raise_for_status()
        payload = _response_object(response)
        snapshot_id = _required_str(payload, "id")
        status = _required_str(payload, "status")
        total_cases = _required_int(payload, "total_cases")
        completed_cases = _required_int(payload, "completed_cases")
        metrics = _optional_object_dict(payload, "metrics")
        result = (
            BenchmarkResult(
                run_id=snapshot_id,
                status=status,
                total_cases=total_cases,
                completed_cases=completed_cases,
                metrics=metrics,
            )
            if status == "completed"
            else None
        )
        return RunSnapshot(
            id=snapshot_id,
            status=status,
            total_cases=total_cases,
            completed_cases=completed_cases,
            result=result,
        )

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def __aenter__(self) -> Self:
        _ = self._get_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback
        await self.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._server_url,
                timeout=self._timeout,
                follow_redirects=True,
                transport=self._transport,
            )
        return self._client


async def _file_chunks(path: Path, *, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    with path.open("rb") as source:
        while chunk := await asyncio.to_thread(source.read, chunk_size):
            yield chunk


def _response_object(response: httpx.Response) -> dict[str, object]:
    raw_payload = cast("object", json.loads(response.content))
    return _object_dict(raw_payload, context="response")


def _parse_case(raw_case: object) -> BenchmarkCase:
    payload = _object_dict(raw_case, context="manifest case")
    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, list):
        msg = "manifest case positions must be a list"
        raise TypeError(msg)

    positions: list[tuple[float, ...]] = []
    for raw_row in cast("list[object]", raw_positions):
        if not isinstance(raw_row, list):
            msg = "each positions matrix row must be a list"
            raise TypeError(msg)
        row: list[float] = []
        for raw_value in cast("list[object]", raw_row):
            if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
                msg = "position values must be numbers"
                raise TypeError(msg)
            row.append(float(raw_value))
        positions.append(tuple(row))

    return BenchmarkCase(
        id=_required_str(payload, "id"),
        positions=tuple(positions),
        dry_key=_required_str(payload, "dry_key"),
        dry_sha256=_required_str(payload, "dry_sha256"),
        download_url=_required_str(payload, "download_url"),
        duration_seconds=_optional_float(payload, "duration_seconds", default=0.0),
    )


def _object_dict(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{context} must be a JSON object with string keys"
        raise TypeError(msg)
    object_mapping = cast("dict[object, object]", value)
    if not all(isinstance(key, str) for key in object_mapping):
        msg = f"{context} must be a JSON object with string keys"
        raise TypeError(msg)
    return cast("dict[str, object]", object_mapping)


def _required_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        msg = f"response field {key!r} must be a string"
        raise TypeError(msg)
    return value


def _required_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"response field {key!r} must be an integer"
        raise TypeError(msg)
    return value


def _optional_float(payload: dict[str, object], key: str, *, default: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"response field {key!r} must be a number"
        raise TypeError(msg)
    return float(value)


def _optional_object_dict(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key, {})
    return _object_dict(value, context=f"response field {key!r}")
