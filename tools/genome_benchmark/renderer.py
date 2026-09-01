from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Protocol

import numpy as np
import soundfile as sf

type PositionMatrix = tuple[tuple[float, ...], ...]

_CONTROL_COUNT = 7
_FIXED_CONTROLS = (("Reverb", 4, 0.0), ("Master", 5, 0.5), ("Bright", 6, 0.0))
_FIXED_TOLERANCE = 1e-7


class _AutomationParameter(Protocol):
    raw_value: float


class _GenomePlugin(Protocol):
    parameters: dict[str, _AutomationParameter]

    def __call__(
        self,
        audio: np.ndarray,
        sample_rate: float,
        *,
        buffer_size: int = 8192,
        reset: bool = False,
    ) -> np.ndarray: ...


def extract_blackface_controls(positions: PositionMatrix) -> tuple[float, float, float, float]:
    """Validate one simple-amp position and return PARADEX's four controls."""
    if len(positions) != 1 or len(positions[0]) != _CONTROL_COUNT:
        msg = "blackface63-simple requires one seven-control position row"
        raise ValueError(msg)
    row = tuple(float(value) for value in positions[0])
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in row):
        msg = "blackface63-simple control values must be finite and between zero and one"
        raise ValueError(msg)
    for name, index, expected in _FIXED_CONTROLS:
        if not math.isclose(row[index], expected, abs_tol=_FIXED_TOLERANCE):
            msg = f"blackface63-simple requires {name}={expected}, received {row[index]}"
            raise ValueError(msg)
    return row[0], row[1], row[2], row[3]


class GenomeParadexHost:
    """Stateful, serialized Genome VST3 renderer for Top Arena callbacks."""

    def __init__(
        self,
        plugin: _GenomePlugin,
        output_dir: Path,
        *,
        warmup_seconds: float = 0.1,
        output_gain: float = 1.0,
    ) -> None:
        if warmup_seconds < 0.0:
            msg = "warmup_seconds cannot be negative"
            raise ValueError(msg)
        if not math.isfinite(output_gain) or output_gain <= 0.0:
            msg = "output_gain must be finite and greater than zero"
            raise ValueError(msg)
        self._plugin = plugin
        self._output_dir = output_dir.expanduser().resolve()
        self._warmup_seconds = warmup_seconds
        self._output_gain = output_gain
        self._lock = threading.Lock()
        self._render_count = 0

    def automation_values(self) -> tuple[float, float, float, float]:
        return tuple(float(self._plugin.parameters[f"a{index}"].raw_value) for index in range(1, 5))  # type: ignore[return-value]

    def render(self, dry_path: Path, positions: PositionMatrix) -> Path:
        controls = extract_blackface_controls(positions)
        with self._lock:
            return self._render_locked(Path(dry_path), controls)

    def _render_locked(self, dry_path: Path, controls: tuple[float, ...]) -> Path:
        dry, sample_rate = sf.read(dry_path, dtype="float32", always_2d=False)
        if dry.ndim != 1:
            msg = f"Genome benchmark input must be mono: {dry_path}"
            raise ValueError(msg)
        if sample_rate <= 0 or not np.isfinite(dry).all():
            msg = f"Genome benchmark input is invalid: {dry_path}"
            raise ValueError(msg)

        for index, value in enumerate(controls, start=1):
            self._plugin.parameters[f"a{index}"].raw_value = value

        channels = dry[np.newaxis, :]
        warmup_frames = round(sample_rate * self._warmup_seconds)
        if warmup_frames:
            warmup = np.zeros((1, warmup_frames), dtype=np.float32)
            _ = self._plugin(warmup, sample_rate, buffer_size=8192, reset=False)
        wet = np.asarray(
            self._plugin(channels, sample_rate, buffer_size=8192, reset=False),
            dtype=np.float32,
        ).squeeze()
        if wet.shape != dry.shape or not np.isfinite(wet).all():
            msg = "Genome returned invalid benchmark audio"
            raise RuntimeError(msg)
        stored = wet * self._output_gain
        peak = float(np.max(np.abs(stored), initial=0.0))
        if peak >= 1.0:
            msg = f"Genome benchmark audio clips at {peak:.4f}; lower --output-gain"
            raise RuntimeError(msg)

        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._render_count += 1
        destination = self._output_dir / f"wet-{self._render_count:04d}.wav"
        partial = destination.with_name(f".{destination.name}.partial.wav")
        sf.write(partial, stored, sample_rate, format="WAV", subtype="FLOAT")
        partial.replace(destination)
        return destination
