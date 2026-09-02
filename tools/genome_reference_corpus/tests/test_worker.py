from __future__ import annotations

from pathlib import Path

import pytest

from tools.genome_reference_corpus.worker import _render_when_ready


class _LoadingRenderer:
    def __init__(self) -> None:
        self.calls = 0

    def render_file(
        self, source: Path, destination: Path, controls: tuple[float, ...]
    ) -> dict[str, int]:
        del source, destination, controls
        self.calls += 1
        if self.calls < 3:
            message = (
                "Genome returned dry passthrough at gain 1.000000; the PARADEX model did not load"
            )
            raise RuntimeError(message)
        return {"frames": 123}


def test_render_when_ready_retries_dry_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = _LoadingRenderer()
    monkeypatch.setattr("tools.genome_reference_corpus.worker.time.sleep", lambda _: None)

    report = _render_when_ready(
        renderer,
        {
            "source": "dry.flac",
            "destination": "wet.flac",
            "controls": [0.5] * 6,
        },
        timeout_seconds=10.0,
    )

    assert report == {"frames": 123}
    assert renderer.calls == 3


def test_render_when_ready_does_not_retry_other_errors() -> None:
    class _BrokenRenderer(_LoadingRenderer):
        def render_file(
            self, source: Path, destination: Path, controls: tuple[float, ...]
        ) -> dict[str, int]:
            del source, destination, controls
            message = "clipping"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="clipping"):
        _render_when_ready(
            _BrokenRenderer(),
            {
                "source": "dry.flac",
                "destination": "wet.flac",
                "controls": [0.5] * 6,
            },
            timeout_seconds=10.0,
        )
