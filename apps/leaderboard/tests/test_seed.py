from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from top_arena_server.config import Settings
from top_arena_server.database import Database
from top_arena_server.models import BenchmarkCase
from top_arena_server.seed import default_positions, seed_sample_dataset


async def test_sample_dataset_contains_ten_chunks_at_five_positions(tmp_path: Path) -> None:
    source = tmp_path / "190-seconds.wav"
    sample_rate = 8_000
    signal = np.sin(2 * np.pi * 110 * np.arange(sample_rate * 51) / sample_rate).astype(np.float32)
    sf.write(source, signal, sample_rate, subtype="FLOAT")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}",
        storage_backend="filesystem",
        storage_path=tmp_path / "objects",
    )

    await seed_sample_dataset(
        settings,
        source=source,
        amp_id="demo-bias-x",
        amp_name="Demo Bias-X",
        amp_type="guitar",
        chunk_count=10,
        chunk_seconds=5.0,
        positions=default_positions(),
    )

    database = Database(settings.database_url)
    async with database.session() as session:
        cases = (await session.scalars(BenchmarkCase.select_all())).all()

    assert len(cases) == 50
    assert len({case.dry_key for case in cases}) == 10
    assert len({str(case.position_matrix) for case in cases}) == 5
