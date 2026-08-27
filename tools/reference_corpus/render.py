from __future__ import annotations

import copy
import importlib.util
import json
import logging
import sqlite3
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import soundfile as sf

from .audio import load_dry_manifest
from .config import CorpusConfig
from .process import s3_exists, s3_upload, sha256, write_json
from .settings import resolve_amps

LOGGER = logging.getLogger(__name__)


def _load_mapper(config: CorpusConfig) -> ModuleType:
    module_path = config.mapper_root / "bias_x_render.py"
    specification = importlib.util.spec_from_file_location("top_arena_bias_x_render", module_path)
    if specification is None or specification.loader is None:
        msg = f"could not load BIAS X mapper from {module_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _chain_for_amp(
    config: CorpusConfig, amp: dict[str, Any], position: dict[str, Any]
) -> dict[str, Any]:
    template_path = config.mapper_root / "chains" / "dynasty-chime-amp-only.json"
    chain = copy.deepcopy(json.loads(template_path.read_text()))
    module = next(item for item in chain["sigPath"] if item.get("dspId") == "BiasOneAmp")
    module["ampId"] = amp["amp_id"]
    module["param"] = [
        {"id": int(control["index"]), "value": float(position["values"][control["name"]])}
        for control in amp["controls"]
    ]
    chain["name"] = f"{amp['amp_name']} {position['position_id']}"
    chain["description"] = "TOP Arena reference-corpus render"
    return chain


