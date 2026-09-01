from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tools.genome_benchmark.renderer import GenomeParadexHost, extract_blackface_controls
from tools.genome_benchmark.run_blackface63_simple import (
    DEFAULT_OUTPUT_BOOST_DB,
    _combined_output_gain,
    _parse_arguments,
)


class _Parameter:
    def __init__(self) -> None:
        self.raw_value = 0.5


class _Plugin:
    def __init__(self) -> None:
        self.parameters = {f"a{index}": _Parameter() for index in range(1, 5)}
        self.calls: list[tuple[np.ndarray, float, bool]] = []

    def __call__(
        self,
        audio: np.ndarray,
        sample_rate: float,
        *,
        buffer_size: int,
        reset: bool,
    ) -> np.ndarray:
        del buffer_size
        self.calls.append((audio.copy(), sample_rate, reset))
        gain = sum(parameter.raw_value for parameter in self.parameters.values()) / 4.0
        return audio * gain


def test_extract_blackface_controls_maps_the_four_variable_controls() -> None:
    positions = ((0.1, 0.2, 0.3, 0.4, 0.0, 0.5, 0.0),)

    assert extract_blackface_controls(positions) == (0.1, 0.2, 0.3, 0.4)


@pytest.mark.parametrize(
    "positions",
    [
        (),
        ((0.1, 0.2),),
        ((0.1, 0.2, 0.3, 0.4, 0.0, 0.5, 0.0),) * 2,
        ((0.1, 0.2, 0.3, 0.4, 0.1, 0.5, 0.0),),
        ((0.1, 0.2, 0.3, 0.4, 0.0, 0.4, 0.0),),
        ((0.1, 0.2, 0.3, 0.4, 0.0, 0.5, 1.0),),
        ((0.1, 0.2, 0.3, 1.1, 0.0, 0.5, 0.0),),
    ],
)
def test_extract_blackface_controls_rejects_incompatible_positions(positions) -> None:
    with pytest.raises(ValueError, match="blackface63-simple"):
        extract_blackface_controls(positions)


def test_renderer_sets_automation_slots_and_writes_valid_audio(tmp_path: Path) -> None:
    dry_path = tmp_path / "dry.wav"
    dry = np.linspace(-0.5, 0.5, 480, dtype=np.float32)
    sf.write(dry_path, dry, 48_000, format="WAV", subtype="FLOAT")
    plugin = _Plugin()
    host = GenomeParadexHost(plugin, tmp_path / "outputs", warmup_seconds=0.01)

    output_path = host.render(
        dry_path,
        ((0.2, 0.4, 0.6, 0.8, 0.0, 0.5, 0.0),),
    )

    assert tuple(plugin.parameters[f"a{index}"].raw_value for index in range(1, 5)) == (
        0.2,
        0.4,
        0.6,
        0.8,
    )
    assert len(plugin.calls) == 2
    assert plugin.calls[0][0].shape == (1, 480)
    assert plugin.calls[1][0].shape == (1, 480)
    assert plugin.calls[0][2] is False
    rendered, sample_rate = sf.read(output_path, dtype="float32", always_2d=False)
    assert sample_rate == 48_000
    assert rendered.shape == dry.shape
    np.testing.assert_allclose(rendered, dry * 0.5, atol=1e-6)


def test_renderer_supports_a_fixed_gain_correction_above_unity(tmp_path: Path) -> None:
    dry_path = tmp_path / "dry.wav"
    dry = np.linspace(-0.25, 0.25, 480, dtype=np.float32)
    sf.write(dry_path, dry, 48_000, format="WAV", subtype="FLOAT")
    host = GenomeParadexHost(
        _Plugin(),
        tmp_path / "outputs",
        warmup_seconds=0.0,
        output_gain=2.0,
    )

    output_path = host.render(
        dry_path,
        ((0.5, 0.5, 0.5, 0.5, 0.0, 0.5, 0.0),),
    )

    rendered, _ = sf.read(output_path, dtype="float32", always_2d=False)
    np.testing.assert_allclose(rendered, dry, atol=1e-6)


def test_combined_output_gain_converts_decibels_to_amplitude() -> None:
    assert _combined_output_gain(0.9, 7.7) == pytest.approx(2.183949085574174)


def test_default_boost_inverts_the_bias_x_training_capture_attenuation() -> None:
    assert DEFAULT_OUTPUT_BOOST_DB == 5.7


def test_cli_accepts_quiet_amp_with_unity_output() -> None:
    arguments = _parse_arguments(
        [
            "--amp-id",
            "blackface63-simple-quiet",
            "--output-gain",
            "1",
            "--output-boost-db",
            "0",
        ]
    )

    assert arguments.amp_id == "blackface63-simple-quiet"
    assert _combined_output_gain(arguments.output_gain, arguments.output_boost_db) == 1.0
