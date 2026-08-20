from __future__ import annotations

from tools.reference_corpus.config import CorpusConfig
from tools.reference_corpus.render import _chain_for_amp


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
