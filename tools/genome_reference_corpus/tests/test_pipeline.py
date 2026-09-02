from __future__ import annotations

from pathlib import Path

import pytest

from tools.genome_reference_corpus.config import GenomeCorpusConfig
from tools.genome_reference_corpus.pipeline import (
    build_render_job,
    build_training_job,
    prepare_model_preset,
)


def _amp() -> dict:
    controls = ["Presence", "Master", "Treble", "Middle", "Bass", "Gain"]
    return {
        "amp_id": "genome-test-simple",
        "amp_name": "genome-test-simple",
        "renderer_model": "Test Model",
        "reference_renderer": "genome-paradex",
        "reference_output_gain": 0.9,
        "controls": [{"name": name} for name in controls],
        "positions": [
            {
                "position_id": "position-01",
                "values": dict.fromkeys(controls, 0.5),
                "vector": [0.5] * 6,
            }
        ],
    }


def test_render_job_contains_benchmark_and_training_tasks(tmp_path: Path) -> None:
    config = GenomeCorpusConfig(root=tmp_path, presets=tmp_path / "presets")
    dry_manifest = {
        "sounds": [
            {"sound_id": "sound-01", "file": "dry/sound-01.flac"},
            {"sound_id": "sound-02", "file": "dry/sound-02.flac"},
        ]
    }

    job = build_render_job(config, _amp(), dry_manifest, sound_limit=1, position_limit=1)

    assert job["amp"]["renderer_model"] == "Test Model"
    assert job["output_gain"] == 0.9
    assert [task["kind"] for task in job["tasks"]] == ["benchmark", "training"]
    assert job["tasks"][0]["object_key"].endswith(
        "/wet/genome-test-simple/position-01/sound-01.flac"
    )
    assert job["tasks"][1]["object_key"].endswith(
        "/training/wet/genome-test-simple/position-01.flac"
    )
    assert job["preset"].endswith("/presets/genome-test-simple.vstpreset")


def test_prepare_model_preset_rejects_a_preset_captured_for_another_model(
    tmp_path: Path,
) -> None:
    presets = tmp_path / "source-presets"
    models = tmp_path / "models"
    presets.mkdir()
    models.mkdir()
    template = (
        Path(__file__).resolve().parents[1] / "presets" / "genome-fried-r50-dirty-simple.vstpreset"
    )
    (presets / "genome-test-simple.vstpreset").write_bytes(template.read_bytes())
    model = models / "Test Model.ampnet"
    model.write_bytes(b"ampnet")
    config = GenomeCorpusConfig(root=tmp_path, presets=presets, models=models)

    with pytest.raises(ValueError, match=r"does not select Test Model\.ampnet"):
        prepare_model_preset(config, _amp())


def test_training_job_marks_genome_as_reference_renderer(tmp_path: Path) -> None:
    config = GenomeCorpusConfig(root=tmp_path)
    amp = _amp()
    report = {
        "object_key": f"{config.nam_prefix}/training/wet/genome-test-simple/position-01.flac",
        "sha256": "wet-sha",
        "peak": 0.8,
        "clipped_samples": 0,
        "frames": 9_120_000,
        "sample_rate": 48_000,
    }

    job = build_training_job(config, amp, amp["positions"][0], report, dry_sha256="dry-sha")

    assert job["format"] == "top-arena.nam-a2-full-job.v1"
    assert job["reference_renderer"] == "genome-paradex"
    assert job["benchmark_reference_prefix"].endswith("/wet/genome-test-simple/position-01")
    assert job["output_root"].endswith("/models/genome-test-simple/position-01")
