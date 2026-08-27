from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nam.train.lightning_module import LightningModule


def recover(checkpoint: Path, model_config: Path, output: Path) -> None:
    config: dict[str, Any] = json.loads(model_config.read_text())
    model = LightningModule.load_from_checkpoint(
        checkpoint,
        **LightningModule.parse_config(config),
        map_location="cpu",
    )
    model.cpu().eval()
    model.net.sample_rate = 48_000
    output.parent.mkdir(parents=True, exist_ok=True)
    model.net.export(output.parent, basename=output.stem)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a NAM model from a final checkpoint")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    recover(arguments.checkpoint, arguments.model_config, arguments.output)


if __name__ == "__main__":
    main()