class RenderState:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS renders (
                object_key TEXT PRIMARY KEY,
                amp_id TEXT NOT NULL,
                position_id TEXT NOT NULL,
                sound_id TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                frames INTEGER NOT NULL,
                peak REAL NOT NULL,
                uploaded_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def contains(self, object_key: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM renders WHERE object_key = ?", (object_key,)
        ).fetchone()
        return row is not None

    def count(self) -> int:
        return int(self.connection.execute("SELECT count(*) FROM renders").fetchone()[0])

    def record(self, row: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO renders
                (object_key, amp_id, position_id, sound_id, sha256, frames, peak, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["object_key"],
                row["amp_id"],
                row["position_id"],
                row["sound_id"],
                row["sha256"],
                row["frames"],
                row["peak"],
                row["uploaded_at"],
            ),
        )
        self.connection.commit()

    def rows(self) -> list[dict[str, Any]]:
        columns = [
            "object_key",
            "amp_id",
            "position_id",
            "sound_id",
            "sha256",
            "frames",
            "peak",
            "uploaded_at",
        ]
        return [
            dict(zip(columns, row, strict=True))
            for row in self.connection.execute(
                "SELECT object_key, amp_id, position_id, sound_id, sha256, frames, peak, "
                "uploaded_at FROM renders ORDER BY object_key"
            )
        ]


def _sync_render_manifest(config: CorpusConfig, state: RenderState) -> None:
    path = config.root / "manifests" / "renders.json"
    rows = state.rows()
    write_json(
        path,
        {
            "format": "top-arena.bias-x-renders.v1",
            "audio_format": "FLAC PCM_24",
            "completed_count": len(rows),
            "renders": rows,
        },
    )
    s3_upload(path, f"{config.s3_root}/manifests/renders.json")


def _upload_render(
    config: CorpusConfig,
    output: Path,
    row: dict[str, Any],
) -> dict[str, Any]:
    s3_upload(output, f"s3://{config.bucket}/{row['object_key']}")
    output.unlink()
    return row


def _collect_uploads(
    pending: set[Future[dict[str, Any]]],
    state: RenderState,
    *,
    block: bool,
) -> list[dict[str, Any]]:
    if not pending:
        return []
    if block:
        completed, _ = wait(pending, return_when=FIRST_COMPLETED)
    else:
        completed = {future for future in pending if future.done()}
    rows: list[dict[str, Any]] = []
    for future in completed:
        row = future.result()
        state.record(row)
        pending.remove(future)
        rows.append(row)
    return rows


def render(
    config: CorpusConfig,
    selectors: list[str],
    *,
    sound_limit: int | None = None,
    position_limit: int | None = None,
    verify_s3: bool = False,
    upload_workers: int | None = None,
    max_pending_uploads: int | None = None,
) -> None:
    from pedalboard import load_plugin  # noqa: PLC0415

    dry = load_dry_manifest(config)
    amp_manifest = json.loads((config.root / "manifests" / "amps.json").read_text())
    amps = resolve_amps(amp_manifest, selectors)
    sounds = list(dry["sounds"])[:sound_limit]
    mapper = _load_mapper(config)
    plugin = load_plugin(str(config.plugin_path))
    state = RenderState(config.root / "state" / "renders.sqlite3")
    staging = config.root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    total = sum(len(amp["positions"]) for amp in amp_manifest["amps"]) * len(dry["sounds"])
    worker_count = upload_workers or config.upload_workers
    queue_limit = max_pending_uploads or config.max_pending_uploads
    if worker_count < 1 or queue_limit < worker_count:
        msg = "upload queue must be at least as large as the worker count"
        raise ValueError(msg)
    pending: set[Future[dict[str, Any]]] = set()
    completed_global = state.count()

    def account(rows: list[dict[str, Any]]) -> None:
        nonlocal completed_global
        for row in rows:
            completed_global += 1
            LOGGER.info(
                "[%d/%d] %s %s %s uploaded",
                completed_global,
                total,
                row["amp_name"],
                row["position_id"],
                row["sound_id"],
            )
            if completed_global % 100 == 0:
                _sync_render_manifest(config, state)

    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="s3-upload") as uploads:
        for amp in amps:
            for position in list(amp["positions"])[:position_limit]:
                chain = _chain_for_amp(config, amp, position)
                chain_path = staging / "active-chain.json"
                write_json(chain_path, chain)
                mapper.apply_chain_file(plugin, chain_path)
                for sound in sounds:
                    key = (
                        f"{config.prefix}/wet/{amp['amp_id']}/"
                        f"{position['position_id']}/{sound['sound_id']}.flac"
                    )
                    if state.contains(key) or (verify_s3 and s3_exists(config.bucket, key)):
                        continue
                    while len(pending) >= queue_limit:
                        account(_collect_uploads(pending, state, block=True))
                    dry_path = config.root / sound["file"]
                    audio, rate = sf.read(dry_path, always_2d=True, dtype="float32")
                    if rate != config.sample_rate or audio.shape[1] != 1:
                        msg = f"unexpected dry format: {dry_path}"
                        raise ValueError(msg)
                    plugin.process(
                        np.zeros((1, config.sample_rate), dtype=np.float32),
                        sample_rate=config.sample_rate,
                        buffer_size=8192,
                        reset=True,
                    )
                    wet = plugin.process(
                        audio.T,
                        sample_rate=config.sample_rate,
                        buffer_size=8192,
                        reset=False,
                    )
                    if wet.shape != (1, len(audio)) or not np.all(np.isfinite(wet)):
                        msg = f"invalid plugin output for {key}"
                        raise RuntimeError(msg)
                    output = (
                        staging
                        / f"{amp['amp_id']}--{position['position_id']}--{sound['sound_id']}.flac"
                    )
                    sf.write(
                        output,
                        wet[0],
                        config.sample_rate,
                        format="FLAC",
                        subtype="PCM_24",
                    )
                    row = {
                        "object_key": key,
                        "amp_id": amp["amp_id"],
                        "amp_name": amp["amp_name"],
                        "position_id": position["position_id"],
                        "sound_id": sound["sound_id"],
                        "sha256": sha256(output),
                        "frames": len(audio),
                        "peak": float(np.max(np.abs(wet), initial=0.0)),
                        "uploaded_at": datetime.now(UTC).isoformat(),
                    }
                    pending.add(uploads.submit(_upload_render, config, output, row))
                    account(_collect_uploads(pending, state, block=False))
        while pending:
            account(_collect_uploads(pending, state, block=True))
    _sync_render_manifest(config, state)


def generate_benchmark_manifest(config: CorpusConfig) -> Path:
    dry = load_dry_manifest(config)
    amps = json.loads((config.root / "manifests" / "amps.json").read_text())["amps"]
    sounds = dry["sounds"][: config.benchmark_sounds_per_position]
    cases = [
        {
            "case_id": (f"{amp['amp_id']}--{position['position_id']}--{sound['sound_id']}"),
            "amp_id": amp["amp_id"],
            "position_id": position["position_id"],
            "positions": position["values"],
            "dry_key": f"{config.prefix}/dry/{sound['sound_id']}.flac",
            "reference_key": (
                f"{config.prefix}/wet/{amp['amp_id']}/"
                f"{position['position_id']}/{sound['sound_id']}.flac"
            ),
        }
        for amp in amps
        for position in amp["positions"]
        for sound in sounds
    ]
    path = config.root / "manifests" / "benchmark-cases.json"
    write_json(
        path,
        {
            "format": "top-arena.benchmark-cases.v1",
            "selection": (
                f"all {config.benchmark_sounds_per_position} dry sounds "
                f"for each of {config.position_count} positions"
            ),
            "cases_per_amp": config.position_count * config.benchmark_sounds_per_position,
            "cases": cases,
        },
    )
    s3_upload(path, f"{config.s3_root}/manifests/benchmark-cases.json")
    return path
