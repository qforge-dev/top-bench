from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import boto3
import numpy as np
import soundfile as sf

from tools.nam_baselines.config import NamBaselineConfig


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scale_audio_file(source: Path, target: Path, *, gain_db: float) -> dict[str, Any]:
    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    rendered = audio * np.float32(10.0 ** (gain_db / 20.0))
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, rendered, sample_rate, subtype="PCM_24", format="FLAC")
    peak = float(np.max(np.abs(rendered))) if rendered.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(rendered, dtype=np.float64)))) if rendered.size else 0.0
    return {
        "frames": int(rendered.shape[0]),
        "sample_rate": int(sample_rate),
        "peak": peak,
        "rms_db": 20.0 * math.log10(max(rms, 1e-12)),
        "clipped_samples": int(np.count_nonzero(np.abs(rendered) >= 1.0)),
        "sha256": _sha256(target),
    }


def derive_metadata(
    source: dict[str, Any],
    *,
    source_amp_id: str,
    target_amp_id: str,
    gain_db: float,
    reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    derived = copy.deepcopy(source)
    derived["amp_id"] = target_amp_id
    derived["amp_name"] = target_amp_id
    derived["job_id"] = str(derived["job_id"]).replace(source_amp_id, target_amp_id)
    for item in cast("list[dict[str, Any]]", derived["cases"]):
        sound_id = str(item["sound_id"])
        item["bias_reference_key"] = str(item["bias_reference_key"]).replace(
            f"/wet/{source_amp_id}/", f"/wet/{target_amp_id}/"
        )
        item["nam_a2_full_key"] = str(item["nam_a2_full_key"]).replace(
            f"/models/{source_amp_id}/", f"/models/{target_amp_id}/"
        )
        item.update(reports[sound_id])
    derived["derivation"] = {
        "kind": "fixed-output-gain",
        "source_amp_id": source_amp_id,
        "gain_db": gain_db,
        "gain_amplitude": 10.0 ** (gain_db / 20.0),
    }
    return derived


def _parse_arguments() -> argparse.Namespace:
    config = NamBaselineConfig()
    parser = argparse.ArgumentParser(description="Derive a fixed-gain NAM reference corpus.")
    parser.add_argument("--source-amp", required=True)
    parser.add_argument("--target-amp", required=True)
    parser.add_argument("--gain-db", required=True, type=float)
    parser.add_argument("--bucket", default=config.corpus.bucket)
    parser.add_argument("--prefix", default=config.prefix)
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    arguments = _parse_arguments()
    client = boto3.client("s3")
    source_root = f"{arguments.prefix}/models/{arguments.source_amp}"
    target_root = f"{arguments.prefix}/models/{arguments.target_amp}"
    with tempfile.TemporaryDirectory(prefix="top-arena-gain-derived-") as temporary:
        root = Path(temporary)
        for position_number in range(1, 11):
            position_id = f"position-{position_number:02d}"
            metadata_key = f"{source_root}/{position_id}/metadata.json"
            metadata = json.loads(
                client.get_object(Bucket=arguments.bucket, Key=metadata_key)["Body"].read()
            )
            cases = cast("list[dict[str, Any]]", metadata["cases"])

            def process(
                case: dict[str, Any], position: str = position_id
            ) -> tuple[str, dict[str, Any]]:
                sound_id = str(case["sound_id"])
                source_file = root / "source" / position / f"{sound_id}.flac"
                target_file = root / "target" / position / f"{sound_id}.flac"
                client.download_file(
                    arguments.bucket,
                    str(case["nam_a2_full_key"]),
                    str(source_file),
                )
                report = scale_audio_file(source_file, target_file, gain_db=arguments.gain_db)
                target_key = f"{target_root}/{position}/outputs/{sound_id}.flac"
                client.upload_file(str(target_file), arguments.bucket, target_key)
                return sound_id, report

            (root / "source" / position_id).mkdir(parents=True, exist_ok=True)
            (root / "target" / position_id).mkdir(parents=True, exist_ok=True)
            with ThreadPoolExecutor(max_workers=arguments.workers) as executor:
                reports = dict(executor.map(process, cases))
            derived = derive_metadata(
                metadata,
                source_amp_id=arguments.source_amp,
                target_amp_id=arguments.target_amp,
                gain_db=arguments.gain_db,
                reports=reports,
            )
            target_metadata = root / "target" / position_id / "metadata.json"
            target_metadata.write_text(json.dumps(derived, indent=2, sort_keys=True) + "\n")
            client.upload_file(
                str(target_metadata),
                arguments.bucket,
                f"{target_root}/{position_id}/metadata.json",
            )
            print(f"derived {arguments.target_amp}/{position_id}")  # noqa: T201


if __name__ == "__main__":
    main()
