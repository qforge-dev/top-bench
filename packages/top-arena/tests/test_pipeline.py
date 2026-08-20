# ruff: noqa: ASYNC240, INP001

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from top_arena._gateway import BenchmarkGateway
from top_arena._models import (
    BenchmarkCase,
    BenchmarkMetadata,
    BenchmarkResult,
    PipelineOptions,
    RunSnapshot,
)
from top_arena._pipeline import BenchmarkRun


@dataclass
class FakeGateway(BenchmarkGateway):
    cases: tuple[BenchmarkCase, ...]
    downloaded: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    last_download_finished_at: float = 0.0
    first_upload_finished_at: float | None = None

    async def create_run(self, metadata: BenchmarkMetadata, amp_id: str) -> str:
        del metadata, amp_id
        return "run-1"

    async def get_manifest(self, amp_id: str) -> tuple[BenchmarkCase, ...]:
        del amp_id
        return self.cases

    async def download_dry(self, case: BenchmarkCase, destination: Path) -> None:
        await asyncio.sleep(0.08 if case.id == "slow" else 0.005)
        destination.write_bytes(case.id.encode())
        self.downloaded.append(case.id)
        self.last_download_finished_at = time.monotonic()

    async def emit_event(
        self,
        run_id: str,
        kind: str,
        case_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        del run_id, case_id, payload
        self.events.append(kind)

    async def upload_wet(
        self,
        run_id: str,
        case_id: str,
        wet_path: Path,
        realtime_x: float,
    ) -> None:
        del run_id, wet_path, realtime_x
        await asyncio.sleep(0.001)
        self.uploaded.append(case_id)
        if self.first_upload_finished_at is None:
            self.first_upload_finished_at = time.monotonic()

    async def finish_run(self, run_id: str) -> None:
        del run_id

    async def get_run(self, run_id: str) -> RunSnapshot:
        return RunSnapshot(
            id=run_id,
            status="completed",
            total_cases=len(self.cases),
            completed_cases=len(self.cases),
            result=BenchmarkResult(
                run_id=run_id,
                status="completed",
                total_cases=len(self.cases),
                completed_cases=len(self.cases),
                metrics={},
            ),
        )


async def test_pipeline_overlaps_download_inference_and_upload(tmp_path: Path) -> None:
    cases = (
        BenchmarkCase("slow", ((0.0,),), "dry/slow.wav", "a" * 64),
        BenchmarkCase("fast-1", ((0.2,),), "dry/fast-1.wav", "b" * 64),
        BenchmarkCase("fast-2", ((0.4,),), "dry/fast-2.wav", "c" * 64),
    )
    gateway = FakeGateway(cases)
    metadata = BenchmarkMetadata(
        name="super-model-v1",
        creator="test-suite",
        unique_positions_used=1,
        audio_duration_sum=15.0,
        turns=1,
        training_time=5_000.0,
        description="Model description",
        parameter_count=40_000,
    )
    run = BenchmarkRun(
        gateway=gateway,
        metadata=metadata,
        cache_dir=tmp_path / "cache",
        options=PipelineOptions(download_concurrency=2, run_concurrency=1, upload_concurrency=2),
    )

    async def model(audio_path: Path, positions: tuple[tuple[float, ...], ...]) -> Path:
        assert positions
        destination = tmp_path / f"wet-{audio_path.name}"
        await asyncio.to_thread(shutil.copyfile, audio_path, destination)
        return destination

    result = await run.run_async("demo-amp", model)

    assert result.status == "completed"
    assert set(gateway.uploaded) == {case.id for case in cases}
    assert gateway.first_upload_finished_at is not None
    assert gateway.first_upload_finished_at < gateway.last_download_finished_at
    assert "download.started" in gateway.events
    assert "inference.completed" in gateway.events
    assert "upload.completed" in gateway.events


async def test_dry_audio_is_reused_from_the_local_cache(tmp_path: Path) -> None:
    case = BenchmarkCase("case-1", ((0.0,),), "dry/shared.wav", "d" * 64)
    gateway = FakeGateway((case,))
    run = BenchmarkRun(
        gateway=gateway,
        metadata=BenchmarkMetadata(
            name="cache-test",
            creator="test-suite",
            unique_positions_used=1,
            audio_duration_sum=5.0,
            turns=1,
            training_time=1.0,
            description="cache",
            parameter_count=1,
        ),
        cache_dir=tmp_path / "cache",
    )

    async def model(audio_path: Path, positions: tuple[tuple[float, ...], ...]) -> Path:
        del positions
        wet = tmp_path / "wet.wav"
        wet.write_bytes(audio_path.read_bytes())
        return wet

    await run.run_async("demo-amp", model)
    await run.run_async("demo-amp", model)

    assert gateway.downloaded == ["case-1"]
    assert "download.cache_hit" in gateway.events


def test_sync_runner_accepts_a_sync_model_callback(tmp_path: Path) -> None:
    case = BenchmarkCase("case-1", ((0.0,),), "dry/example.wav", "e" * 64)
    gateway = FakeGateway((case,))
    run = BenchmarkRun(
        gateway=gateway,
        metadata=BenchmarkMetadata(
            name="sync-model",
            creator="test-suite",
            unique_positions_used=1,
            audio_duration_sum=5.0,
            turns=1,
            training_time=1.0,
            description="sync callback",
            parameter_count=1,
        ),
        cache_dir=tmp_path / "cache",
    )

    def model(audio_path: Path, positions: tuple[tuple[float, ...], ...]) -> str:
        assert positions == ((0.0,),)
        wet_path = tmp_path / "sync-wet.wav"
        wet_path.write_bytes(audio_path.read_bytes())
        return str(wet_path)

    result = run.run("demo-amp", model)

    assert result.status == "completed"
    assert gateway.uploaded == ["case-1"]
