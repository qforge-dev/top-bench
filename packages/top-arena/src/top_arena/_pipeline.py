# Copyright (c) 2026 Top Arena contributors

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import secrets
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import soundfile as sf

from top_arena._models import (
    BenchmarkCase,
    BenchmarkMetadata,
    BenchmarkResult,
    ModelCallback,
    PipelineOptions,
)
from top_arena._reporting import ConsoleReporter

if TYPE_CHECKING:
    from top_arena._gateway import BenchmarkGateway


LOGGER = logging.getLogger(__name__)


@runtime_checkable
class _AsyncClosable(Protocol):
    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _DownloadedCase:
    case: BenchmarkCase
    dry_path: Path


@dataclass(frozen=True, slots=True)
class _ProcessedCase:
    case: BenchmarkCase
    wet_path: Path
    realtime_x: float


class BenchmarkRun:
    """A configured model submission that can be run synchronously or asynchronously."""

    def __init__(
        self,
        *,
        gateway: BenchmarkGateway,
        metadata: BenchmarkMetadata,
        cache_dir: Path,
        options: PipelineOptions | None = None,
    ) -> None:
        self._gateway: BenchmarkGateway = gateway
        self._metadata: BenchmarkMetadata = metadata
        self._cache_dir: Path = cache_dir.expanduser()
        self._options: PipelineOptions = options or PipelineOptions()
        self._cache_locks: dict[Path, asyncio.Lock] = {}

    @property
    def metadata(self) -> BenchmarkMetadata:
        return self._metadata

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def run(self, amp_id: str, callback: ModelCallback) -> BenchmarkResult:
        """Run the benchmark from synchronous Python and wait for final scores."""
        try:
            _ = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async(amp_id, callback))
        msg = "BenchmarkRun.run() cannot be used from an active event loop; use run_async()"
        raise RuntimeError(msg)

    async def run_async(self, amp_id: str, callback: ModelCallback) -> BenchmarkResult:
        """Run the benchmark without taking ownership of the caller's event loop."""
        run_id: str | None = None
        completion_task: asyncio.Task[BenchmarkResult] | None = None
        reporter = ConsoleReporter(
            self._options.report_format,
            show_progress=self._options.show_progress,
            min_finding_signal=self._options.report_min_finding_signal,
            min_evidence_signal=self._options.report_min_evidence_signal,
        )
        self._cache_locks = {}
        try:
            run_id = await self._gateway.create_run(self._metadata, amp_id)
            cases = await self._gateway.get_manifest(amp_id)
            await self._warm_up_model(run_id, cases, callback)
            reporter.start(self._metadata.name, amp_id)
            completion_task = asyncio.create_task(self._wait_for_result(run_id, reporter))
            await self._gateway.emit_event(run_id, "run.started", payload={"amp_id": amp_id})
            await self._execute_pipeline(run_id, cases, callback)
            await self._gateway.finish_run(run_id)
            await self._gateway.emit_event(run_id, "run.finish_requested")
            result = await completion_task
            reporter.finish(result)
            return result  # noqa: TRY300
        except Exception as error:
            reporter.fail(error)
            if completion_task is not None and not completion_task.done():
                completion_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await completion_task
            if run_id is not None:
                await self._report_client_failure(run_id, error)
            raise
        finally:
            if completion_task is not None:
                if not completion_task.done():
                    completion_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await completion_task
            if isinstance(self._gateway, _AsyncClosable):
                await self._gateway.aclose()

    async def _report_client_failure(self, run_id: str, error: Exception) -> None:
        for attempt in range(3):
            try:
                await self._gateway.emit_event(
                    run_id,
                    "run.client_failed",
                    payload={"error": str(error)},
                )
            except Exception as notification_error:
                if attempt == 2:
                    LOGGER.warning(
                        "could not report failed benchmark run %s",
                        run_id,
                        exc_info=notification_error,
                    )
                    return
                await asyncio.sleep(0.05 * (attempt + 1))
            else:
                return

    async def _warm_up_model(
        self,
        run_id: str,
        cases: tuple[BenchmarkCase, ...],
        callback: ModelCallback,
    ) -> None:
        if not cases:
            return
        case = secrets.choice(cases)
        await self._gateway.emit_event(run_id, "inference.warmup_started", case.id)
        dry_path = await self._get_dry_audio(run_id, case)
        candidate = await asyncio.to_thread(callback, dry_path, case.positions)
        if inspect.isawaitable(candidate):
            candidate = await candidate
        _ = await asyncio.to_thread(_resolve_output_path, candidate, case.id)
        await self._gateway.emit_event(run_id, "inference.warmup_completed", case.id)

    async def _execute_pipeline(
        self,
        run_id: str,
        cases: tuple[BenchmarkCase, ...],
        callback: ModelCallback,
    ) -> None:
        capacity = self._options.queue_capacity
        download_queue: asyncio.Queue[BenchmarkCase | None] = asyncio.Queue(capacity)
        inference_queue: asyncio.Queue[_DownloadedCase | None] = asyncio.Queue(capacity)
        upload_queue: asyncio.Queue[_ProcessedCase | None] = asyncio.Queue(capacity)

        async def produce() -> None:
            for case in cases:
                await download_queue.put(case)
            for _ in range(self._options.download_concurrency):
                await download_queue.put(None)

        async def download_worker() -> None:
            while (case := await download_queue.get()) is not None:
                dry_path = await self._get_dry_audio(run_id, case)
                await inference_queue.put(_DownloadedCase(case=case, dry_path=dry_path))

        async def inference_worker() -> None:
            while (downloaded := await inference_queue.get()) is not None:
                processed = await self._run_model(run_id, downloaded, callback, len(cases))
                await upload_queue.put(processed)

        async def upload_worker() -> None:
            while (processed := await upload_queue.get()) is not None:
                await self._upload_result(run_id, processed)

        async with asyncio.TaskGroup() as task_group:
            _ = task_group.create_task(produce())
            download_tasks = [
                task_group.create_task(download_worker())
                for _ in range(self._options.download_concurrency)
            ]
            _ = task_group.create_task(
                _close_queue_after(download_tasks, inference_queue, self._options.run_concurrency)
            )
            inference_tasks = [
                task_group.create_task(inference_worker())
                for _ in range(self._options.run_concurrency)
            ]
            _ = task_group.create_task(
                _close_queue_after(inference_tasks, upload_queue, self._options.upload_concurrency)
            )
            for _ in range(self._options.upload_concurrency):
                _ = task_group.create_task(upload_worker())

    async def _get_dry_audio(self, run_id: str, case: BenchmarkCase) -> Path:
        destination = self._dry_cache_path(case)
        lock = self._cache_locks.setdefault(destination, asyncio.Lock())
        async with lock:
            if destination.is_file():
                await self._gateway.emit_event(
                    run_id,
                    "download.cache_hit",
                    case.id,
                    {"dry_key": case.dry_key},
                )
                return destination

            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
            await self._gateway.emit_event(
                run_id,
                "download.started",
                case.id,
                {"dry_key": case.dry_key},
            )
            try:
                await self._gateway.download_dry(case, temporary_path)
                await asyncio.to_thread(os.replace, temporary_path, destination)
            except BaseException:
                with suppress(OSError):
                    temporary_path.unlink()
                raise
            await self._gateway.emit_event(
                run_id,
                "download.completed",
                case.id,
                {"dry_key": case.dry_key},
            )
            return destination

    async def _run_model(
        self,
        run_id: str,
        downloaded: _DownloadedCase,
        callback: ModelCallback,
        case_count: int,
    ) -> _ProcessedCase:
        case = downloaded.case
        await self._gateway.emit_event(run_id, "inference.started", case.id)
        started_at = time.perf_counter()
        candidate = await asyncio.to_thread(callback, downloaded.dry_path, case.positions)
        if inspect.isawaitable(candidate):
            candidate = await candidate
        elapsed_seconds = max(time.perf_counter() - started_at, 1e-12)
        output_path = await asyncio.to_thread(_resolve_output_path, candidate, case.id)

        realtime_x = self._case_duration(case, case_count) / elapsed_seconds
        wet_path = await self._stage_wet_file(run_id, case, output_path)
        await self._gateway.emit_event(
            run_id,
            "inference.completed",
            case.id,
            {"elapsed_seconds": elapsed_seconds, "realtime_x": realtime_x},
        )
        return _ProcessedCase(case=case, wet_path=wet_path, realtime_x=realtime_x)

    async def _stage_wet_file(
        self,
        run_id: str,
        case: BenchmarkCase,
        output_path: Path,
    ) -> Path:
        safe_run_id = hashlib.sha256(run_id.encode()).hexdigest()[:20]
        safe_case_id = hashlib.sha256(case.id.encode()).hexdigest()[:20]
        destination = self._cache_dir / "pending-uploads" / safe_run_id / f"{safe_case_id}.flac"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        try:
            await asyncio.to_thread(_transcode_to_flac, output_path, temporary_path)
            await asyncio.to_thread(os.replace, temporary_path, destination)
        except BaseException:
            with suppress(OSError):
                temporary_path.unlink()
            raise
        return destination

    async def _upload_result(self, run_id: str, processed: _ProcessedCase) -> None:
        await self._gateway.emit_event(
            run_id,
            "upload.started",
            processed.case.id,
            {"realtime_x": processed.realtime_x},
        )
        await self._gateway.upload_wet(
            run_id,
            processed.case.id,
            processed.wet_path,
            processed.realtime_x,
        )
        await self._gateway.emit_event(
            run_id,
            "upload.completed",
            processed.case.id,
            {"realtime_x": processed.realtime_x},
        )
        with suppress(OSError):
            processed.wet_path.unlink()

    async def _wait_for_result(self, run_id: str, reporter: ConsoleReporter) -> BenchmarkResult:
        try:
            async with asyncio.timeout(self._options.completion_timeout_seconds):
                while True:
                    snapshot = await self._gateway.get_run(run_id)
                    reporter.update(snapshot)
                    if snapshot.status == "completed":
                        return snapshot.result or BenchmarkResult(
                            run_id=snapshot.id,
                            status=snapshot.status,
                            total_cases=snapshot.total_cases,
                            completed_cases=snapshot.completed_cases,
                            metrics={},
                        )
                    if snapshot.status == "failed":
                        msg = f"benchmark run {run_id!r} failed on the server"
                        raise RuntimeError(msg)
                    await asyncio.sleep(self._options.poll_interval_seconds)
        except TimeoutError as error:
            msg = f"timed out waiting for benchmark run {run_id!r} to complete"
            raise TimeoutError(msg) from error

    def _dry_cache_path(self, case: BenchmarkCase) -> Path:
        cache_key = case.dry_sha256 or hashlib.sha256(case.dry_key.encode()).hexdigest()
        suffix = Path(case.dry_key).suffix or ".wav"
        return self._cache_dir / "dry" / f"{cache_key}{suffix}"

    def _case_duration(self, case: BenchmarkCase, case_count: int) -> float:
        if case.duration_seconds > 0:
            return case.duration_seconds
        if case_count == 0:
            return 0.0
        return self._metadata.audio_duration_sum / case_count


async def _close_queue_after[T](
    tasks: list[asyncio.Task[None]],
    queue: asyncio.Queue[T | None],
    consumer_count: int,
) -> None:
    _ = await asyncio.gather(*tasks)
    for _ in range(consumer_count):
        await queue.put(None)


def _resolve_output_path(output: Path | str, case_id: str) -> Path:
    output_path = Path(output).expanduser().resolve()
    if not output_path.is_file():
        msg = f"model callback returned a missing file for case {case_id!r}: {output_path}"
        raise FileNotFoundError(msg)
    return output_path


def _transcode_to_flac(source: Path, destination: Path) -> None:
    with (
        sf.SoundFile(source) as input_audio,
        sf.SoundFile(
            destination,
            mode="w",
            samplerate=input_audio.samplerate,
            channels=input_audio.channels,
            format="FLAC",
            subtype="PCM_24",
        ) as output_audio,
    ):
        while True:
            block = input_audio.read(65_536, dtype="float32", always_2d=True)
            if len(block) == 0:
                break
            _ = output_audio.write(block)
