from __future__ import annotations

import argparse
import os
import shutil
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from top_arena import PipelineOptions, PositionMatrix, ReportFormat, benchmark

AMP_ID = "D3D21964-8E80-11EE-B9D1-0242AC120002"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Top Arena passthrough benchmark")
    parser.add_argument(
        "--format",
        choices=("agent", "text", "json", "jsonl", "none"),
        default="agent",
        help="final report format (default: agent)",
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get(
            "TOP_ARENA_SERVER_URL",
            "https://top-arena.labqoat.com",
        ),
    )
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--min-finding-signal", type=float, default=1.0)
    parser.add_argument("--min-evidence-signal", type=float, default=1.0)
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
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
            server_url=arguments.server_url,
            options=PipelineOptions(
                report_format=cast("ReportFormat", arguments.format),
                show_progress=not arguments.no_progress,
                report_min_finding_signal=arguments.min_finding_signal,
                report_min_evidence_signal=arguments.min_evidence_signal,
            ),
        )
        _ = run.run(AMP_ID, passthrough)


if __name__ == "__main__":
    main()
