from __future__ import annotations

from tools.reference_corpus.config import CorpusConfig
from tools.reference_corpus.render import _chain_for_amp, _upload_render


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
