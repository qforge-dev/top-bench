from __future__ import annotations

import json
import logging
import sqlite3
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from tools.reference_corpus.process import s3_upload, sha256, write_json
from tools.reference_corpus.render import _chain_for_amp, _load_mapper
from tools.reference_corpus.settings import resolve_amps

from .config import NamBaselineConfig

LOGGER = logging.getLogger(__name__)


class ProducerState:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                amp_id TEXT NOT NULL,
                position_id TEXT NOT NULL,
                wet_key TEXT NOT NULL,
                job_key TEXT NOT NULL,
                wet_sha256 TEXT NOT NULL,
                uploaded_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def contains(self, job_id: str) -> bool:
        return (
            self.connection.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            is not None
        )

    def count(self) -> int:
        return int(self.connection.execute("SELECT count(*) FROM jobs").fetchone()[0])

    def record(self, row: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO jobs
                (job_id, amp_id, position_id, wet_key, job_key, wet_sha256, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["job_id"],
                row["amp_id"],
                row["position_id"],
                row["wet_key"],
                row["job_key"],
                row["wet_sha256"],
                row["uploaded_at"],
            ),
        )
        self.connection.commit()


def _upload_ready_job(
    config: NamBaselineConfig,
    wet_path: Path,
    job_path: Path,
    row: dict[str, Any],
) -> dict[str, Any]:
    # The ready marker is deliberately last. Bestia can never observe a job
    # whose 190-second wet training capture has not finished uploading.
    s3_upload(wet_path, f"s3://{config.corpus.bucket}/{row['wet_key']}")
    s3_upload(job_path, f"s3://{config.corpus.bucket}/{row['job_key']}")
    wet_path.unlink()
    job_path.unlink()
    return row


def _collect(
    pending: set[Future[dict[str, Any]]],
    state: ProducerState,
    *,
    block: bool,
    total: int,
) -> None:
    if not pending:
        return
    completed = (
        wait(pending, return_when=FIRST_COMPLETED)[0]
        if block
        else {future for future in pending if future.done()}
    )
    for future in completed:
        row = future.result()
        state.record(row)
        pending.remove(future)
        LOGGER.info(
            "[%d/%d] training capture ready: %s %s",
            state.count(),
            total,
            row["amp_name"],
            row["position_id"],
        )


def _prepare_dry(config: NamBaselineConfig) -> tuple[Path, str]:
    output = config.root / "training" / "dry-190s.flac"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists():
        audio, rate = sf.read(config.corpus.reference, always_2d=False, dtype="float64")
        if rate != config.corpus.sample_rate or audio.ndim != 1:
            msg = "NAM training reference must be mono 48 kHz"
            raise ValueError(msg)
        sf.write(output, audio, rate, format="FLAC", subtype="PCM_24")
    digest = sha256(output)
    s3_upload(output, f"s3://{config.corpus.bucket}/{config.dry_190_key}")
    return output, digest


def produce(
    config: NamBaselineConfig,
    selectors: list[str],
    *,
    amp_limit: int | None = None,
    position_limit: int | None = None,
) -> None:
    from pedalboard import load_plugin  # noqa: PLC0415

    amp_manifest = json.loads((config.corpus.root / "manifests" / "amps.json").read_text())
    amps = resolve_amps(amp_manifest, selectors)[:amp_limit]
    _, dry_sha256 = _prepare_dry(config)
    dry, rate = sf.read(config.corpus.reference, always_2d=True, dtype="float32")
    if rate != config.corpus.sample_rate or dry.shape[1] != 1:
        msg = "unexpected 190-second dry format"
        raise ValueError(msg)

    mapper = _load_mapper(config.corpus)
    plugin = load_plugin(str(config.corpus.plugin_path))
    state = ProducerState(config.root / "state" / "producer.sqlite3")
    staging = config.root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    total = len(amp_manifest["amps"]) * config.corpus.position_count
    pending: set[Future[dict[str, Any]]] = set()

    with ThreadPoolExecutor(
        max_workers=config.producer_upload_workers,
        thread_name_prefix="training-capture-upload",
    ) as uploads:
        for amp in amps:
            for position in list(amp["positions"])[:position_limit]:
                job_id = f"{amp['amp_id']}--{position['position_id']}"
                if state.contains(job_id):
                    continue
                while len(pending) >= config.producer_max_pending:
                    _collect(pending, state, block=True, total=total)
                chain = _chain_for_amp(config.corpus, amp, position)
                chain_path = staging / "active-training-chain.json"
                write_json(chain_path, chain)
                mapper.apply_chain_file(plugin, chain_path)
                plugin.process(
                    np.zeros((1, config.corpus.sample_rate // 2), dtype=np.float32),
                    sample_rate=config.corpus.sample_rate,
                    buffer_size=8192,
                    reset=True,
                )
                wet = plugin.process(
                    dry.T,
                    sample_rate=config.corpus.sample_rate,
                    buffer_size=8192,
                    reset=False,
                )
                if wet.shape != (1, len(dry)) or not np.all(np.isfinite(wet)):
                    msg = f"invalid BIAS X training capture: {job_id}"
                    raise RuntimeError(msg)
                peak = float(np.max(np.abs(wet), initial=0.0))
                clipped_samples = int(np.count_nonzero(np.abs(wet) >= 1.0))
                encoded_wet = np.clip(wet[0], -1.0, 1.0 - 2.0**-23)
                wet_path = staging / f"{job_id}.flac"
                sf.write(
                    wet_path,
                    encoded_wet,
                    config.corpus.sample_rate,
                    format="FLAC",
                    subtype="PCM_24",
                )
                wet_key = (
                    f"{config.prefix}/training/wet/{amp['amp_id']}/{position['position_id']}.flac"
                )
                job_key = f"{config.prefix}/queue/ready/{job_id}.json"
                output_root = f"{config.prefix}/models/{amp['amp_id']}/{position['position_id']}"
                job = {
                    "format": "top-arena.nam-a2-full-job.v1",
                    "job_id": job_id,
                    "amp_id": amp["amp_id"],
                    "amp_name": amp["amp_name"],
                    "position_id": position["position_id"],
                    "positions": position["values"],
                    "position_vector": position["vector"],
                    "sample_rate": config.corpus.sample_rate,
                    "frames": len(dry),
                    "duration_seconds": len(dry) / config.corpus.sample_rate,
                    "epochs": config.epochs,
                    "architecture": "official NAM 0.13 A2 Full WaveNet",
                    "latency_samples": config.latency_samples,
                    "train_stop_seconds": config.train_stop_seconds,
                    "dry_key": config.dry_190_key,
                    "dry_sha256": dry_sha256,
                    "wet_key": wet_key,
                    "wet_sha256": sha256(wet_path),
                    "wet_peak": peak,
                    "wet_clipped_samples": clipped_samples,
                    "output_root": output_root,
                    "benchmark_dry_prefix": f"{config.corpus.prefix}/dry",
                    "benchmark_reference_prefix": (
                        f"{config.corpus.prefix}/wet/{amp['amp_id']}/{position['position_id']}"
                    ),
                    "created_at": datetime.now(UTC).isoformat(),
                }
                job_path = staging / f"{job_id}.json"
                write_json(job_path, job)
                row = {
                    **job,
                    "job_key": job_key,
                    "uploaded_at": datetime.now(UTC).isoformat(),
                }
                pending.add(uploads.submit(_upload_ready_job, config, wet_path, job_path, row))
                _collect(pending, state, block=False, total=total)
        while pending:
            _collect(pending, state, block=True, total=total)
