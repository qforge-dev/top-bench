from __future__ import annotations

import csv
import json
import logging
import math
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .config import CorpusConfig
from .process import s3_download, s3_upload, sha256, write_json

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AudioLevels:
    integrated_lufs: float
    true_peak_db: float
    sample_peak_db: float
    rms_db: float


_INTEGRATED = re.compile(r"I:\s+(-?[0-9.]+) LUFS")
_TRUE_PEAK = re.compile(r"Peak:\s+(-?[0-9.]+) dBFS")


def measure_levels(path: Path) -> AudioLevels:
    result = __import__("subprocess").run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    integrated = _INTEGRATED.findall(result.stderr)
    peaks = _TRUE_PEAK.findall(result.stderr)
    if not integrated or not peaks:
        msg = f"ffmpeg did not report EBU R128 levels for {path}"
        raise RuntimeError(msg)
    audio, _ = sf.read(path, always_2d=False, dtype="float64")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    peak = float(np.max(np.abs(audio), initial=0.0))
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
    return AudioLevels(
        integrated_lufs=float(integrated[-1]),
        true_peak_db=float(peaks[-1]),
        sample_peak_db=20.0 * math.log10(max(peak, 1e-12)),
        rms_db=20.0 * math.log10(max(rms, 1e-12)),
    )


