from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tools.reference_corpus.config import CorpusConfig

DEFAULT_CORPUS_ROOT = CorpusConfig().root


@dataclass(frozen=True, slots=True)
class NamBaselineConfig:
    corpus: CorpusConfig = field(default_factory=CorpusConfig)
    root: Path = DEFAULT_CORPUS_ROOT / "nam-a2-full-v1"
    namespace: str = "nam-a2-full/v1"
    epochs: int = 200
    latency_samples: int = 9
    train_stop_seconds: float = 181.0
    producer_upload_workers: int = 4
    producer_max_pending: int = 8
    bestia_root: Path = Path("/home/ubuntu/top-arena-nam-a2")
    bestia_gpus: tuple[int, int] = (2, 3)
    bestia_inference_workers: int = 4
    poll_seconds: int = 10

    @property
    def prefix(self) -> str:
        return f"{self.corpus.prefix}/{self.namespace}"

    @property
    def s3_root(self) -> str:
        return f"s3://{self.corpus.bucket}/{self.prefix}"

    @property
    def dry_190_key(self) -> str:
        return f"{self.prefix}/training/dry-190s.flac"
