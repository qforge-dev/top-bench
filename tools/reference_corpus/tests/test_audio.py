from __future__ import annotations

import csv

import numpy as np
import soundfile as sf

from tools.reference_corpus.audio import AudioLevels, prepare_loop_dry
from tools.reference_corpus.config import CorpusConfig


def test_prepare_loops_streams_exact_selection_and_removes_stale_dry(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    fieldnames = [
        "file",
        "song",
        "loop_number",
        "beats",
        "bpm",
        "duration",
        "source_start",
        "source_end",
        "source_url",
    ]
    rows = []
    for loop_id, song in (("003", "Song Three"), ("006", "Song Six")):
        filename = f"{loop_id} - {song}.wav"
        sf.write(source / filename, np.full(480, 0.1), 48_000, subtype="PCM_24")
        rows.append(
            {
                "file": filename,
                "song": song,
                "loop_number": "1",
                "beats": "16",
                "bpm": "120",
                "duration": "0.01",
                "source_start": "1.0",
                "source_end": "1.01",
                "source_url": "https://example.test/source",
            }
        )
    with (source / "loop_manifest.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    levels = AudioLevels(-20.2, -3.0, -3.0, -22.0)
    monkeypatch.setattr("tools.reference_corpus.audio.measure_levels", lambda _path: levels)
    monkeypatch.setattr(
        "tools.reference_corpus.audio._normalize_clip",
        lambda clip, **_kwargs: (clip, levels, levels, 0.0),
    )
    uploads = []
    monkeypatch.setattr(
        "tools.reference_corpus.audio.s3_upload",
        lambda path, destination: uploads.append((path.name, destination)),
    )
    root = tmp_path / "corpus"
    stale = root / "dry" / "sound-99.flac"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    config = CorpusConfig(root=root, reference=reference, sound_count=2)

    manifest_path = prepare_loop_dry(config, source, ("006", "003"))

    assert not stale.exists()
    assert (root / "dry" / "sound-01.flac").exists()
    assert (root / "dry" / "sound-02.flac").exists()
    manifest = __import__("json").loads(manifest_path.read_text())
    assert [sound["song"] for sound in manifest["sounds"]] == ["Song Six", "Song Three"]
    assert len(uploads) == 3