def _load_window(path: Path, row: dict[str, str], config: CorpusConfig) -> np.ndarray:
    audio, source_rate = sf.read(path, always_2d=True, dtype="float64")
    mono = np.mean(audio, axis=1)
    duration_frames = round(config.clip_seconds * source_rate)
    if len(mono) < duration_frames:
        msg = "source is shorter than 15 seconds"
        raise ValueError(msg)
    table_rate = int(row["sample_rate"])
    center_seconds = (
        (int(row["valid_start_sample"]) + int(row["valid_end_sample"])) / 2 / table_rate
    )
    start = round(center_seconds * source_rate - duration_frames / 2)
    start = min(max(0, start), len(mono) - duration_frames)
    clip = mono[start : start + duration_frames]
    if source_rate != config.sample_rate:
        divisor = math.gcd(source_rate, config.sample_rate)
        clip = resample_poly(clip, config.sample_rate // divisor, source_rate // divisor)
    expected = round(config.clip_seconds * config.sample_rate)
    if len(clip) < expected:
        clip = np.pad(clip, (0, expected - len(clip)))
    return np.asarray(clip[:expected], dtype=np.float64)


def _normalize_clip(
    clip: np.ndarray,
    *,
    target_lufs: float,
    config: CorpusConfig,
) -> tuple[np.ndarray, AudioLevels, AudioLevels, float]:
    with tempfile.TemporaryDirectory(prefix="top-arena-levels-") as directory:
        before_path = Path(directory) / "before.wav"
        after_path = Path(directory) / "after.wav"
        sf.write(before_path, clip, config.sample_rate, subtype="FLOAT")
        before = measure_levels(before_path)
        if not math.isfinite(before.integrated_lufs) or before.integrated_lufs <= -70:
            msg = "candidate is silent"
            raise ValueError(msg)
        gain_db = min(
            target_lufs - before.integrated_lufs,
            config.true_peak_cap_db - before.true_peak_db,
        )
        normalized = clip * (10.0 ** (gain_db / 20.0))
        sf.write(after_path, normalized, config.sample_rate, subtype="PCM_24")
        after = measure_levels(after_path)
        normalized, _ = sf.read(after_path, always_2d=False, dtype="float64")
    return np.asarray(normalized), before, after, gain_db


def prepare_dry(config: CorpusConfig, candidates_csv: Path, *, upload: bool = True) -> Path:
    dry_dir = config.root / "dry"
    cache_dir = config.root / "source" / "cache"
    manifest_path = config.root / "manifests" / "dry.json"
    dry_dir.mkdir(parents=True, exist_ok=True)
    reference_levels = measure_levels(config.reference)
    rows = list(csv.DictReader(candidates_csv.open()))
    sources = sorted({row["source_name"] for row in rows})
    if len(sources) * config.sounds_per_source != config.sound_count:
        msg = "the source-balanced selection does not produce 50 sounds"
        raise ValueError(msg)

    selected: list[dict[str, Any]] = []
    for source in sources:
        source_rows = [row for row in rows if row["source_name"] == source]
        accepted = 0
        for row in source_rows:
            if accepted >= config.sounds_per_source:
                break
            source_path = cache_dir / f"{row['recording_id']}.wav"
            if not source_path.exists():
                s3_download(row["dry_s3_uri"], source_path)
            if sha256(source_path) != row["dry_sha256"]:
                msg = f"source checksum mismatch: {source_path}"
                raise ValueError(msg)
            try:
                clip = _load_window(source_path, row, config)
                normalized, before, after, gain_db = _normalize_clip(
                    clip,
                    target_lufs=reference_levels.integrated_lufs,
                    config=config,
                )
            except ValueError:
                continue
            loudness_error = abs(after.integrated_lufs - reference_levels.integrated_lufs)
            if loudness_error > config.max_loudness_error_lu:
                LOGGER.info(
                    "skipping %s: peak cap leaves it %.1f LU below target",
                    row["recording_id"],
                    loudness_error,
                )
                continue
            sound_id = f"sound-{len(selected) + 1:02d}"
            output = dry_dir / f"{sound_id}.flac"
            sf.write(output, normalized, config.sample_rate, format="FLAC", subtype="PCM_24")
            item = {
                "sound_id": sound_id,
                "source_name": source,
                "source_id": row["source_id"],
                "recording_id": row["recording_id"],
                "source_s3_uri": row["dry_s3_uri"],
                "source_sha256": row["dry_sha256"],
                "iceberg_segment": {
                    "start_sample": int(row["valid_start_sample"]),
                    "end_sample": int(row["valid_end_sample"]),
                    "sample_rate": int(row["sample_rate"]),
                },
                "file": str(output.relative_to(config.root)),
                "sha256": sha256(output),
                "frames": round(config.clip_seconds * config.sample_rate),
                "sample_rate": config.sample_rate,
                "duration_seconds": config.clip_seconds,
                "input_gain_db": gain_db,
                "levels_before": asdict(before),
                "levels_after": asdict(after),
            }
            selected.append(item)
            accepted += 1
            LOGGER.info(
                "[%02d/%d] %s: %.1f -> %.1f LUFS (%+.1f dB)",
                len(selected),
                config.sound_count,
                source,
                before.integrated_lufs,
                after.integrated_lufs,
                gain_db,
            )
            if upload:
                s3_upload(output, f"{config.s3_root}/dry/{output.name}")
        if accepted != config.sounds_per_source:
            msg = f"only found {accepted} usable 15-second sounds for {source}"
            raise RuntimeError(msg)

    manifest = {
        "format": "top-arena.reference-dry.v1",
        "reference": {
            "path": str(config.reference),
            "sha256": sha256(config.reference),
            "levels": asdict(reference_levels),
        },
        "normalization": {
            "method": "constant_linear_gain",
            "target_lufs": reference_levels.integrated_lufs,
            "true_peak_cap_db": config.true_peak_cap_db,
            "max_loudness_error_lu": config.max_loudness_error_lu,
            "limiter": False,
        },
        "sound_count": len(selected),
        "duration_seconds": sum(float(row["duration_seconds"]) for row in selected),
        "sounds": selected,
    }
    write_json(manifest_path, manifest)
    if upload:
        s3_upload(manifest_path, f"{config.s3_root}/manifests/dry.json")
    return manifest_path


def load_dry_manifest(config: CorpusConfig) -> dict[str, Any]:
    return json.loads((config.root / "manifests" / "dry.json").read_text())
