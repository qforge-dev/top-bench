# ruff: noqa: ASYNC240, INP001

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
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
    uploaded_formats: list[tuple[str, str, str, str, int]] = field(default_factory=list)
    uploaded_realtime: list[tuple[str, float]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    upload_finished: asyncio.Event = field(default_factory=asyncio.Event)
    overlap_observed: bool = False
    client_failure_errors_remaining: int = 0

    async def create_run(self, metadata: BenchmarkMetadata, amp_id: str) -> str:
        del metadata, amp_id
        return "run-1"

    async def get_manifest(self, amp_id: str) -> tuple[BenchmarkCase, ...]:
        del amp_id
        return self.cases

    async def download_dry(self, case: BenchmarkCase, destination: Path) -> None:
        if case.id == "slow":
            async with asyncio.timeout(1):
                await self.upload_finished.wait()
            self.overlap_observed = True
        else:
            await asyncio.sleep(0.005)
        sf.write(
            destination,
            np.zeros(480, dtype=np.float32),
            48_000,
            format="WAV",
            subtype="PCM_24",
        )
        self.downloaded.append(case.id)

    async def emit_event(
        self,
        run_id: str,
        kind: str,
        case_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        del run_id, case_id, payload
        if kind == "run.client_failed" and self.client_failure_errors_remaining > 0:
            self.client_failure_errors_remaining -= 1
            msg = "temporary event failure"
            raise RuntimeError(msg)
        self.events.append(kind)

    async def upload_wet(
        self,
        run_id: str,
        case_id: str,
        wet_path: Path,
        realtime_x: float,
    ) -> None:
        del run_id
        self.uploaded_realtime.append((case_id, realtime_x))
        with sf.SoundFile(wet_path) as audio:
            self.uploaded_formats.append(
                (case_id, wet_path.suffix, audio.format, audio.subtype, audio.samplerate)
            )
        await asyncio.sleep(0.001)
        self.uploaded.append(case_id)
        self.upload_finished.set()

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


async def test_pipeline_overlaps_download_inference_and_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = (
        BenchmarkCase("slow", ((0.0,),), "dry/slow.wav", "a" * 64),
        BenchmarkCase("fast-1", ((0.2,),), "dry/fast-1.wav", "b" * 64),
        BenchmarkCase("fast-2", ((0.4,),), "dry/fast-2.wav", "c" * 64),
    )
    gateway = FakeGateway(cases)
    monkeypatch.setattr("top_arena._pipeline.secrets.choice", lambda values: values[1])
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
    assert gateway.overlap_observed
    assert "download.started" in gateway.events
    assert "inference.completed" in gateway.events
    assert "upload.completed" in gateway.events


async def test_pipeline_warms_model_with_random_case_without_uploading_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = (
        BenchmarkCase("case-1", ((0.1,),), "dry/one.wav", "1" * 64, duration_seconds=0.01),
        BenchmarkCase("case-2", ((0.9,),), "dry/two.wav", "2" * 64, duration_seconds=0.01),
    )
    gateway = FakeGateway(cases)
    monkeypatch.setattr("top_arena._pipeline.secrets.choice", lambda values: values[1])
    run = BenchmarkRun(
        gateway=gateway,
        metadata=BenchmarkMetadata(
            name="warm-model",
            creator="test-suite",
            unique_positions_used=2,
            audio_duration_sum=0.02,
            turns=1,
            training_time=1.0,
            description="warm-up test",
            parameter_count=1,
        ),
        cache_dir=tmp_path / "cache",
    )
    calls: list[tuple[tuple[float, ...], ...]] = []

    def model(audio_path: Path, positions: tuple[tuple[float, ...], ...]) -> Path:
        calls.append(positions)
        wet_path = tmp_path / f"wet-{len(calls)}.wav"
        wet_path.write_bytes(audio_path.read_bytes())
        return wet_path

    await run.run_async("demo-amp", model)

    assert calls[0] == cases[1].positions
    assert len(calls) == len(cases) + 1
    assert calls.count(cases[1].positions) == 2
    assert set(gateway.uploaded) == {case.id for case in cases}
    assert len(gateway.uploaded_realtime) == len(cases)
    assert gateway.events.count("inference.warmup_started") == 1
    assert gateway.events.count("inference.warmup_completed") == 1
    assert gateway.events.count("inference.started") == len(cases)
    assert gateway.events.count("inference.completed") == len(cases)


async def test_client_failure_notification_retries_transient_errors(tmp_path: Path) -> None:
    case = BenchmarkCase("case-1", ((0.0,),), "dry/input.wav", "f" * 64)
    gateway = FakeGateway((case,), client_failure_errors_remaining=2)
    run = BenchmarkRun(
        gateway=gateway,
        metadata=BenchmarkMetadata(
            name="failing-model",
            creator="test-suite",
            unique_positions_used=1,
            audio_duration_sum=0.01,
            turns=1,
            training_time=1.0,
            description="Failure reporting",
            parameter_count=1,
        ),
        cache_dir=tmp_path / "cache",
    )

    def broken_model(_audio_path: Path, _positions: tuple[tuple[float, ...], ...]) -> Path:
        msg = "model failed"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="model failed"):
        await run.run_async("demo-amp", broken_model)

    assert gateway.client_failure_errors_remaining == 0
    assert "run.client_failed" in gateway.events


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


async def test_model_wav_output_is_staged_as_pcm24_flac(tmp_path: Path) -> None:
    case = BenchmarkCase("case-1", ((0.0,),), "dry/input.wav", "f" * 64)
    gateway = FakeGateway((case,))
    run = BenchmarkRun(
        gateway=gateway,
        metadata=BenchmarkMetadata(
            name="flac-upload",
            creator="test-suite",
            unique_positions_used=1,
            audio_duration_sum=0.01,
            turns=1,
            training_time=1.0,
            description="FLAC staging",
            parameter_count=1,
        ),
        cache_dir=tmp_path / "cache",
    )

    def model(_audio_path: Path, _positions: tuple[tuple[float, ...], ...]) -> Path:
        wet_path = tmp_path / "model-output.wav"
        sf.write(
            wet_path,
            np.linspace(-0.5, 0.5, 480, dtype=np.float32),
            48_000,
            format="WAV",
            subtype="FLOAT",
        )
        return wet_path

    await run.run_async("demo-amp", model)

    assert gateway.uploaded_formats == [("case-1", ".flac", "FLAC", "PCM_24", 48_000)]


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
