from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from nam.models import init_from_nam


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_directory(
    model_path: Path,
    input_directory: Path,
    output_directory: Path,
    *,
    torch_threads: int,
    output_gain: float = 1.0,
    expected_count: int | None = None,
) -> Path:
    torch.set_num_threads(torch_threads)
    payload = json.loads(model_path.read_text())
    if payload.get("architecture") == "SlimmableContainer":
        payload = max(
            payload["config"]["submodels"],
            key=lambda row: float(row["max_value"]),
        )["model"]
    model = init_from_nam(payload).eval()
    output_directory.mkdir(parents=True, exist_ok=True)
    results = []
    for input_path in sorted(input_directory.glob("sound-*.flac")):
        dry, rate = sf.read(input_path, always_2d=False, dtype="float32")
        if rate != 48_000 or dry.ndim != 1:
            msg = f"unexpected NAM inference input: {input_path}"
            raise ValueError(msg)
        with torch.inference_mode():
            prediction = model(torch.from_numpy(dry), pad_start=True).reshape(-1)
        wet = prediction.detach().cpu().numpy().astype("float32") * output_gain
        if wet.shape != dry.shape or not np.all(np.isfinite(wet)):
            msg = f"invalid NAM inference output: {input_path}"
            raise RuntimeError(msg)
        peak = float(np.max(np.abs(wet), initial=0.0))
        clipped_samples = int(np.count_nonzero(np.abs(wet) >= 1.0))
        encoded_wet = np.clip(wet, -1.0, 1.0 - 2.0**-23)
        output_path = output_directory / input_path.name
        sf.write(output_path, encoded_wet, rate, format="FLAC", subtype="PCM_24")
        rms = float(np.sqrt(np.mean(np.square(encoded_wet), dtype=np.float64)))
        results.append(
            {
                "sound_id": input_path.stem,
                "file": output_path.name,
                "sha256": _sha256(output_path),
                "frames": len(wet),
                "sample_rate": rate,
                "peak": peak,
                "clipped_samples": clipped_samples,
                "rms_db": 20.0 * math.log10(max(rms, 1e-12)),
            }
        )
    if expected_count is not None and len(results) != expected_count:
        msg = f"expected {expected_count} NAM renders, found {len(results)}"
        raise RuntimeError(msg)
    manifest = output_directory / "inference.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "top-arena.nam-a2-full-inference.v1",
                "model": str(model_path),
                "audio_format": "FLAC PCM_24",
                "output_gain": output_gain,
                "outputs": results,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Render 50 dry sounds through one NAM model")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--torch-threads", type=int, default=max(1, (os.cpu_count() or 8) // 4))
    parser.add_argument("--output-gain", type=float, default=1.0)
    parser.add_argument("--expected-count", type=int)
    arguments = parser.parse_args()
    render_directory(
        arguments.model,
        arguments.input_dir,
        arguments.output_dir,
        torch_threads=arguments.torch_threads,
        output_gain=arguments.output_gain,
        expected_count=arguments.expected_count,
    )


if __name__ == "__main__":
    main()
