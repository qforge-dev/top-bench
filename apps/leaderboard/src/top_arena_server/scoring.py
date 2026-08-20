from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Sequence
from contextlib import suppress
from typing import Any

import numpy as np
import soundfile as sf
from scipy import signal
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from .config import Settings
from .database import Database
from .metrics import AudioMetrics, calculate_metrics
from .models import BenchmarkRun, RunCase, RunEvent, now_utc
from .storage import ObjectStorage

LOGGER = logging.getLogger(__name__)


class ScoringService:
    """Small durable database-backed scoring queue for a single server process."""

    def __init__(self, database: Database, storage: ObjectStorage, settings: Settings) -> None:
        self._database = database
        self._storage = storage
        self._settings = settings
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._queued: set[str] = set()

    async def start(self) -> None:
        async with self._database.session() as session:
            recoverable = await session.scalars(
                select(RunCase.id).where(RunCase.status.in_(("uploaded", "scoring")))
            )
            for run_case_id in recoverable:
                await self.enqueue(run_case_id)
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

    async def enqueue(self, run_case_id: str) -> None:
        if run_case_id not in self._queued:
            self._queued.add(run_case_id)
            await self._queue.put(run_case_id)

    async def finalize_if_ready(self, run_id: str) -> bool:
        async with self._database.session() as session:
            run = await session.scalar(
                select(BenchmarkRun).where(BenchmarkRun.id == run_id).with_for_update()
            )
            if run is None:
                return False
            return await self._finalize_if_ready(run, session)

    async def _worker(self) -> None:
        while True:
            run_case_id = await self._queue.get()
            self._queued.discard(run_case_id)
            try:
                await self._score(run_case_id)
            except Exception:
                LOGGER.exception("scoring failed for run case %s", run_case_id)
                await self._mark_failed(run_case_id)
            finally:
                self._queue.task_done()

    async def _score(self, run_case_id: str) -> None:
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
            candidate_key = run_case.candidate_wet_key

        reference_bytes, candidate_bytes = await asyncio.gather(
            self._storage.get(reference_key),
            self._storage.get(candidate_key),
        )
        metrics = await asyncio.to_thread(self._metrics_from_wav, reference_bytes, candidate_bytes)

        async with self._database.session() as session:
            run_case = await session.get(RunCase, run_case_id)
            if run_case is None:
                return
            run_case.status = "completed"
            run_case.esr = metrics.esr
            run_case.human_weighted_esr = metrics.human_weighted_esr
            run_case.mrstft = metrics.mrstft
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
                    },
                )
            )
        await self.finalize_if_ready(run_id)

    @staticmethod
    def _metrics_from_wav(reference_bytes: bytes, candidate_bytes: bytes) -> AudioMetrics:
        reference, reference_rate = sf.read(
            io.BytesIO(reference_bytes), dtype="float32", always_2d=False
        )
        candidate, candidate_rate = sf.read(
            io.BytesIO(candidate_bytes), dtype="float32", always_2d=False
        )
        reference_array = np.asarray(reference, dtype=np.float32)
        candidate_array = np.asarray(candidate, dtype=np.float32)
        if reference_array.ndim == 2:
            reference_array = reference_array.mean(axis=1)
        if candidate_array.ndim == 2:
            candidate_array = candidate_array.mean(axis=1)
        if candidate_rate != reference_rate:
            divisor = int(np.gcd(candidate_rate, reference_rate))
            candidate_array = signal.resample_poly(
                candidate_array,
                reference_rate // divisor,
                candidate_rate // divisor,
            ).astype(np.float32)
        return calculate_metrics(reference_array, candidate_array, sample_rate=reference_rate)

    async def _finalize_if_ready(self, run: BenchmarkRun, session: AsyncSession) -> bool:
        if run.status in {"completed", "failed"}:
            return run.status == "completed"
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
        rows = (
            await session.scalars(
                select(RunCase).where(
                    RunCase.run_id == run.id,
                    RunCase.status == "completed",
                )
            )
        ).all()
        run.completed_cases = len(rows)
        if not run.client_finished:
            return False
        if len(rows) != run.total_cases:
            run.status = "finalizing"
            return False
        run.metrics = aggregate_metrics(rows)
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


def _summary(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p90": None, "worst": None, "best": None}
    array = np.asarray(values, dtype=np.float64)
    return {
        "mean": float(np.mean(array)),
        "p90": float(np.quantile(array, 0.9)),
        "worst": float(np.max(array)),
        "best": float(np.min(array)),
    }


def aggregate_metrics(rows: Sequence[RunCase]) -> dict[str, Any]:
    return {
        "contract": {
            "version": "top-arena-audio-v1",
            "sample_rate": 48_000,
            "esr_epsilon": 1e-12,
            "human_weighting": "A-weighted spectral ESR",
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
        "realtime_x": _summary([value for row in rows if (value := row.realtime_x) is not None]),
    }
