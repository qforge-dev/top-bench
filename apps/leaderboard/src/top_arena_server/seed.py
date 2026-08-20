from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy import signal
from sqlalchemy import select

from .config import Settings
from .database import Database
from .models import Amp, BenchmarkCase
from .storage import create_storage

PositionMatrix = tuple[tuple[float, ...], ...]
FloatAudio = NDArray[np.float32]


def default_positions() -> tuple[PositionMatrix, ...]:
    """Five Blackface 63 positions in canonical model control order.

    The six values are volume, bright, bass, middle, treble, and master. Each
    benchmark row contains one static position, represented as a one-row matrix.
    """
    return (
        (
            (
                0.14596686163468764,
                1.0,
                0.36446787600385044,
                0.7915800416884399,
                0.20742652296430342,
                0.9249949963442391,
            ),
        ),
        (
            (
                0.9025994349111174,
                1.0,
                0.8879421990067325,
                0.7705708202664379,
                0.22298824814582652,
                0.9367268833740224,
            ),
        ),
        (
            (
                0.33663395566253496,
                1.0,
                0.445218708145412,
                0.9773574273768145,
                0.13910065015274875,
                0.9853869767239871,
            ),
        ),
        (
            (
                0.43890649776107793,
                0.0,
                0.7204138704453926,
                0.3796328259127344,
                0.6473639138527951,
                0.8367565894431489,
            ),
        ),
        (
            (
                0.8757990112532245,
                1.0,
                0.13335236063002753,
                0.7938769067962497,
                0.5709786576608339,
                0.9089533748770093,
            ),
        ),
    )


def _load_mono(path: Path, sample_rate: int = 48_000) -> FloatAudio:
    value, source_rate = sf.read(path, dtype="float32", always_2d=False)
    audio = np.asarray(value, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if source_rate != sample_rate:
        divisor = int(np.gcd(source_rate, sample_rate))
        audio = cast(
            "FloatAudio",
            signal.resample_poly(
                audio,
                sample_rate // divisor,
                source_rate // divisor,
            ).astype(np.float32),
        )
    return cast("FloatAudio", audio)


def _wav_bytes(value: FloatAudio, sample_rate: int = 48_000) -> bytes:
    destination = io.BytesIO()
    sf.write(destination, value, sample_rate, format="WAV", subtype="PCM_24")
    return destination.getvalue()


def _placeholder_wet(dry: FloatAudio, position: PositionMatrix) -> FloatAudio:
    controls = position[0]
    volume = controls[0] if controls else 0.5
    drive = 1.5 + 3.0 * volume
    saturated = np.tanh(dry * drive) / np.tanh(drive)
    return cast("FloatAudio", (saturated * (0.65 + 0.3 * controls[-1])).astype(np.float32))


async def seed_sample_dataset(
    settings: Settings,
    *,
    source: Path,
    amp_id: str,
    amp_name: str,
    amp_type: str,
    chunk_count: int = 10,
    chunk_seconds: float = 5.0,
    positions: Sequence[PositionMatrix] | None = None,
    wet_sources: Sequence[Path] | None = None,
) -> None:
    """Create idempotent, frame-aligned dry/reference objects and manifest rows."""
    selected_positions = tuple(positions or default_positions())
    if wet_sources is not None and len(wet_sources) != len(selected_positions):
        msg = "wet_sources must contain one aligned file per position"
        raise ValueError(msg)
    sample_rate = 48_000
    dry_audio = await asyncio.to_thread(_load_mono, source, sample_rate)
    wet_audio = (
        tuple(
            await asyncio.gather(
                *(asyncio.to_thread(_load_mono, path, sample_rate) for path in wet_sources)
            )
        )
        if wet_sources is not None
        else None
    )
    chunk_samples = round(chunk_seconds * sample_rate)
    if len(dry_audio) < chunk_samples:
        msg = "source audio is shorter than one requested chunk"
        raise ValueError(msg)
    if wet_audio is not None and any(len(value) < len(dry_audio) for value in wet_audio):
        msg = "aligned wet sources must be at least as long as the dry source"
        raise ValueError(msg)
    max_start = len(dry_audio) - chunk_samples
    starts = np.linspace(0, max_start, chunk_count, dtype=np.int64)

    database = Database(settings.database_url)
    await database.initialize()
    storage = create_storage(settings)
    try:
        async with database.session() as session:
            amp = await session.get(Amp, amp_id)
            if amp is None:
                amp = Amp(
                    id=amp_id,
                    name=amp_name,
                    amp_type=amp_type,
                    control_names=["volume", "bright", "bass", "middle", "treble", "master"],
                )
                session.add(amp)
            else:
                amp.name = amp_name
                amp.amp_type = amp_type

        for chunk_index, start_value in enumerate(starts):
            start = int(start_value)
            stop = start + chunk_samples
            dry_bytes = await asyncio.to_thread(_wav_bytes, dry_audio[start:stop], sample_rate)
            dry_key = f"amps/{amp_id}/dry/chunk-{chunk_index + 1:02d}.wav"
            await storage.put(dry_key, dry_bytes)
            dry_sha256 = hashlib.sha256(dry_bytes).hexdigest()

            for position_index, position in enumerate(selected_positions):
                reference = (
                    wet_audio[position_index][start:stop]
                    if wet_audio is not None
                    else _placeholder_wet(dry_audio[start:stop], position)
                )
                wet_bytes = await asyncio.to_thread(_wav_bytes, reference, sample_rate)
                reference_key = (
                    f"amps/{amp_id}/reference/chunk-{chunk_index + 1:02d}"
                    f"-position-{position_index + 1:02d}.wav"
                )
                await storage.put(reference_key, wet_bytes)
                case_id = f"{amp_id}:chunk-{chunk_index + 1:02d}:position-{position_index + 1:02d}"
                async with database.session() as session:
                    benchmark_case = await session.scalar(
                        select(BenchmarkCase).where(BenchmarkCase.id == case_id)
                    )
                    values = {
                        "amp_id": amp_id,
                        "chunk_index": chunk_index,
                        "position_index": position_index,
                        "position_matrix": [list(row) for row in position],
                        "dry_key": dry_key,
                        "dry_sha256": dry_sha256,
                        "reference_wet_key": reference_key,
                        "duration_seconds": chunk_seconds,
                        "sample_rate": sample_rate,
                    }
                    if benchmark_case is None:
                        session.add(BenchmarkCase(id=case_id, **values))
                    else:
                        for key, value in values.items():
                            setattr(benchmark_case, key, value)
    finally:
        await database.close()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the Top Arena sample dataset")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--wet", type=Path, action="append", default=[])
    parser.add_argument("--amp-id", default="D3D21964-8E80-11EE-B9D1-0242AC120002")
    parser.add_argument("--amp-name", default="Blackface 63")
    parser.add_argument("--amp-type", default="guitar")
    parser.add_argument("--chunks", type=int, default=10)
    parser.add_argument("--chunk-seconds", type=float, default=5.0)
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    asyncio.run(
        seed_sample_dataset(
            Settings(),
            source=arguments.source,
            amp_id=arguments.amp_id,
            amp_name=arguments.amp_name,
            amp_type=arguments.amp_type,
            chunk_count=arguments.chunks,
            chunk_seconds=arguments.chunk_seconds,
            wet_sources=tuple(arguments.wet) or None,
        )
    )
