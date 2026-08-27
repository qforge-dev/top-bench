from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOOP_SOURCE = Path(
    "/Users/michalwarda/Downloads/Different Songs Guitar DI Loops - 16 Beats - 15 Minutes - FINAL"
)
DEFAULT_LOOP_SELECTION = (
    "003",
    "006",
    "019",
    "024",
    "033",
    "035",
    "040",
    "051",
    "058",
    "061",
    "070",
    "077",
    "081",
    "100",
    "116",
)


@dataclass(frozen=True, slots=True)
class CorpusConfig:
    root: Path = REPOSITORY_ROOT / "data" / "reference-corpus-v1"
    reference: Path = Path(
        "/Users/michalwarda/Projects/qlamp-tools/bias-x-wet-captures/dry/input.wav"
    )
    amp_report: Path = Path(
        "/Users/michalwarda/Projects/top-blackface/reports/amp-discovery/amp-discovery-report.json"
    )
    old_capture_plan: Path = Path(
        "/Users/michalwarda/Projects/qlamp-tools/bias-x-wet-captures/plan.json"
    )
    mapper_root: Path = Path("/Users/michalwarda/Projects/top-blackface/bias-x-mapper")
    plugin_path: Path = Path("/Library/Audio/Plug-Ins/VST3/BIAS X.vst3")
    factory_resources: Path = Path("/Library/Application Support/PositiveGrid/BIAS_X/Resources")
    bucket: str = "qlamp-training-artifacts-088543363904"
    prefix: str = "parametric-amplifier/public/top-arena/reference-corpus/v1"
    sample_rate: int = 48_000
    clip_seconds: float = 15.0
    sound_count: int = 15
    sounds_per_source: int = 5
    position_count: int = 10
    benchmark_sounds_per_position: int = 15
    download_workers: int = 8
    upload_workers: int = 8
    max_pending_uploads: int = 32
    true_peak_cap_db: float = -1.0
    max_loudness_error_lu: float = 2.0
    seed: int = 630_048

    @property
    def s3_root(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}"
