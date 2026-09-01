from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import soundfile as sf

from tools.nam_baselines.derive_gain_calibrated import (
    derive_metadata,
    scale_audio_file,
)


def test_scale_audio_file_applies_exact_gain_and_reports_output(tmp_path: Path) -> None:
    source = tmp_path / "source.flac"
    target = tmp_path / "target.flac"
    audio = np.linspace(-0.8, 0.8, 4_800, dtype=np.float32)
    sf.write(source, audio, 48_000, subtype="PCM_24")

    report = scale_audio_file(source, target, gain_db=-5.7)
    rendered, sample_rate = sf.read(target, dtype="float32")
    expected, _ = sf.read(source, dtype="float32")

    assert sample_rate == 48_000
    assert np.allclose(rendered, expected * 10 ** (-5.7 / 20), atol=2e-7)
    assert report["frames"] == 4_800
    assert report["sample_rate"] == 48_000
    assert report["clipped_samples"] == 0
    assert report["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_derive_metadata_retargets_cases_and_records_provenance() -> None:
    source = {
        "amp_id": "blackface63-simple",
        "amp_name": "blackface63-simple",
        "job_id": "blackface63-simple--position-01",
        "cases": [
            {
                "sound_id": "sound-01",
                "bias_reference_key": "corpus/wet/blackface63-simple/position-01/sound-01.flac",
                "nam_a2_full_key": (
                    "corpus/nam/models/blackface63-simple/position-01/outputs/sound-01.flac"
                ),
                "peak": 0.7,
                "rms_db": -12.0,
                "sha256": "old",
            }
        ],
    }
    report = {
        "sound-01": {
            "frames": 123,
            "sample_rate": 48_000,
            "peak": 0.3,
            "rms_db": -17.7,
            "clipped_samples": 0,
            "sha256": "new",
        }
    }

    derived = derive_metadata(
        source,
        source_amp_id="blackface63-simple",
        target_amp_id="blackface63-simple-quiet",
        gain_db=-5.7,
        reports=report,
    )

    assert derived["amp_id"] == "blackface63-simple-quiet"
    assert derived["amp_name"] == "blackface63-simple-quiet"
    assert derived["job_id"] == "blackface63-simple-quiet--position-01"
    assert derived["cases"][0]["bias_reference_key"].endswith(
        "/wet/blackface63-simple-quiet/position-01/sound-01.flac"
    )
    assert derived["cases"][0]["nam_a2_full_key"].endswith(
        "/models/blackface63-simple-quiet/position-01/outputs/sound-01.flac"
    )
    assert derived["cases"][0]["sha256"] == "new"
    assert derived["derivation"] == {
        "kind": "fixed-output-gain",
        "source_amp_id": "blackface63-simple",
        "gain_db": -5.7,
        "gain_amplitude": 10 ** (-5.7 / 20),
    }
    assert source["amp_id"] == "blackface63-simple"
