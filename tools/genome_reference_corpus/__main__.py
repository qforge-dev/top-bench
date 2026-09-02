from __future__ import annotations

import argparse
import json
import logging

from tools.genome_reference_corpus.config import GenomeCorpusConfig
from tools.genome_reference_corpus.pipeline import render_amp
from tools.reference_corpus.audio import load_dry_manifest
from tools.reference_corpus.settings import resolve_amps_for_renderer


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Top Arena references through Genome")
    parser.add_argument("--amp", action="append", required=True)
    parser.add_argument("--sound-limit", type=int)
    parser.add_argument("--position-limit", type=int)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = GenomeCorpusConfig()
    manifest = json.loads((config.corpus.root / "manifests" / "amps.json").read_text())
    amps = resolve_amps_for_renderer(manifest, arguments.amp, "genome-paradex")
    dry = load_dry_manifest(config.corpus)
    for amp in amps:
        render_amp(
            config,
            amp,
            dry,
            sound_limit=arguments.sound_limit,
            position_limit=arguments.position_limit,
        )


if __name__ == "__main__":
    main()
