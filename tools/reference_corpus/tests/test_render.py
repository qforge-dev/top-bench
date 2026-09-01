from __future__ import annotations

from tools.reference_corpus.config import CorpusConfig
from tools.reference_corpus.render import (
    _chain_for_amp,
    _set_renderer_output_level,
    _upload_render,
)


class _Parameter:
    def __init__(self) -> None:
        self.raw_value = 0.0


class _Plugin:
    def __init__(self) -> None:
        self.parameters = {"master_output_level": _Parameter()}


def test_chain_uses_control_indices_and_selected_values() -> None:
    config = CorpusConfig()
    amp = {
        "amp_id": "AMP-ID",
        "amp_name": "Example",
        "controls": [
            {"index": 0, "name": "Gain"},
            {"index": 3, "name": "Master"},
        ],
    }
    position = {
        "position_id": "position-02",
        "values": {"Gain": 0.2, "Master": 0.8},
    }
    chain = _chain_for_amp(config, amp, position)
    module = next(item for item in chain["sigPath"] if item.get("dspId") == "BiasOneAmp")
    assert module["ampId"] == "AMP-ID"
    assert module["param"] == [{"id": 0, "value": 0.2}, {"id": 3, "value": 0.8}]


def test_derived_amp_chain_uses_the_source_renderer_amp_id() -> None:
    config = CorpusConfig()
    amp = {
        "amp_id": "blackface63-simple",
        "renderer_amp_id": "BLACKFACE-BIAS-X-ID",
        "amp_name": "blackface63-simple",
        "controls": [{"index": 6, "name": "Bright"}],
    }
    position = {"position_id": "position-01", "values": {"Bright": 0.0}}

    chain = _chain_for_amp(config, amp, position)

    module = next(item for item in chain["sigPath"] if item.get("dspId") == "BiasOneAmp")
    assert module["ampId"] == "BLACKFACE-BIAS-X-ID"


def test_quiet_amp_sets_the_bias_x_output_to_minus_5_7_db() -> None:
    plugin = _Plugin()

    _set_renderer_output_level(
        plugin,
        {
            "amp_id": "blackface63-simple-quiet",
            "renderer_output_db": -5.7,
            "renderer_output_raw": 0.705,
        },
    )

    assert plugin.parameters["master_output_level"].raw_value == 0.705


def test_normal_amp_restores_the_bias_x_output_to_zero_db() -> None:
    plugin = _Plugin()
    plugin.parameters["master_output_level"].raw_value = 0.705

    _set_renderer_output_level(plugin, {"amp_id": "normal-amp"})

    assert plugin.parameters["master_output_level"].raw_value == 0.8


def test_upload_removes_staging_only_after_success(tmp_path, monkeypatch) -> None:
    output = tmp_path / "wet.flac"
    output.write_bytes(b"fLaC")
    calls = []

    def upload(path, destination):
        assert path.exists()
        calls.append((path, destination))

    monkeypatch.setattr("tools.reference_corpus.render.s3_upload", upload)
    row = {"object_key": "corpus/wet.flac"}
    result = _upload_render(CorpusConfig(bucket="bucket"), output, row)
    assert result is row
    assert calls == [(output, "s3://bucket/corpus/wet.flac")]
    assert not output.exists()
