# Genome PARADEX benchmark renderer

This tool runs the public `blackface63-simple` or `blackface63-simple-quiet` Top
Arena benchmark through the local `~/Documents/blackface63.ampnet` model hosted by
Two notes Genome's PARADEX block.
It maps the benchmark controls to the AmpNet model as follows:

| Top Arena control | Genome automation |
| --- | --- |
| Volume | A1 / PARADEX parameter 1 |
| Bass | A2 / PARADEX parameter 2 |
| Middle | A3 / PARADEX parameter 3 |
| Treble | A4 / PARADEX parameter 4 |

The renderer validates the simple-amp constants on every case: Reverb is `0`, Master
is `0.5`, and Bright is `0`. Genome needs to show its editor while rendering because
the plugin constructs its DSP graph when the editor first opens. The worker exits
automatically after all outputs have been uploaded and scored.

Install the macOS-only runtime dependency into the workspace environment and run:

```bash
uv pip install --python .venv/bin/python pedalboard
.venv/bin/python -m tools.genome_benchmark.run_blackface63_simple
```

The runner applies a fixed `+5.7 dB` correction because BIAS X's output was set to
`-5.7 dB` while the AmpNet training targets were captured. This is applied after the
existing `0.9` safety trim. Override it with `--output-boost-db`; every render still
fails before upload if the corrected signal reaches full scale.

For the separately versioned quiet benchmark, use the training capture level and
disable all post-render gain:

```bash
.venv/bin/python -m tools.genome_benchmark.run_blackface63_simple \
  --amp-id blackface63-simple-quiet \
  --output-gain 1 \
  --output-boost-db 0
```

The run audit, generated WAV files, and final result are stored under
`.top-arena/genome-blackface63-simple/<UTC timestamp>/`. Training metadata can be
overridden with the command's `--unique-positions-used`, `--audio-duration-sum`,
`--turns`, `--training-time`, and `--parameter-count` options.
