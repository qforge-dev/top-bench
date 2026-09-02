from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.genome_reference_corpus.config import GenomeCorpusConfig
from tools.genome_reference_corpus.preset import inspect_model_state
from tools.genome_reference_corpus.renderer import position_controls
from tools.reference_corpus.process import s3_upload, sha256, write_json


def prepare_model_preset(config: GenomeCorpusConfig, amp: dict[str, Any]) -> Path:
    preset = config.presets / f"{amp['amp_id']}.vstpreset"
    model = config.models / f"{amp['renderer_model']}.ampnet"
    if not preset.exists():
        message = f"Genome captured preset does not exist: {preset}"
        raise FileNotFoundError(message)
    if not model.exists():
        message = f"Genome model does not exist: {model}"
        raise FileNotFoundError(message)
    state = inspect_model_state(preset.read_bytes())
    if Path(state.model_path).expanduser().resolve() != model.resolve():
        msg = f"Genome preset {preset.name} does not select {model.name}"
        raise ValueError(msg)
    if not state.preset_state_md5:
        msg = f"Genome preset {preset.name} has no captured model state"
        raise ValueError(msg)
    return preset


def build_render_job(
    config: GenomeCorpusConfig,
    amp: dict[str, Any],
    dry_manifest: dict[str, Any],
    *,
    sound_limit: int | None = None,
    position_limit: int | None = None,
) -> dict[str, Any]:
    positions = list(amp["positions"])[:position_limit]
    sounds = list(dry_manifest["sounds"])[:sound_limit]
    staging = config.root / "staging" / str(amp["amp_id"])
    tasks: list[dict[str, Any]] = []
    for position in positions:
        controls = position_controls(amp, position)
        tasks.extend(
            (
                {
                    "kind": "benchmark",
                    "position_id": position["position_id"],
                    "sound_id": sound["sound_id"],
                    "controls": controls,
                    "source": str((config.corpus.root / sound["file"]).resolve()),
                    "destination": str(
                        staging
                        / "benchmark"
                        / position["position_id"]
                        / f"{sound['sound_id']}.flac"
                    ),
                    "object_key": (
                        f"{config.corpus.prefix}/wet/{amp['amp_id']}/"
                        f"{position['position_id']}/{sound['sound_id']}.flac"
                    ),
                }
            )
            for sound in sounds
        )
        tasks.append(
            {
                "kind": "training",
                "position_id": position["position_id"],
                "controls": controls,
                "source": str(config.training_dry.resolve()),
                "destination": str(staging / "training" / f"{position['position_id']}.flac"),
                "object_key": (
                    f"{config.nam_prefix}/training/wet/{amp['amp_id']}/"
                    f"{position['position_id']}.flac"
                ),
            }
        )
    return {
        "format": "top-arena.genome-reference-render-job.v1",
        "amp": amp,
        "plugin": str(config.plugin.resolve()),
        "preset": str((config.presets / f"{amp['amp_id']}.vstpreset").resolve()),
        "model": str((config.models / f"{amp['renderer_model']}.ampnet").resolve()),
        "output_gain": float(amp.get("reference_output_gain", config.output_gain)),
        "tasks": tasks,
        "manifest": str((staging / "render-manifest.json").resolve()),
    }


def build_training_job(
    config: GenomeCorpusConfig,
    amp: dict[str, Any],
    position: dict[str, Any],
    report: dict[str, Any],
    *,
    dry_sha256: str,
) -> dict[str, Any]:
    amp_id = str(amp["amp_id"])
    position_id = str(position["position_id"])
    output_root = f"{config.nam_prefix}/models/{amp_id}/{position_id}"
    return {
        "format": "top-arena.nam-a2-full-job.v1",
        "job_id": f"{amp_id}--{position_id}",
        "amp_id": amp_id,
        "amp_name": amp["amp_name"],
        "position_id": position_id,
        "positions": position["values"],
        "position_vector": position["vector"],
        "reference_renderer": "genome-paradex",
        "renderer_model": amp["renderer_model"],
        "sample_rate": report["sample_rate"],
        "frames": report["frames"],
        "duration_seconds": report["frames"] / report["sample_rate"],
        "epochs": config.epochs,
        "architecture": "official NAM 0.13 A2 Full WaveNet",
        "latency_samples": config.latency_samples,
        "train_stop_seconds": config.train_stop_seconds,
        "dry_key": config.training_dry_key,
        "dry_sha256": dry_sha256,
        "wet_key": report["object_key"],
        "wet_sha256": report["sha256"],
        "wet_peak": report["peak"],
        "wet_clipped_samples": report["clipped_samples"],
        "output_root": output_root,
        "benchmark_dry_prefix": f"{config.corpus.prefix}/dry",
        "benchmark_reference_prefix": (f"{config.corpus.prefix}/wet/{amp_id}/{position_id}"),
        "created_at": datetime.now(UTC).isoformat(),
    }


def render_amp(
    config: GenomeCorpusConfig,
    amp: dict[str, Any],
    dry_manifest: dict[str, Any],
    *,
    sound_limit: int | None = None,
    position_limit: int | None = None,
) -> None:
    prepare_model_preset(config, amp)
    job = build_render_job(
        config,
        amp,
        dry_manifest,
        sound_limit=sound_limit,
        position_limit=position_limit,
    )
    for label in ("plugin", "preset", "model"):
        if not Path(job[label]).exists():
            msg = f"Genome {label} does not exist: {job[label]}"
            raise FileNotFoundError(msg)
    job_path = config.root / "jobs" / f"{amp['amp_id']}.json"
    write_json(job_path, job)
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "tools.genome_reference_corpus.worker", "--job", str(job_path)],
        check=True,
    )
    manifest = json.loads(Path(job["manifest"]).read_text())
    dry_sha = sha256(config.training_dry)
    positions = {str(row["position_id"]): row for row in amp["positions"]}
    uploads = [
        (
            Path(report["destination"]),
            f"s3://{config.corpus.bucket}/{report['object_key']}",
        )
        for report in manifest["reports"]
    ]
    with ThreadPoolExecutor(max_workers=config.upload_workers) as executor:
        list(executor.map(lambda item: s3_upload(*item), uploads))
    for report in manifest["reports"]:
        if report["kind"] == "training":
            training_job = build_training_job(
                config,
                amp,
                positions[str(report["position_id"])],
                report,
                dry_sha256=dry_sha,
            )
            marker = config.root / "queue" / f"{training_job['job_id']}.json"
            write_json(marker, training_job)
            s3_upload(
                marker,
                (
                    f"s3://{config.corpus.bucket}/{config.nam_prefix}/queue/ready/"
                    f"{training_job['job_id']}.json"
                ),
            )
    provenance = config.root / "manifests" / f"{amp['amp_id']}.json"
    write_json(provenance, manifest)
    s3_upload(
        provenance,
        f"{config.corpus.s3_root}/amps/{amp['amp_id']}/genome-renderer.json",
    )
