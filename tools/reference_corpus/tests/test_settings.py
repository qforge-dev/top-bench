from __future__ import annotations

from typing import Any

import numpy as np

from tools.reference_corpus.settings import (
    _build_genome_amps,
    _build_positions,
    _derive_fixed_amp,
    _derive_output_calibrated_amp,
    _maximin_latin_hypercube,
    resolve_amps_for_renderer,
)


def test_latin_hypercube_is_reproducible_and_stratified() -> None:
    first = _maximin_latin_hypercube(10, 4, 42)
    second = _maximin_latin_hypercube(10, 4, 42)
    np.testing.assert_array_equal(first, second)
    assert np.all((first >= 0.05) & (first <= 0.95))
    normalized = (first - 0.05) / 0.9
    for column in normalized.T:
        assert sorted(np.floor(column * 10).astype(int)) == list(range(10))


def test_factory_default_snaps_binary_controls_to_declared_choices() -> None:
    amp = {
        "controls": ["Gain", "Bright"],
        "settings": {"default": {"values": [0.5, 0.061]}},
    }
    controls: list[dict[str, Any]] = [
        {"name": "Gain", "kind": "knob", "sampling": "uniform_0_1"},
        {
            "name": "Bright",
            "kind": "singleSwitch",
            "sampling": "uniform_discrete",
            "choices": [0.0, 1.0],
        },
    ]

    positions = _build_positions(amp, controls, count=10, seed=42)

    assert positions[0]["kind"] == "factory_default"
    assert positions[0]["values"]["Bright"] == 0.0
    assert {position["values"]["Bright"] for position in positions} <= {0.0, 1.0}


def test_derived_amp_preserves_blackface_distribution_and_fixes_bright_and_master() -> None:
    source = {
        "amp_id": "blackface-source-id",
        "amp_index": 19,
        "amp_name": "Blackface 63",
        "controls": [
            {"index": 0, "name": "Volume"},
            {"index": 5, "name": "Master"},
            {"index": 6, "name": "Bright"},
        ],
        "positions": [
            {
                "position_id": "position-01",
                "kind": "factory_default",
                "values": {"Volume": 0.75, "Master": 0.3, "Bright": 1.0},
                "vector": [0.75, 0.3, 1.0],
            },
            {
                "position_id": "position-02",
                "kind": "maximin_latin_hypercube",
                "values": {"Volume": 0.2, "Master": 0.8, "Bright": 1.0},
                "vector": [0.2, 0.8, 1.0],
            },
        ],
    }

    derived = _derive_fixed_amp(
        source,
        amp_id="blackface63-simple",
        amp_name="blackface63-simple",
        amp_index=49,
        fixed_controls={"Bright": 0.0, "Master": 0.5},
    )

    assert derived["amp_id"] == "blackface63-simple"
    assert derived["renderer_amp_id"] == "blackface-source-id"
    assert derived["fixed_controls"] == {"Bright": 0.0, "Master": 0.5}
    assert [position["values"]["Volume"] for position in derived["positions"]] == [0.75, 0.2]
    assert all(position["values"]["Bright"] == 0.0 for position in derived["positions"])
    assert all(position["values"]["Master"] == 0.5 for position in derived["positions"])
    assert [position["vector"] for position in derived["positions"]] == [
        [0.75, 0.5, 0.0],
        [0.2, 0.5, 0.0],
    ]


def test_quiet_amp_reuses_simple_positions_and_records_output_calibration() -> None:
    simple = {
        "amp_id": "blackface63-simple",
        "amp_index": 53,
        "amp_name": "blackface63-simple",
        "renderer_amp_id": "blackface-source-id",
        "fixed_controls": {"Bright": 0.0, "Master": 0.5},
        "positions": [
            {
                "position_id": "position-01",
                "values": {"Volume": 0.75, "Master": 0.5, "Bright": 0.0},
                "vector": [0.75, 0.5, 0.0],
            }
        ],
    }

    quiet = _derive_output_calibrated_amp(
        simple,
        amp_id="blackface63-simple-quiet",
        amp_name="blackface63-simple-quiet",
        amp_index=54,
        output_db=-5.7,
        output_raw=0.705,
    )

    assert quiet["amp_id"] == "blackface63-simple-quiet"
    assert quiet["renderer_amp_id"] == "blackface-source-id"
    assert quiet["renderer_output_db"] == -5.7
    assert quiet["renderer_output_raw"] == 0.705
    assert quiet["positions"] == simple["positions"]
    assert quiet["positions"] is not simple["positions"]


def test_genome_catalog_builds_ten_six_control_simple_amps() -> None:
    amps = _build_genome_amps(first_amp_index=55, position_count=10, seed=630_048)

    assert len(amps) == 10
    assert len({amp["amp_id"] for amp in amps}) == 10
    assert {amp["reference_renderer"] for amp in amps} == {"genome-paradex"}
    gains = {amp["amp_id"]: amp["reference_output_gain"] for amp in amps}
    assert gains["genome-hektor-lead-simple"] == 0.04
    assert {gain for amp_id, gain in gains.items() if amp_id != "genome-hektor-lead-simple"} == {
        0.9
    }
    assert [control["name"] for control in amps[0]["controls"]] == [
        "Presence",
        "Master",
        "Treble",
        "Middle",
        "Bass",
        "Gain",
    ]
    for amp in amps:
        assert len(amp["positions"]) == 10
        assert amp["positions"][0]["kind"] == "factory_default"
        assert amp["positions"][0]["vector"] == [0.5] * 6
        for control_index in range(6):
            values = [position["vector"][control_index] for position in amp["positions"]]
            assert min(values) >= 0.05
            assert max(values) <= 0.95


def test_renderer_selection_filters_all_and_rejects_explicit_wrong_renderer() -> None:
    manifest = {
        "amps": [
            {"amp_id": "bias", "amp_name": "Bias", "amp_index": 1, "reference_renderer": "bias-x"},
            {
                "amp_id": "genome",
                "amp_name": "Genome",
                "amp_index": 2,
                "reference_renderer": "genome-paradex",
            },
        ]
    }

    assert [amp["amp_id"] for amp in resolve_amps_for_renderer(manifest, ["all"], "bias-x")] == [
        "bias"
    ]
    with np.testing.assert_raises_regex(ValueError, "genome-paradex"):
        resolve_amps_for_renderer(manifest, ["genome"], "bias-x")
