from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
import weakref
from collections import deque
from collections.abc import Sequence
from contextlib import suppress
from typing import Any, cast

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy import signal
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from .config import Settings
from .database import Database
from .diagnostics import aggregate_diagnostics, calculate_case_diagnostics
from .metrics import AudioMetrics, CaseAnalysis, calculate_metrics
from .models import BenchmarkCase, BenchmarkRun, RunCase, RunEvent, now_utc
from .storage import ObjectStorage

LOGGER = logging.getLogger(__name__)
_NAM_CACHE_VERSION = "top-arena-nam-metrics-v1"


def baseline_cache_signature(benchmark_case: BenchmarkCase) -> str:
    source = "\0".join(
        (
            _NAM_CACHE_VERSION,
            benchmark_case.reference_wet_key,
            str(benchmark_case.reference_latency_samples),
            benchmark_case.nam_reference_wet_key or "",
        )
    )
    return hashlib.sha256(source.encode()).hexdigest()


class ScoringService:
    """Small durable database-backed scoring queue for a single server process."""

    def __init__(self, database: Database, storage: ObjectStorage, settings: Settings) -> None:
        self._database = database
        self._storage = storage
        self._settings = settings
        self._ready_runs: asyncio.Queue[str] = asyncio.Queue()
        self._pending_by_run: dict[str, deque[str]] = {}
        self._scheduled_runs: set[str] = set()
        self._workers: list[asyncio.Task[None]] = []
        self._queued: set[str] = set()
        self._enqueued_at: dict[str, float] = {}
        self._finalization_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._baseline_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    @property
    def queue_depth(self) -> int:
        return len(self._queued)

    @property
    def worker_count(self) -> int:
        return len(self._workers)

    async def start(self) -> None:
        async with self._database.session() as session:
            recoverable = tuple(
                (
                    await session.execute(
                        select(RunCase.id, RunCase.run_id)
                        .where(RunCase.status.in_(("uploaded", "scoring")))
                        .order_by(RunCase.run_id, RunCase.uploaded_at, RunCase.id)
                    )
                ).all()
            )
            finalizable = tuple(
                (
                    await session.scalars(
                        select(BenchmarkRun.id).where(
                            BenchmarkRun.client_finished.is_(True),
                            BenchmarkRun.status.notin_(("completed", "failed")),
                        )
                    )
                ).all()
            )
        for run_case_id, run_id in recoverable:
            await self.enqueue(run_case_id, run_id=run_id)
        for run_id in finalizable:
            await self.finalize_if_ready(run_id)
        self._workers = [
            asyncio.create_task(self._worker(), name=f"score-worker-{index}")
            for index in range(max(1, self._settings.score_worker_count))
        ]

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            with suppress(asyncio.CancelledError):
                await worker
        self._workers.clear()

    async def enqueue(self, run_case_id: str, *, run_id: str) -> None:
        if run_case_id not in self._queued:
            self._queued.add(run_case_id)
            self._enqueued_at[run_case_id] = time.perf_counter()
            pending = self._pending_by_run.setdefault(run_id, deque())
            pending.append(run_case_id)
            if run_id not in self._scheduled_runs:
                self._scheduled_runs.add(run_id)
                await self._ready_runs.put(run_id)
            depth = self.queue_depth
            if depth in {10, 50, 100, 250, 500, 1_000}:
                LOGGER.warning(
                    "score.queue_depth depth=%d active_runs=%d workers=%d",
                    depth,
                    len(self._scheduled_runs),
                    self.worker_count,
                )

    async def finalize_if_ready(self, run_id: str) -> bool:
        # PostgreSQL serializes these transactions with FOR UPDATE. SQLite ignores
        # that clause, so its scoring workers also need a process-local per-run lock
        # to prevent a stale progress write from landing after terminal completion.
        lock = self._finalization_locks.setdefault(run_id, asyncio.Lock())
        async with lock, self._database.session() as session:
            run = await session.scalar(
                select(BenchmarkRun).where(BenchmarkRun.id == run_id).with_for_update()
            )
            if run is None:
                return False
            return await self._finalize_if_ready(run, session)

    async def _worker(self) -> None:
        while True:
            run_id = await self._ready_runs.get()
            pending = self._pending_by_run[run_id]
            run_case_id = pending.popleft()
            self._queued.discard(run_case_id)
            enqueued_at = self._enqueued_at.pop(run_case_id, time.perf_counter())
            queue_wait_ms = (time.perf_counter() - enqueued_at) * 1_000
            try:
                await self._score(run_case_id, queue_wait_ms=queue_wait_ms)
            except Exception:
                LOGGER.exception(
                    "score.failed run_case_id=%s queue_wait_ms=%.1f queue_depth=%d",
                    run_case_id,
                    queue_wait_ms,
                    self.queue_depth,
                )
                await self._mark_failed(run_case_id)
            finally:
                if pending:
                    self._ready_runs.put_nowait(run_id)
                else:
                    del self._pending_by_run[run_id]
                    self._scheduled_runs.discard(run_id)
                self._ready_runs.task_done()

    async def _score(self, run_case_id: str, *, queue_wait_ms: float) -> None:
        started_at = time.perf_counter()
        async with self._database.session() as session:
            run_case = await session.scalar(
                select(RunCase)
                .options(joinedload(RunCase.benchmark_case))
                .where(RunCase.id == run_case_id)
            )
            if run_case is None or run_case.status == "completed":
                return
            if run_case.candidate_wet_key is None:
                msg = "uploaded case has no candidate object key"
                raise RuntimeError(msg)
            run_case.status = "scoring"
            session.add(
                RunEvent(
                    run_id=run_case.run_id,
                    benchmark_case_id=run_case.benchmark_case_id,
                    kind="score.started",
                    payload={},
                )
            )
            run_id = run_case.run_id
            reference_key = run_case.benchmark_case.reference_wet_key
            reference_latency_samples = run_case.benchmark_case.reference_latency_samples
            dry_key = run_case.benchmark_case.dry_key
            candidate_key = run_case.candidate_wet_key
            benchmark_case_id = run_case.benchmark_case.id
            baseline_signature = baseline_cache_signature(run_case.benchmark_case)

        loaded_at = time.perf_counter()
        reference_bytes, candidate_bytes, dry_bytes = await asyncio.gather(
            self._storage.get(reference_key),
            self._storage.get(candidate_key),
            self._storage.get(dry_key),
        )
        storage_loaded_at = time.perf_counter()
        metrics, diagnostics = await asyncio.to_thread(
            self._metrics_and_diagnostics_from_audio,
            reference_bytes,
            candidate_bytes,
            dry_bytes,
            reference_latency_samples=reference_latency_samples,
        )
        candidate_scored_at = time.perf_counter()
        nam_metrics, baseline_cache = await self._nam_result(
            benchmark_case_id,
            expected_signature=baseline_signature,
            reference_bytes=reference_bytes,
        )
        baseline_scored_at = time.perf_counter()

        async with self._database.session() as session:
            run_case = await session.get(RunCase, run_case_id)
            if run_case is None:
                return
            run_case.status = "completed"
            run_case.esr = metrics.esr
            run_case.human_weighted_esr = metrics.human_weighted_esr
            run_case.mrstft = metrics.mrstft
            run_case.level_db = metrics.level_db
            run_case.peak_db = metrics.peak_db
            run_case.correlation = metrics.correlation
            analysis: dict[str, Any] = dict(metrics.analysis)
            analysis["diagnostics"] = diagnostics
            if nam_metrics is not None:
                run_case.nam_esr = nam_metrics.esr
                run_case.nam_human_weighted_esr = nam_metrics.human_weighted_esr
                run_case.nam_mrstft = nam_metrics.mrstft
                run_case.nam_level_db = nam_metrics.level_db
                run_case.nam_peak_db = nam_metrics.peak_db
                run_case.nam_correlation = nam_metrics.correlation
            run_case.analysis = analysis
            run_case.scored_at = now_utc()
            session.add(
                RunEvent(
                    run_id=run_id,
                    benchmark_case_id=run_case.benchmark_case_id,
                    kind="score.completed",
                    payload={
                        "esr": metrics.esr,
                        "human_weighted_esr": metrics.human_weighted_esr,
                        "mrstft": metrics.mrstft,
                        "level_db": metrics.level_db,
                        "peak_db": metrics.peak_db,
                        "correlation": metrics.correlation,
                        "nam_a2_full": (
                            {
                                "esr": nam_metrics.esr,
                                "human_weighted_esr": nam_metrics.human_weighted_esr,
                                "mrstft": nam_metrics.mrstft,
                                "level_db": nam_metrics.level_db,
                                "peak_db": nam_metrics.peak_db,
                                "correlation": nam_metrics.correlation,
                            }
                            if nam_metrics is not None
                            else None
                        ),
                    },
                )
            )
        await self.finalize_if_ready(run_id)
        finished_at = time.perf_counter()
        LOGGER.info(
            "score.completed run_id=%s case_id=%s queue_wait_ms=%.1f load_ms=%.1f "
            "storage_ms=%.1f candidate_ms=%.1f baseline_ms=%.1f baseline_cache=%s "
            "db_finalize_ms=%.1f total_ms=%.1f queue_depth=%d",
            run_id,
            benchmark_case_id,
            queue_wait_ms,
            (loaded_at - started_at) * 1_000,
            (storage_loaded_at - loaded_at) * 1_000,
            (candidate_scored_at - storage_loaded_at) * 1_000,
            (baseline_scored_at - candidate_scored_at) * 1_000,
            baseline_cache,
            (finished_at - baseline_scored_at) * 1_000,
            (finished_at - started_at) * 1_000,
            self.queue_depth,
        )

    async def _nam_result(
        self,
        benchmark_case_id: str,
        *,
        expected_signature: str,
        reference_bytes: bytes,
    ) -> tuple[AudioMetrics | None, str]:
        lock = self._baseline_locks.setdefault(benchmark_case_id, asyncio.Lock())
        async with lock:
            async with self._database.session() as session:
                benchmark_case = await session.get(BenchmarkCase, benchmark_case_id)
                if benchmark_case is None or benchmark_case.nam_reference_wet_key is None:
                    return None, "unavailable"
                current_signature = baseline_cache_signature(benchmark_case)
                cached = self._cached_nam_result(benchmark_case, current_signature)
                if cached is not None:
                    return cached, "hit"
                nam_reference_key = benchmark_case.nam_reference_wet_key
                reference_latency_samples = benchmark_case.reference_latency_samples
                reference_key = benchmark_case.reference_wet_key

            if current_signature != expected_signature:
                reference_bytes = await self._storage.get(reference_key)
            nam_reference_bytes = await self._storage.get(nam_reference_key)
            metrics = await asyncio.to_thread(
                self._metrics_from_audio,
                reference_bytes,
                nam_reference_bytes,
                reference_latency_samples=reference_latency_samples,
            )
            async with self._database.session() as session:
                benchmark_case = await session.get(BenchmarkCase, benchmark_case_id)
                if (
                    benchmark_case is not None
                    and baseline_cache_signature(benchmark_case) == current_signature
                ):
                    benchmark_case.nam_metrics_cache = self._serialize_metrics(metrics)
                    benchmark_case.nam_cache_signature = current_signature
            return metrics, "miss"

    @staticmethod
    def _serialize_metrics(metrics: AudioMetrics) -> dict[str, Any]:
        return {
            "esr": metrics.esr,
            "human_weighted_esr": metrics.human_weighted_esr,
            "mrstft": metrics.mrstft,
            "level_db": metrics.level_db,
            "peak_db": metrics.peak_db,
            "correlation": metrics.correlation,
            "analysis": metrics.analysis,
        }

    @staticmethod
    def _cached_nam_result(
        benchmark_case: BenchmarkCase,
        signature: str,
    ) -> AudioMetrics | None:
        if benchmark_case.nam_cache_signature != signature:
            return None
        values = benchmark_case.nam_metrics_cache
        analysis = values.get("analysis")
        if not isinstance(analysis, dict):
            return None
        try:
            metrics = AudioMetrics(
                esr=float(values["esr"]),
                human_weighted_esr=float(values["human_weighted_esr"]),
                mrstft=float(values["mrstft"]),
                level_db=float(values["level_db"]),
                peak_db=float(values["peak_db"]),
                correlation=float(values["correlation"]),
                analysis=cast("CaseAnalysis", analysis),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return metrics

    @staticmethod
    def _metrics_from_audio(
        reference_bytes: bytes,
        candidate_bytes: bytes,
        *,
        reference_latency_samples: int,
    ) -> AudioMetrics:
        reference_array, candidate_array, reference_rate = ScoringService._aligned_audio(
            reference_bytes,
            candidate_bytes,
            reference_latency_samples=reference_latency_samples,
        )
        return calculate_metrics(reference_array, candidate_array, sample_rate=reference_rate)

    @staticmethod
    def _metrics_and_diagnostics_from_audio(
        reference_bytes: bytes,
        candidate_bytes: bytes,
        dry_bytes: bytes,
        *,
        reference_latency_samples: int,
    ) -> tuple[AudioMetrics, dict[str, Any]]:
        reference_array, candidate_array, reference_rate = ScoringService._aligned_audio(
            reference_bytes,
            candidate_bytes,
            reference_latency_samples=reference_latency_samples,
        )
        dry_array, dry_rate = ScoringService._read_mono(dry_bytes)
        dry_array = ScoringService._resample(dry_array, dry_rate, reference_rate)
        metrics = calculate_metrics(reference_array, candidate_array, sample_rate=reference_rate)
        diagnostics = calculate_case_diagnostics(
            dry_array,
            reference_array,
            candidate_array,
            sample_rate=reference_rate,
        )
        return metrics, diagnostics

    @staticmethod
    def _aligned_audio(
        reference_bytes: bytes,
        candidate_bytes: bytes,
        *,
        reference_latency_samples: int,
    ) -> tuple[NDArray[np.float32], NDArray[np.float32], int]:
        reference_array, reference_rate = ScoringService._read_mono(reference_bytes)
        candidate_array, candidate_rate = ScoringService._read_mono(candidate_bytes)
        candidate_array = ScoringService._resample(candidate_array, candidate_rate, reference_rate)
        if reference_latency_samples < 0:
            msg = "reference latency must not be negative"
            raise ValueError(msg)
        if reference_latency_samples:
            if (
                len(reference_array) <= reference_latency_samples
                or len(candidate_array) <= reference_latency_samples
            ):
                msg = "audio is shorter than the reference latency"
                raise ValueError(msg)
            reference_array = reference_array[reference_latency_samples:]
            candidate_array = candidate_array[:-reference_latency_samples]
        return reference_array, candidate_array, reference_rate

    @staticmethod
    def _read_mono(value: bytes) -> tuple[NDArray[np.float32], int]:
        audio, sample_rate = sf.read(io.BytesIO(value), dtype="float32", always_2d=False)
        array = np.asarray(audio, dtype=np.float32)
        if array.ndim == 2:
            array = array.mean(axis=1)
        return array, int(sample_rate)

    @staticmethod
    def _resample(
        value: NDArray[np.float32],
        source_rate: int,
        target_rate: int,
    ) -> NDArray[np.float32]:
        if source_rate == target_rate:
            return value
        divisor = int(np.gcd(source_rate, target_rate))
        return cast(
            "NDArray[np.float32]",
            signal.resample_poly(
                value,
                target_rate // divisor,
                source_rate // divisor,
            ).astype(np.float32),
        )

    async def _finalize_if_ready(self, run: BenchmarkRun, session: AsyncSession) -> bool:
        if run.status == "completed":
            return True
        completed_cases = int(
            await session.scalar(
                select(func.count(RunCase.id)).where(
                    RunCase.run_id == run.id,
                    RunCase.status == "completed",
                )
            )
            or 0
        )
        run.completed_cases = completed_cases
        if run.status == "failed":
            return False
        failed_cases = int(
            await session.scalar(
                select(func.count(RunCase.id)).where(
                    RunCase.run_id == run.id,
                    RunCase.status == "failed",
                )
            )
            or 0
        )
        if failed_cases:
            run.status = "failed"
            return False
        if not run.client_finished:
            return False
        if completed_cases != run.total_cases:
            run.status = "finalizing"
            return False
        rows = (
            await session.scalars(
                select(RunCase)
                .options(joinedload(RunCase.benchmark_case).joinedload(BenchmarkCase.amp))
                .where(RunCase.run_id == run.id, RunCase.status == "completed")
            )
        ).all()
        run.metrics = aggregate_metrics(rows, nam_a2_realtime_x=run.nam_a2_realtime_x)
        run.status = "completed"
        run.completed_at = now_utc()
        session.add(
            RunEvent(
                run_id=run.id,
                kind="run.completed",
                payload={"metrics": run.metrics},
            )
        )
        return True

    async def _mark_failed(self, run_case_id: str) -> None:
        async with self._database.session() as session:
            run_case = await session.get(RunCase, run_case_id)
            if run_case is None:
                return
            run_case.status = "failed"
            run_case.error = "scoring failed; inspect server logs"
            run = await session.get(BenchmarkRun, run_case.run_id)
            if run is not None:
                run.status = "failed"
            session.add(
                RunEvent(
                    run_id=run_case.run_id,
                    benchmark_case_id=run_case.benchmark_case_id,
                    kind="score.failed",
                    payload={},
                )
            )


def _summary(values: Sequence[float], *, higher_is_better: bool = False) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p90": None, "worst": None, "best": None}
    array = np.asarray(values, dtype=np.float64)
    return {
        "mean": float(np.mean(array)),
        "p90": float(np.quantile(array, 0.9)),
        "worst": float(np.min(array) if higher_is_better else np.max(array)),
        "best": float(np.max(array) if higher_is_better else np.min(array)),
    }


def aggregate_metrics(
    rows: Sequence[RunCase],
    *,
    nam_a2_realtime_x: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "contract": {
            "version": "top-arena-audio-v3",
            "sample_rate": 48_000,
            "esr_epsilon": 1e-12,
            "analysis": {
                "version": "top-arena-case-analysis-v1",
                "window_seconds": 0.1,
                "hop_seconds": 0.1,
                "dbfs_floor": -120.0,
            },
            "diagnostics": {
                "case_version": "top-arena-case-diagnostics-v1",
                "run_version": "top-arena-run-diagnostics-v6",
                "display_bands_hz": [20, 80, 150, 400, 800, 2_000, 4_000, 8_000, 20_000],
                "phase_windows_ms": {
                    "transient": [0, 50],
                    "early_body": [50, 200],
                    "sustain": [200, 500],
                },
            },
            "human_weighting": "A-weighted spectral ESR",
            "comparisons": {
                "bias_x": "candidate vs latency-aligned BIAS X reference",
                "nam_a2_full": "NAM-A2-FULL baseline vs latency-aligned BIAS X reference",
            },
            "mrstft": [
                {"fft": 512, "hop": 50, "window": 240},
                {"fft": 1024, "hop": 120, "window": 600},
                {"fft": 2048, "hop": 240, "window": 1200},
            ],
        },
        "esr": _summary([value for row in rows if (value := row.esr) is not None]),
        "human_weighted_esr": _summary(
            [value for row in rows if (value := row.human_weighted_esr) is not None]
        ),
        "mrstft": _summary([value for row in rows if (value := row.mrstft) is not None]),
        "level_db": _summary([value for row in rows if (value := row.level_db) is not None]),
        "peak_db": _summary([value for row in rows if (value := row.peak_db) is not None]),
        "correlation": _summary(
            [value for row in rows if (value := row.correlation) is not None],
            higher_is_better=True,
        ),
        "realtime_x": _summary(
            [value for row in rows if (value := row.realtime_x) is not None],
            higher_is_better=True,
        ),
    }
    if nam_a2_realtime_x is not None and nam_a2_realtime_x > 0:
        result["nam_a2_speed_ratio"] = _summary(
            [value / nam_a2_realtime_x for row in rows if (value := row.realtime_x) is not None],
            higher_is_better=True,
        )
        result["speed_calibration"] = {
            "basis": "local native NeuralAmpModelerCore NAM-A2-FULL inference",
            "nam_a2_realtime_x": nam_a2_realtime_x,
        }
    nam_rows = [row for row in rows if row.nam_esr is not None]
    result["nam_a2_full"] = {
        "available_cases": len(nam_rows),
        "esr": _summary([value for row in nam_rows if (value := row.nam_esr) is not None]),
        "human_weighted_esr": _summary(
            [value for row in nam_rows if (value := row.nam_human_weighted_esr) is not None]
        ),
        "mrstft": _summary([value for row in nam_rows if (value := row.nam_mrstft) is not None]),
        "level_db": _summary(
            [value for row in nam_rows if (value := row.nam_level_db) is not None]
        ),
        "peak_db": _summary([value for row in nam_rows if (value := row.nam_peak_db) is not None]),
        "correlation": _summary(
            [value for row in nam_rows if (value := row.nam_correlation) is not None],
            higher_is_better=True,
        ),
    }
    result["diagnostics"] = aggregate_diagnostics(
        rows,
        nam_a2_realtime_x=nam_a2_realtime_x,
    )
    return result
