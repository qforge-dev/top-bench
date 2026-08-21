from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from top_arena_server.database import Database
from top_arena_server.models import Amp, BenchmarkCase, BenchmarkRun

from tools.nam_baselines.activate import AmpSpec, CaseSpec, DatasetActivator


def test_storage_keys_are_relative_to_the_server_storage_prefix() -> None:
    activator = DatasetActivator(
        database=object(),  # type: ignore[arg-type]
        s3=object(),  # type: ignore[arg-type]
        bucket="bucket",
        prefix="parametric-amplifier/public/top-arena/reference-corpus/v1",
        storage_prefix="parametric-amplifier/public/top-arena",
    )

    assert (
        activator._storage_key(  # noqa: SLF001
            "parametric-amplifier/public/top-arena/reference-corpus/v1/dry/sound-01.flac"
        )
        == "reference-corpus/v1/dry/sound-01.flac"
    )


def _amp_spec(amp_id: str) -> AmpSpec:
    return AmpSpec(
        id=amp_id,
        name="Production Amp",
        control_names=["Volume", "Bass"],
        cases=[
            CaseSpec(
                id=f"v1-{amp_id}-sound-{sound + 1:02d}-position-{position + 1:02d}",
                chunk_index=sound,
                position_index=position,
                position_matrix=[[0.1, 0.2]],
                dry_key=f"corpus/dry/sound-{sound + 1:02d}.flac",
                dry_sha256="0" * 64,
                reference_wet_key=(
                    f"corpus/wet/{amp_id}/position-{position + 1:02d}/sound-{sound + 1:02d}.flac"
                ),
                nam_reference_wet_key=(
                    f"corpus/nam/{amp_id}/position-{position + 1:02d}/sound-{sound + 1:02d}.flac"
                ),
                duration_seconds=15.0,
                sample_rate=48_000,
            )
            for sound in range(5)
            for position in range(10)
        ],
    )


async def test_activation_preserves_a_starter_run_and_is_idempotent(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'activation.db'}")
    await database.initialize()
    amp_id = "production-amp"
    old_case_id = "starter-case"
    async with database.session() as session:
        session.add(Amp(id=amp_id, name="Starter Amp", amp_type="guitar", control_names=[]))
        session.add(
            BenchmarkCase(
                id=old_case_id,
                amp_id=amp_id,
                chunk_index=0,
                position_index=0,
                position_matrix=[[0.5]],
                dry_key="starter/dry.flac",
                dry_sha256="1" * 64,
                reference_wet_key="starter/reference.flac",
                reference_latency_samples=0,
                nam_reference_wet_key=None,
                duration_seconds=5.0,
                sample_rate=48_000,
            )
        )
        session.add(
            BenchmarkRun(
                id="starter-run",
                name="starter-run",
                creator="tests",
                amp_id=amp_id,
                unique_positions_used=1,
                audio_duration_sum=5.0,
                turns=1,
                training_time=1.0,
                description="starter",
                parameter_count=1,
                status="completed",
                client_finished=True,
                total_cases=1,
                completed_cases=1,
                metrics={},
            )
        )

    spec = _amp_spec(amp_id)
    async with database.session() as session:
        assert await DatasetActivator.activate(session, spec)
    async with database.session() as session:
        assert not await DatasetActivator.activate(session, spec)

    async with database.session() as session:
        run = await session.get(BenchmarkRun, "starter-run")
        old_case = await session.get(BenchmarkCase, old_case_id)
        production_amp = await session.get(Amp, amp_id)
        production_case_count = await session.scalar(
            select(func.count()).select_from(BenchmarkCase).where(BenchmarkCase.amp_id == amp_id)
        )
        assert run is not None
        assert old_case is not None
        assert production_amp is not None
        assert run.amp_id == f"{amp_id}:starter-v1"
        assert old_case.amp_id == f"{amp_id}:starter-v1"
        assert production_amp.name == "Production Amp"
        assert production_case_count == 50
        production_case = await session.get(BenchmarkCase, f"v1-{amp_id}-sound-01-position-01")
        assert production_case is not None
        assert production_case.reference_latency_samples == 9
        assert production_case.nam_reference_wet_key is not None
    await database.close()
