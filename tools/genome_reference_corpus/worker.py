from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from tools.genome_reference_corpus.renderer import (
    GenomeReferenceRenderer,
    _Plugin,
    automation_slots_ready,
)
from tools.reference_corpus.process import sha256, write_json

MODEL_LOAD_SECONDS = 8.0


def _render_when_ready(
    renderer: GenomeReferenceRenderer,
    task: dict,
    *,
    timeout_seconds: float = 20.0,
) -> dict:
    """Render after Genome's asynchronously loaded AmpNet DSP becomes active."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return renderer.render_file(
                Path(task["source"]),
                Path(task["destination"]),
                tuple(float(value) for value in task["controls"]),
            )
        except RuntimeError as error:
            if "Genome returned dry passthrough" not in str(error):
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def _render_job(job: dict, plugin: _Plugin) -> None:
    renderer = GenomeReferenceRenderer(plugin, output_gain=float(job["output_gain"]))
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if automation_slots_ready(plugin):
            break
        time.sleep(0.1)
    else:
        msg = "Genome did not expose its six PARADEX automation slots"
        raise RuntimeError(msg)
    # Genome publishes its automation parameters before the selected AmpNet DSP
    # is active.  A short wait can therefore produce a deterministic fallback
    # render that is neither bit-identical dry audio nor the selected model.
    time.sleep(MODEL_LOAD_SECONDS)
    reports = []
    for index, task in enumerate(job["tasks"], start=1):
        report = (
            _render_when_ready(renderer, task)
            if index == 1
            else renderer.render_file(
                Path(task["source"]),
                Path(task["destination"]),
                tuple(float(value) for value in task["controls"]),
            )
        )
        reports.append({**task, **report})
        print(  # noqa: T201
            f"Genome {index}/{len(job['tasks'])}: {task['kind']} "
            f"{task['position_id']} {task.get('sound_id', '')}",
            flush=True,
        )
    write_json(
        Path(job["manifest"]),
        {
            "format": "top-arena.genome-reference-renders.v1",
            "amp_id": job["amp"]["amp_id"],
            "amp_name": job["amp"]["amp_name"],
            "renderer": "genome-paradex",
            "renderer_model": job["amp"]["renderer_model"],
            "model": job["model"],
            "model_sha256": sha256(Path(job["model"])),
            "plugin": job["plugin"],
            "plugin_sha256": sha256(Path(job["plugin"]) / "Contents/MacOS/Genome"),
            "preset": job["preset"],
            "preset_sha256": sha256(Path(job["preset"])),
            "output_gain": job["output_gain"],
            "reports": reports,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    arguments = parser.parse_args()
    job = json.loads(arguments.job.read_text())
    from pedalboard import load_plugin  # noqa: PLC0415

    plugin = load_plugin(job["plugin"])
    plugin.preset_data = Path(job["preset"]).read_bytes()

    def run() -> None:
        try:
            _render_job(job, plugin)
        except BaseException:  # noqa: BLE001
            traceback.print_exc()
            sys.stderr.flush()
            os._exit(1)
        sys.stdout.flush()
        os._exit(0)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    plugin.show_editor()
    if thread.is_alive():
        msg = "Genome editor closed before corpus rendering completed"
        raise RuntimeError(msg)


if __name__ == "__main__":
    main()
