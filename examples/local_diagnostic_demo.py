from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import httpx
import numpy as np
import soundfile as sf
from scipy import signal
from sqlalchemy import select
from top_arena import PipelineOptions, PositionMatrix, ReportFormat
from top_arena._gateway import HttpBenchmarkGateway
from top_arena._models import BenchmarkMetadata
from top_arena._pipeline import BenchmarkRun
from top_arena_server.app import create_app
from top_arena_server.config import Settings
from top_arena_server.models import BenchmarkCase
from top_arena_server.seed import default_positions, seed_sample_dataset

_SAMPLE_RATE = 48_000
_AMP_ID = "local-diagnostic-amp"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a self-contained diagnostic demo")
    parser.add_argument(
        "--format",
        choices=("agent", "text", "json", "jsonl", "none"),
        default="agent",
    )
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--min-finding-signal", type=float, default=1.0)
    parser.add_argument("--min-evidence-signal", type=float, default=1.0)
    return parser.parse_args()


def _demo_source(seconds: float = 4.0) -> np.ndarray:
    samples = np.arange(round(seconds * _SAMPLE_RATE), dtype=np.float64)
    audio = np.zeros_like(samples)
    for index, onset_seconds in enumerate(np.arange(0.1, seconds - 0.2, 0.22)):
        onset = round(onset_seconds * _SAMPLE_RATE)
        length = min(round(0.3 * _SAMPLE_RATE), len(audio) - onset)
        local_time = np.arange(length, dtype=np.float64) / _SAMPLE_RATE
        frequency = 82.41 * (1.0 + (index % 7) / 6.0)
        envelope = np.exp(-local_time * (7.0 + index % 3))
        note = (
            np.sin(2 * np.pi * frequency * local_time)
            + 0.35 * np.sin(2 * np.pi * frequency * 2 * local_time)
            + 0.18 * np.sin(2 * np.pi * frequency * 3 * local_time)
        )
        audio[onset : onset + length] += envelope * note
    peak = max(float(np.max(np.abs(audio))), 1e-12)
    return np.asarray(0.45 * audio / peak, dtype=np.float32)


async def _run(
    report_format: ReportFormat,
    *,
    show_progress: bool,
    min_finding_signal: float,
    min_evidence_signal: float,
) -> None:
    sequence = count()
    with TemporaryDirectory(prefix="top-arena-local-demo-") as temporary:
        root = Path(temporary)
        source = root / "source.wav"
        sf.write(source, _demo_source(), _SAMPLE_RATE, subtype="PCM_24")
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{root / 'demo.db'}",
            storage_backend="filesystem",
            storage_path=root / "objects",
            public_base_url="http://demo",
            score_worker_count=4,
            score_poll_interval_seconds=0.01,
        )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            await seed_sample_dataset(
                settings,
                source=source,
                amp_id=_AMP_ID,
                amp_name="Local diagnostic amp",
                amp_type="guitar",
                chunk_count=3,
                chunk_seconds=1.0,
                positions=default_positions(),
            )
            async with app.state.services.database.session() as session:
                cases = (await session.scalars(select(BenchmarkCase))).all()
                for case in cases:
                    case.nam_reference_wet_key = case.dry_key

            def demo_model(dry_path: Path, positions: PositionMatrix) -> Path:
                dry, sample_rate = sf.read(dry_path, dtype="float32", always_2d=False)
                controls = positions[0]
                volume, master = controls[0], controls[-1]
                drive = 1.35 + 2.65 * volume
                wet = np.tanh(dry * drive) / np.tanh(drive)
                wet *= 0.65 + 0.3 * master
                high = signal.sosfilt(
                    signal.butter(2, 2_200, btype="highpass", fs=sample_rate, output="sos"),
                    wet,
                )
                candidate = np.asarray(wet + 0.12 * high, dtype=np.float32)
                destination = root / f"candidate-{next(sequence):03d}.wav"
                sf.write(destination, candidate, sample_rate, subtype="FLOAT")
                return destination

            run = BenchmarkRun(
                gateway=HttpBenchmarkGateway(
                    "http://demo",
                    transport=httpx.ASGITransport(app=app),
                ),
                metadata=BenchmarkMetadata(
                    name=f"local-diagnostic-demo-{datetime.now(UTC):%H%M%S}",
                    creator="local-demo",
                    training_positions=tuple(position[0] for position in default_positions()),
                    training_dry_files=("none://procedural-diagnostic-model",),
                    audio_duration_sum=15.0,
                    turns=1,
                    training_time=0.0,
                    description="Synthetic model with a deliberate high-frequency mismatch.",
                    parameter_count=0,
                ),
                cache_dir=root / "cache",
                options=PipelineOptions(
                    download_concurrency=4,
                    run_concurrency=2,
                    upload_concurrency=4,
                    poll_interval_seconds=0.05,
                    completion_timeout_seconds=60.0,
                    report_format=report_format,
                    show_progress=show_progress,
                    report_min_finding_signal=min_finding_signal,
                    report_min_evidence_signal=min_evidence_signal,
                ),
            )
            _ = await run.run_async(_AMP_ID, demo_model)


def main() -> None:
    arguments = _arguments()
    asyncio.run(
        _run(
            cast("ReportFormat", arguments.format),
            show_progress=not arguments.no_progress,
            min_finding_signal=arguments.min_finding_signal,
            min_evidence_signal=arguments.min_evidence_signal,
        )
    )


if __name__ == "__main__":
    main()
