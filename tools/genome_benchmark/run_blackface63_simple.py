from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import threading
import time
import traceback
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from pedalboard import load_plugin
from top_arena import benchmark
from top_arena._models import PipelineOptions

from tools.genome_benchmark.renderer import GenomeParadexHost

AMP_ID = "blackface63-simple"
SUPPORTED_AMP_IDS = (AMP_ID, "blackface63-simple-quiet")
PLUGIN_PATH = Path("/Library/Audio/Plug-Ins/VST3/Genome.vst3")
MODEL_PATH = Path.home() / "Documents/blackface63.ampnet"
GENOME_MODEL_PATH = (
    Path.home() / "Documents/Two notes Audio Engineering/PARADEX Models/blackface63.ampnet"
)
PRESET_PATH = Path(__file__).resolve().parent / "presets/blackface63.vstpreset"
DEFAULT_OUTPUT_ROOT = Path.cwd() / ".top-arena/genome-blackface63-simple"
DEFAULT_OUTPUT_BOOST_DB = 5.7


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_runtime_files(plugin_path: Path, model_path: Path, preset_path: Path) -> None:
    plugin_binary = plugin_path / "Contents/MacOS/Genome"
    for label, path in (
        ("Genome plugin", plugin_binary),
        ("local AmpNet model", model_path),
        ("Genome benchmark preset", preset_path),
        ("Genome PARADEX model copy", GENOME_MODEL_PATH),
    ):
        if not path.is_file():
            msg = f"{label} does not exist: {path}"
            raise FileNotFoundError(msg)
    if _sha256(model_path) != _sha256(GENOME_MODEL_PATH):
        msg = "Genome's PARADEX model copy does not match ~/Documents/blackface63.ampnet"
        raise RuntimeError(msg)


def _wait_for_genome(host: GenomeParadexHost, timeout_seconds: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if all(abs(value - 0.5) < 1e-4 for value in host.automation_values()):
            time.sleep(1.5)
            return
        time.sleep(0.1)
    msg = "Genome did not initialize the four PARADEX automation slots"
    raise RuntimeError(msg)


def _combined_output_gain(base_gain: float, boost_db: float) -> float:
    if not math.isfinite(base_gain) or base_gain <= 0.0:
        msg = "--output-gain must be finite and greater than zero"
        raise ValueError(msg)
    if not math.isfinite(boost_db):
        msg = "--output-boost-db must be finite"
        raise ValueError(msg)
    return base_gain * 10.0 ** (boost_db / 20.0)


def _parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the local blackface63 AmpNet through Genome PARADEX."
    )
    parser.add_argument("--creator", default="qforge-dev")
    parser.add_argument("--amp-id", choices=SUPPORTED_AMP_IDS, default=AMP_ID)
    parser.add_argument("--name")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--output-gain", type=float, default=0.9)
    parser.add_argument("--output-boost-db", type=float, default=DEFAULT_OUTPUT_BOOST_DB)
    parser.add_argument("--server-url")
    parser.add_argument(
        "--training-positions-file",
        type=Path,
        required=True,
        help="JSON array containing every normalized training control position",
    )
    parser.add_argument(
        "--training-dry-files-file",
        type=Path,
        required=True,
        help="JSON array containing every dry-file identifier used for training",
    )
    parser.add_argument("--audio-duration-sum", type=float, default=1900.0)
    parser.add_argument("--turns", type=int, default=1)
    parser.add_argument("--training-time", type=float, default=0.0)
    parser.add_argument("--parameter-count", type=int, default=0)
    return parser.parse_args(arguments)


def main() -> None:
    arguments = _parse_arguments()
    _require_runtime_files(PLUGIN_PATH, MODEL_PATH, PRESET_PATH)
    training_positions = json.loads(arguments.training_positions_file.read_text())
    training_dry_files = json.loads(arguments.training_dry_files_file.read_text())
    if not isinstance(training_positions, list) or not isinstance(training_dry_files, list):
        msg = "training provenance files must each contain a JSON array"
        raise TypeError(msg)

    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = arguments.output_root.expanduser().resolve() / timestamp
    output_dir.mkdir(parents=True, exist_ok=False)
    run_name = arguments.name or f"genome-{arguments.amp_id}-{timestamp.lower()}"
    plugin = load_plugin(str(PLUGIN_PATH))
    plugin.preset_data = PRESET_PATH.read_bytes()
    combined_output_gain = _combined_output_gain(
        arguments.output_gain,
        arguments.output_boost_db,
    )
    host = GenomeParadexHost(plugin, output_dir / "wet", output_gain=combined_output_gain)
    run = benchmark.create(
        name=run_name,
        creator=arguments.creator,
        training_positions=training_positions,
        training_dry_files=training_dry_files,
        audio_duration_sum=arguments.audio_duration_sum,
        turns=arguments.turns,
        training_time=arguments.training_time,
        description=(
            "Local Two notes Genome PARADEX render of blackface63.ampnet. "
            "Volume, Bass, Middle, and Treble are automated; Reverb=0, Master=0.5, "
            f"and Bright=0 are fixed by the {arguments.amp_id} benchmark. "
            f"The renderer applies {arguments.output_boost_db:+.2f} dB after a base "
            f"gain of {arguments.output_gain:.6g}."
        ),
        parameter_count=arguments.parameter_count,
        amp_control_count=4,
        server_url=arguments.server_url,
        options=PipelineOptions(
            run_concurrency=1,
            report_format="text",
            show_progress=True,
        ),
    )
    launch_record = {
        "amp_id": arguments.amp_id,
        "model": str(MODEL_PATH),
        "model_sha256": _sha256(MODEL_PATH),
        "genome_model": str(GENOME_MODEL_PATH),
        "plugin": str(PLUGIN_PATH),
        "preset": str(PRESET_PATH),
        "preset_sha256": _sha256(PRESET_PATH),
        "run_name": run_name,
        "base_output_gain": arguments.output_gain,
        "output_boost_db": arguments.output_boost_db,
        "combined_output_gain": combined_output_gain,
        "metadata": asdict(run.metadata),
    }
    (output_dir / "launch.json").write_text(
        json.dumps(launch_record, indent=2, sort_keys=True) + "\n"
    )

    def run_benchmark() -> None:
        try:
            _wait_for_genome(host)
            result = run.run(arguments.amp_id, host.render)
            (output_dir / "result.json").write_text(
                json.dumps(asdict(result), indent=2, sort_keys=True) + "\n"
            )
        except Exception:  # noqa: BLE001 - process boundary must persist every failure
            failure = traceback.format_exc()
            (output_dir / "failure.txt").write_text(failure)
            print(failure, file=sys.stderr, flush=True)  # noqa: T201
            os._exit(1)
        print(f"Benchmark result: {output_dir / 'result.json'}", flush=True)  # noqa: T201
        sys.stdout.flush()
        os._exit(0)

    thread = threading.Thread(target=run_benchmark, daemon=True)
    thread.start()
    plugin.show_editor()
    if thread.is_alive():
        msg = "Genome editor closed before the benchmark completed"
        raise RuntimeError(msg)


if __name__ == "__main__":
    main()
