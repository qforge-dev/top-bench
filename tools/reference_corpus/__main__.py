from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from .audio import prepare_dry, prepare_loop_dry
from .config import DEFAULT_LOOP_SELECTION, DEFAULT_LOOP_SOURCE, CorpusConfig
from .query import query_candidates
from .render import generate_benchmark_manifest, render
from .settings import generate_settings

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and render TOP Arena's BIAS X corpus")
    parser.add_argument("--root", type=Path, help="override the ignored local corpus directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("query-candidates")

    prepare = subparsers.add_parser("prepare-dry")
    prepare.add_argument("--candidates", type=Path, required=True)
    prepare.add_argument("--no-upload", action="store_true")

    loops = subparsers.add_parser("prepare-loops")
    loops.add_argument("--source", type=Path, default=DEFAULT_LOOP_SOURCE)
    loops.add_argument("--selection", default=",".join(DEFAULT_LOOP_SELECTION))
    loops.add_argument("--no-upload", action="store_true")

    positions = subparsers.add_parser("generate-settings")
    positions.add_argument("--no-upload", action="store_true")

    rendering = subparsers.add_parser("render")
    rendering.add_argument("--amp", action="append", required=True)
    rendering.add_argument("--sound-limit", type=int)
    rendering.add_argument("--position-limit", type=int)
    rendering.add_argument("--verify-s3", action="store_true")
    rendering.add_argument("--upload-workers", type=int)
    rendering.add_argument("--max-pending-uploads", type=int)

    subparsers.add_parser("benchmark-manifest")

    launch = subparsers.add_parser("launch")
    launch.add_argument("--amp", action="append")
    launch.add_argument("--log", type=Path)
    launch.add_argument("--upload-workers", type=int, default=8)
    launch.add_argument("--max-pending-uploads", type=int, default=32)
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    arguments = _parser().parse_args()
    config = (
        CorpusConfig(root=arguments.root.expanduser().resolve())
        if arguments.root
        else CorpusConfig()
    )
    if arguments.command == "query-candidates":
        destination = config.root / "source" / "athena-candidates.csv"
        query_candidates(destination)
    elif arguments.command == "prepare-dry":
        path = prepare_dry(config, arguments.candidates, upload=not arguments.no_upload)
        LOGGER.info("dry manifest: %s", path)
    elif arguments.command == "prepare-loops":
        selection = tuple(
            value.strip() for value in arguments.selection.split(",") if value.strip()
        )
        path = prepare_loop_dry(
            config,
            arguments.source.expanduser().resolve(),
            selection,
            upload=not arguments.no_upload,
        )
        LOGGER.info("dry manifest: %s", path)
    elif arguments.command == "generate-settings":
        path = generate_settings(config, upload=not arguments.no_upload)
        LOGGER.info("amp settings: %s", path)
    elif arguments.command == "render":
        render(
            config,
            arguments.amp,
            sound_limit=arguments.sound_limit,
            position_limit=arguments.position_limit,
            verify_s3=arguments.verify_s3,
            upload_workers=arguments.upload_workers,
            max_pending_uploads=arguments.max_pending_uploads,
        )
    elif arguments.command == "benchmark-manifest":
        LOGGER.info("benchmark manifest: %s", generate_benchmark_manifest(config))
    elif arguments.command == "launch":
        log = arguments.log or config.root / "logs" / "full-render.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "tools.reference_corpus",
            "--root",
            str(config.root),
            "render",
        ]
        for amp in arguments.amp or ["all"]:
            command.extend(("--amp", amp))
        command.extend(("--upload-workers", str(arguments.upload_workers)))
        command.extend(("--max-pending-uploads", str(arguments.max_pending_uploads)))
        with log.open("ab", buffering=0) as output:
            process = subprocess.Popen(  # noqa: S603
                command,
                cwd=Path(__file__).resolve().parents[2],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=os.environ.copy(),
            )
        pid_path = config.root / "state" / "full-render.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(f"{process.pid}\n")
        LOGGER.info(
            "launched detached render: %s",
            json.dumps({"pid": process.pid, "log": str(log), "command": command}),
        )


if __name__ == "__main__":
    main()
