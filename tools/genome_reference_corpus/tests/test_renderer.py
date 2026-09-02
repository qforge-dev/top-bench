from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tools.genome_reference_corpus.renderer import (
    GenomeReferenceRenderer,
    automation_slots_ready,
    position_controls,
)


class _Parameter:
    def __init__(self) -> None:
        self.raw_value = 0.5


class _Plugin:
    def __init__(self) -> None:
        self.parameters = {f"a{index}": _Parameter() for index in range(1, 7)}
        self.reset_values: list[bool] = []

    def __call__(self, audio, sample_rate, *, buffer_size, reset):
        del sample_rate, buffer_size
        self.reset_values.append(reset)
        gain = sum(self.parameters[f"a{index}"].raw_value for index in range(1, 7)) / 6
        return np.tanh(audio * gain)


class _PassThroughPlugin(_Plugin):
    def __call__(self, audio, sample_rate, *, buffer_size, reset):
        del sample_rate, buffer_size
        self.reset_values.append(reset)
        return audio


def test_automation_slots_are_ready_even_when_genome_initializes_them_at_zero() -> None:
    plugin = _Plugin()
    for parameter in plugin.parameters.values():
        parameter.raw_value = 0.0

    assert automation_slots_ready(plugin)


def test_automation_slots_are_not_ready_until_all_six_are_exposed() -> None:
    plugin = _Plugin()
    del plugin.parameters["a6"]

    assert not automation_slots_ready(plugin)


def test_position_controls_follow_manifest_control_order() -> None:
    names = ("Presence", "Master", "Treble", "Middle", "Bass", "Gain")
    amp = {
        "controls": [{"name": name} for name in names],
    }
    position = {
        "values": dict(zip(names, (0.1, 0.2, 0.3, 0.4, 0.5, 0.6), strict=True)),
        "vector": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
    }

    assert position_controls(amp, position) == (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)


def test_position_controls_reject_vector_disagreement() -> None:
    names = ("Presence", "Master", "Treble", "Middle", "Bass", "Gain")
    amp = {"controls": [{"name": name} for name in names]}
    position = {"values": dict.fromkeys(names, 0.2), "vector": [0.3] * 6}

    with pytest.raises(ValueError, match="position vector"):
        position_controls(amp, position)


def test_renderer_sets_six_slots_resets_dsp_and_writes_pcm24(tmp_path: Path) -> None:
    source = tmp_path / "dry.flac"
    destination = tmp_path / "wet.flac"
    dry = np.linspace(-0.5, 0.5, 480, dtype=np.float32)
    sf.write(source, dry, 48_000, format="FLAC", subtype="PCM_24")
    plugin = _Plugin()
    renderer = GenomeReferenceRenderer(plugin, output_gain=0.9, warmup_seconds=0.01)

    report = renderer.render_file(source, destination, (0.1, 0.2, 0.3, 0.4, 0.5, 0.6))

    rendered, sample_rate = sf.read(destination, dtype="float32")
    expected, _ = sf.read(source, dtype="float32")
    np.testing.assert_allclose(rendered, np.tanh(expected * 0.35) * 0.9, atol=2e-7)
    assert sample_rate == 48_000
    assert plugin.reset_values == [True, False]
    assert report["frames"] == 480
    assert report["sample_rate"] == 48_000
    assert report["clipped_samples"] == 0
    assert report["passthrough_residual_db"] > -100.0


def test_renderer_rejects_dry_passthrough(tmp_path: Path) -> None:
    source = tmp_path / "dry.flac"
    dry = np.linspace(-0.5, 0.5, 480, dtype=np.float32)
    sf.write(source, dry, 48_000, format="FLAC", subtype="PCM_24")
    renderer = GenomeReferenceRenderer(_PassThroughPlugin(), output_gain=0.9, warmup_seconds=0.01)

    with pytest.raises(RuntimeError, match="dry passthrough"):
        renderer.render_file(source, tmp_path / "wet.flac", (0.5,) * 6)
