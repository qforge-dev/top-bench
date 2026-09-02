from __future__ import annotations

from pathlib import Path

from tools.genome_reference_corpus.preset import (
    component_xml,
    inspect_model_state,
    session_to_vstpreset,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = (
    REPOSITORY_ROOT
    / "tools"
    / "genome_reference_corpus"
    / "presets"
    / "genome-fried-r50-dirty-simple.vstpreset"
)


def test_inspect_model_state_reads_captured_model_and_digest() -> None:
    state = inspect_model_state(TEMPLATE.read_bytes())

    assert state.model_path.endswith("/Fried R50 Dirty.ampnet")
    assert state.preset_state_md5 == "7f8f5399f926500491143fe9a32c3a93"


def test_session_to_vstpreset_preserves_captured_model_state() -> None:
    template = TEMPLATE.read_bytes()
    session = (
        component_xml(template)
        .replace(b"<Genome>", b"<genome>", 1)
        .replace(b"</Genome>", b"</genome>", 1)
    )

    captured = session_to_vstpreset(session, template)

    assert captured.startswith(b"VST3")
    assert component_xml(captured).startswith(b"<?xml")
    assert b"<Genome>" in component_xml(captured)
    assert inspect_model_state(captured) == inspect_model_state(template)
