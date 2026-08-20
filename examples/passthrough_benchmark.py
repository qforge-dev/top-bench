from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from top_arena import PositionMatrix, benchmark

AMP_ID = "D3D21964-8E80-11EE-B9D1-0242AC120002"


def main() -> None:
    sequence = count()
    with TemporaryDirectory(prefix="top-arena-smoke-") as temporary:
        output_directory = Path(temporary)

        def passthrough(dry_audio: Path, positions: PositionMatrix) -> Path:
            del positions
            output = output_directory / f"wet-{next(sequence):03d}.wav"
            shutil.copyfile(dry_audio, output)
            return output

        run = benchmark.create(
            name=f"passthrough-{datetime.now(UTC):%Y%m%d-%H%M%S}",
            creator=os.environ.get("USER", "anonymous"),
            unique_positions_used=1,
            audio_duration_sum=250.0,
            turns=1,
            training_time=0.0,
            description="Identity callback used to verify the full benchmark pipeline.",
            parameter_count=0,
            server_url=os.environ.get(
                "TOP_ARENA_SERVER_URL",
                "https://top-arena.54-90-214-165.sslip.io",
            ),
        )
        result = run.run(AMP_ID, passthrough)
        print(result)  # noqa: T201


if __name__ == "__main__":
    main()
