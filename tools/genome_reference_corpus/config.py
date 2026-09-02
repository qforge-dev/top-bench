from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tools.reference_corpus.config import REPOSITORY_ROOT, CorpusConfig


@dataclass(frozen=True, slots=True)
class GenomeCorpusConfig:
    root: Path = REPOSITORY_ROOT / "data" / "genome-reference-corpus"
    presets: Path = REPOSITORY_ROOT / "tools" / "genome_reference_corpus" / "presets"
    plugin: Path = Path("/Library/Audio/Plug-Ins/VST3/Genome.vst3")
    models: Path = Path.home() / "Documents/Two notes Audio Engineering/PARADEX Models"
    corpus: CorpusConfig = field(default_factory=CorpusConfig)
    output_gain: float = 0.9
    upload_workers: int = 8
    epochs: int = 200
    latency_samples: int = 9
    train_stop_seconds: float = 181.0

    @property
    def nam_prefix(self) -> str:
        return f"{self.corpus.prefix}/nam-a2-full/v1"

    @property
    def training_dry_key(self) -> str:
        return f"{self.nam_prefix}/training/dry-190s.flac"

    @property
    def training_dry(self) -> Path:
        return self.corpus.root / "nam-a2-full-v1" / "training" / "dry-190s.flac"
