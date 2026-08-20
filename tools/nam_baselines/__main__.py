from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from .config import NamBaselineConfig
from .producer import produce

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build TOP Arena NAM A2 Full baselines")
    subparsers = parser.add_subparsers(dest="command", required=True)

    producer = subparsers.add_parser("produce")
    producer.add_argument("--amp", action="append", required=True)
    producer.add_argument("--amp-limit", type=int)
    producer.add_argument("--position-limit", type=int)

    orchestrate = subparsers.add_parser("orchestrate")
    orchestrate.add_argument("--resume-reference-corpus", action="store_true")

    launch = subparsers.add_parser("launch-producer")
    launch.add_argument("--log", type=Path)
    launch.add_argument("--resume-reference-corpus", action="store_true")
    return parser


def _orchestrate(config: NamBaselineConfig, *, resume_reference_corpus: bool) -> None:
    producer = [sys.executable, "-m", "tools.nam_baselines", "produce", "--amp", "all"]
    subprocess.run(producer, check=True)  # noqa: S603
    if resume_reference_corpus:
        reference = [
            sys.executable,
            "-m",
            "tools.reference_corpus",
            "render",
            "--amp",
            "all",
            "--upload-workers",
            str(config.corpus.upload_workers),
            "--max-pending-uploads",
            str(config.corpus.max_pending_uploads),
        ]
        subprocess.run(reference, check=True)  # noqa: S603


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    arguments = _parser().parse_args()
    config = NamBaselineConfig()
    if arguments.command == "produce":
        produce(
            config,
            arguments.amp,
            amp_limit=arguments.amp_limit,
            position_limit=arguments.position_limit,
        )
    elif arguments.command == "orchestrate":
        _orchestrate(
            config,
            resume_reference_corpus=arguments.resume_reference_corpus,
        )
    elif arguments.command == "launch-producer":
        log = arguments.log or config.root / "logs" / "producer.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "tools.nam_baselines", "orchestrate"]
        if arguments.resume_reference_corpus:
            command.append("--resume-reference-corpus")
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
        pid_path = config.root / "state" / "producer.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(f"{process.pid}\n")
        LOGGER.info(
            "launched NAM producer: %s",
            json.dumps({"pid": process.pid, "log": str(log), "command": command}),
        )


if __name__ == "__main__":
    main()
