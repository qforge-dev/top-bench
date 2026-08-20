from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol, cast

import boto3
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from top_arena_server.database import Database
from top_arena_server.models import Amp, BenchmarkCase, BenchmarkRun

LOGGER = logging.getLogger(__name__)
POSITION_COUNT = 10
SOUND_COUNT = 5
REFERENCE_LATENCY_SAMPLES = 9


class S3Like(Protocol):
    def get_object(
        self, *, Bucket: str, Key: str  # noqa: N803
    ) -> dict[str, Any]: ...

    def get_paginator(self, operation_name: str) -> Any: ...  # noqa: ANN401


@dataclass(frozen=True, slots=True)
class CaseSpec:
    id: str
    chunk_index: int
    position_index: int
    position_matrix: list[list[float]]
    dry_key: str
    dry_sha256: str
    reference_wet_key: str
    nam_reference_wet_key: str
    duration_seconds: float
    sample_rate: int


@dataclass(frozen=True, slots=True)
class AmpSpec:
    id: str
    name: str
    control_names: list[str]
    cases: list[CaseSpec]


class DatasetActivator:
    def __init__(
        self,
        *,
        database: Database,
        s3: S3Like,
        bucket: str,
        prefix: str,
    ) -> None:
        self.database = database
        self.s3 = s3
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")

    def _json(self, key: str) -> dict[str, Any]:
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"].read()
        value = json.loads(body)
        if not isinstance(value, dict):
            msg = f"expected JSON object at s3://{self.bucket}/{key}"
            raise TypeError(msg)
        return cast("dict[str, Any]", value)

    def _keys(self, prefix: str) -> set[str]:
        paginator = self.s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket, Prefix=prefix)
        return {
            str(item["Key"])
            for page in pages
            for item in page.get("Contents", [])
        }

    def _amp_manifest_keys(self) -> list[str]:
        prefix = f"{self.prefix}/amps/"
        return sorted(
            key for key in self._keys(prefix) if key.endswith("/positions.json")
        )

    def _build_if_ready(
        self,
        manifest_key: str,
        dry_manifest: dict[str, Any],
    ) -> AmpSpec | None:
        amp_manifest = self._json(manifest_key)
        amp_id = str(amp_manifest["amp_id"])
        positions = cast("list[dict[str, Any]]", amp_manifest["positions"])
        if len(positions) != POSITION_COUNT:
            msg = f"{amp_id} has {len(positions)} positions, expected {POSITION_COUNT}"
            raise ValueError(msg)

        nam_root = f"{self.prefix}/nam-a2-full/v1/models/{amp_id}"
        wet_root = f"{self.prefix}/wet/{amp_id}"
        nam_keys = self._keys(f"{nam_root}/")
        wet_keys = self._keys(f"{wet_root}/")
        metadata_keys = [
            f"{nam_root}/position-{index:02d}/metadata.json"
            for index in range(1, POSITION_COUNT + 1)
        ]
        required_wet_keys = {
            f"{wet_root}/position-{position:02d}/sound-{sound:02d}.flac"
            for position in range(1, POSITION_COUNT + 1)
            for sound in range(1, SOUND_COUNT + 1)
        }
        if not set(metadata_keys).issubset(nam_keys) or not required_wet_keys.issubset(wet_keys):
            return None

        sounds = cast("list[dict[str, Any]]", dry_manifest["sounds"])
        selected_sounds = sounds[:SOUND_COUNT]
        if len(selected_sounds) != SOUND_COUNT:
            msg = f"dry manifest has fewer than {SOUND_COUNT} sounds"
            raise ValueError(msg)
        metadata_by_position = {
            index: self._json(key) for index, key in enumerate(metadata_keys, start=1)
        }
        cases: list[CaseSpec] = []
        for chunk_index, sound in enumerate(selected_sounds):
            sound_id = str(sound["sound_id"])
            dry_key = f"{self.prefix}/{sound['file']}"
            for position_index, position in enumerate(positions):
                position_number = position_index + 1
                position_id = f"position-{position_number:02d}"
                metadata_cases = cast(
                    "list[dict[str, Any]]", metadata_by_position[position_number]["cases"]
                )
                metadata_case = next(
                    (item for item in metadata_cases if item["sound_id"] == sound_id),
                    None,
                )
                if metadata_case is None:
                    msg = f"{amp_id}/{position_id} has no {sound_id} NAM output"
                    raise ValueError(msg)
                bias_key = str(metadata_case["bias_reference_key"])
                nam_key = str(metadata_case["nam_a2_full_key"])
                if bias_key not in wet_keys or nam_key not in nam_keys:
                    return None
                cases.append(
                    CaseSpec(
                        id=f"v1-{amp_id}-{sound_id}-{position_id}",
                        chunk_index=chunk_index,
                        position_index=position_index,
                        position_matrix=[
                            [float(value) for value in cast("list[float]", position["vector"])]
                        ],
                        dry_key=dry_key,
                        dry_sha256=str(sound["sha256"]),
                        reference_wet_key=bias_key,
                        nam_reference_wet_key=nam_key,
                        duration_seconds=float(sound["duration_seconds"]),
                        sample_rate=int(sound["sample_rate"]),
                    )
                )
        controls = cast("list[dict[str, Any]]", amp_manifest["controls"])
        return AmpSpec(
            id=amp_id,
            name=str(amp_manifest["amp_name"]),
            control_names=[str(control["name"]) for control in controls],
            cases=cases,
        )

    @staticmethod
    async def activate(session: AsyncSession, spec: AmpSpec) -> bool:
        amp = await session.get(Amp, spec.id)
        existing_case_ids = set(
            await session.scalars(select(BenchmarkCase.id).where(BenchmarkCase.amp_id == spec.id))
        )
        target_case_ids = {case.id for case in spec.cases}
        if amp is not None and existing_case_ids == target_case_ids:
            return False

        if amp is not None and existing_case_ids:
            run_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(BenchmarkRun)
                    .where(BenchmarkRun.amp_id == spec.id)
                )
                or 0
            )
            if run_count:
                archive_id = f"{spec.id}:starter-v1"
                archive = await session.get(Amp, archive_id)
                if archive is None:
                    session.add(
                        Amp(
                            id=archive_id,
                            name=f"{amp.name} (starter dataset)",
                            amp_type=amp.amp_type,
                            control_names=amp.control_names,
                        )
                    )
                    await session.flush()
                await session.execute(
                    update(BenchmarkCase)
                    .where(BenchmarkCase.amp_id == spec.id)
                    .values(amp_id=archive_id)
                )
                await session.execute(
                    update(BenchmarkRun)
                    .where(BenchmarkRun.amp_id == spec.id)
                    .values(amp_id=archive_id)
                )
            else:
                await session.execute(delete(BenchmarkCase).where(BenchmarkCase.amp_id == spec.id))
            await session.execute(delete(Amp).where(Amp.id == spec.id))
            await session.flush()
            amp = None

        if amp is None:
            session.add(
                Amp(
                    id=spec.id,
                    name=spec.name,
                    amp_type="guitar",
                    control_names=spec.control_names,
                )
            )
        else:
            amp.name = spec.name
            amp.amp_type = "guitar"
            amp.control_names = spec.control_names
        session.add_all(
            BenchmarkCase(
                id=case.id,
                amp_id=spec.id,
                chunk_index=case.chunk_index,
                position_index=case.position_index,
                position_matrix=case.position_matrix,
                dry_key=case.dry_key,
                dry_sha256=case.dry_sha256,
                reference_wet_key=case.reference_wet_key,
                reference_latency_samples=REFERENCE_LATENCY_SAMPLES,
                nam_reference_wet_key=case.nam_reference_wet_key,
                duration_seconds=case.duration_seconds,
                sample_rate=case.sample_rate,
            )
            for case in spec.cases
        )
        return True

    async def scan_once(self) -> list[str]:
        dry_manifest = await asyncio.to_thread(
            self._json, f"{self.prefix}/manifests/dry.json"
        )
        manifest_keys = await asyncio.to_thread(self._amp_manifest_keys)
        activated: list[str] = []
        for manifest_key in manifest_keys:
            try:
                spec = await asyncio.to_thread(self._build_if_ready, manifest_key, dry_manifest)
                if spec is None:
                    continue
                async with self.database.session() as session:
                    changed = await self.activate(session, spec)
                if changed:
                    activated.append(spec.id)
                    LOGGER.info("activated %s with %d cases", spec.id, len(spec.cases))
            except Exception:
                LOGGER.exception("activation scan failed for %s", manifest_key)
        return activated


async def run_forever(activator: DatasetActivator, poll_seconds: float) -> None:
    while True:
        activated = await activator.scan_once()
        LOGGER.info("activation scan complete; activated=%d", len(activated))
        await asyncio.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Activate complete TOP Arena NAM datasets")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    arguments = parser.parse_args()
    database_url = os.environ.get("TOP_ARENA_DATABASE_URL")
    if not database_url:
        parser.error("TOP_ARENA_DATABASE_URL is required")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(threadName)s %(levelname)s %(message)s",
    )
    database = Database(database_url)
    activator = DatasetActivator(
        database=database,
        s3=cast("S3Like", boto3.client("s3")),
        bucket=arguments.bucket,
        prefix=arguments.prefix,
    )
    try:
        asyncio.run(run_forever(activator, arguments.poll_seconds))
    finally:
        asyncio.run(database.close())


if __name__ == "__main__":
    main()
