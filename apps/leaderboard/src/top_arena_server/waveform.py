from __future__ import annotations

import io

import numpy as np
import soundfile as sf

WAVEFORM_POINT_COUNT = 720


def waveform_envelope(value: bytes, *, point_count: int = WAVEFORM_POINT_COUNT) -> list[float]:
    samples, _sample_rate = sf.read(io.BytesIO(value), dtype="float32", always_2d=False)
    mono = np.asarray(samples, dtype=np.float32)
    if mono.ndim == 2:
        mono = mono.mean(axis=1)
    mono = np.nan_to_num(mono, nan=0.0, posinf=1.0, neginf=-1.0)
    if mono.size == 0:
        msg = "waveform audio must not be empty"
        raise ValueError(msg)
    edges = np.linspace(0, mono.size, point_count + 1, dtype=np.int64)
    values: list[float] = []
    for index in range(point_count):
        start = int(edges[index])
        end = max(int(edges[index + 1]), start + 1)
        window = mono[start : min(end, mono.size)]
        peak = float(np.max(np.abs(window))) if window.size else 0.0
        values.append(float(np.clip(peak, 0.0, 1.0)))
    return values
