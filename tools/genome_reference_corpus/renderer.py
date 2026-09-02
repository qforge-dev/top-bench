from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import soundfile as sf


class _Parameter(Protocol):
    raw_value: float


class _Plugin(Protocol):
    parameters: dict[str, _Parameter]

    def __call__(
        self,
        audio: np.ndarray,
        sample_rate: float,
        *,
        buffer_size: int,
        reset: bool,
    ) -> np.ndarray: ...


def automation_slots_ready(plugin: _Plugin) -> bool:
    """Return whether Genome has exposed six writable PARADEX automation slots."""
    try:
        values = [float(plugin.parameters[f"a{index}"].raw_value) for index in range(1, 7)]
    except (KeyError, TypeError, ValueError):
        return False
    return all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values)


def position_controls(amp: dict[str, Any], position: dict[str, Any]) -> tuple[float, ...]:
    names = [str(control["name"]) for control in amp["controls"]]
    values = tuple(float(position["values"][name]) for name in names)
    vector = tuple(float(value) for value in position["vector"])
    if len(values) != 6:
        msg = f"Genome simple amps require six controls, received {len(values)}"
        raise ValueError(msg)
    if values != vector:
        msg = "Genome position vector does not match its named control values"
        raise ValueError(msg)
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
        msg = "Genome position controls must be finite and between zero and one"
        raise ValueError(msg)
    return values


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class GenomeReferenceRenderer:
    def __init__(
        self,
        plugin: _Plugin,
        *,
        output_gain: float,
        warmup_seconds: float = 0.1,
    ) -> None:
        if not math.isfinite(output_gain) or not 0.0 < output_gain <= 1.0:
            msg = "output_gain must be finite, greater than zero, and at most one"
            raise ValueError(msg)
        if not math.isfinite(warmup_seconds) or warmup_seconds < 0.0:
            msg = "warmup_seconds must be finite and non-negative"
            raise ValueError(msg)
        self.plugin = plugin
        self.output_gain = output_gain
        self.warmup_seconds = warmup_seconds

    def automation_values(self) -> tuple[float, ...]:
        return tuple(float(self.plugin.parameters[f"a{index}"].raw_value) for index in range(1, 7))

    def render_file(
        self,
        source: Path,
        destination: Path,
        controls: tuple[float, ...],
    ) -> dict[str, Any]:
        dry, sample_rate = sf.read(source, dtype="float32", always_2d=False)
        if dry.ndim != 1 or sample_rate <= 0 or not np.isfinite(dry).all():
            msg = f"Genome reference input must be valid mono audio: {source}"
            raise ValueError(msg)
        if len(controls) != 6:
            msg = "Genome simple amps require six automation values"
            raise ValueError(msg)
        for index, value in enumerate(controls, start=1):
            self.plugin.parameters[f"a{index}"].raw_value = float(value)
        channels = dry[np.newaxis, :]
        warmup_frames = round(sample_rate * self.warmup_seconds)
        if warmup_frames:
            self.plugin(
                np.zeros((1, warmup_frames), dtype=np.float32),
                sample_rate,
                buffer_size=8192,
                reset=True,
            )
        wet = np.asarray(
            self.plugin(channels, sample_rate, buffer_size=8192, reset=False),
            dtype=np.float32,
        ).squeeze()
        if wet.shape != dry.shape or not np.isfinite(wet).all():
            msg = f"Genome returned invalid reference audio for {source}"
            raise RuntimeError(msg)
        dry64 = dry.astype(np.float64)
        wet64 = wet.astype(np.float64)
        dry_power = float(np.dot(dry64, dry64))
        wet_power = float(np.dot(wet64, wet64))
        if dry_power > 0.0 and wet_power > 0.0:
            passthrough_gain = float(np.dot(dry64, wet64) / dry_power)
            residual = wet64 - passthrough_gain * dry64
            residual_ratio = float(np.sqrt(np.dot(residual, residual) / wet_power))
        else:
            passthrough_gain = 0.0
            residual_ratio = 1.0
        passthrough_residual_db = 20.0 * math.log10(max(residual_ratio, 1e-12))
        if passthrough_residual_db <= -100.0:
            msg = (
                f"Genome returned dry passthrough at gain {passthrough_gain:.6f}; "
                "the PARADEX model did not load"
            )
            raise RuntimeError(msg)
        stored = wet * np.float32(self.output_gain)
        peak = float(np.max(np.abs(stored), initial=0.0))
        clipped_samples = int(np.count_nonzero(np.abs(stored) >= 1.0))
        if clipped_samples:
            msg = f"Genome reference clips at {peak:.4f}; lower output gain"
            raise RuntimeError(msg)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f".{destination.name}.partial.flac")
        sf.write(partial, stored, sample_rate, format="FLAC", subtype="PCM_24")
        partial.replace(destination)
        rms = float(np.sqrt(np.mean(np.square(stored, dtype=np.float64))))
        return {
            "frames": len(stored),
            "sample_rate": int(sample_rate),
            "peak": peak,
            "rms_db": 20.0 * math.log10(max(rms, 1e-12)),
            "clipped_samples": clipped_samples,
            "passthrough_gain": passthrough_gain,
            "passthrough_residual_db": passthrough_residual_db,
            "sha256": _sha256(destination),
        }
