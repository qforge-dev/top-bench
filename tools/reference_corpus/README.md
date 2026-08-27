# BIAS X reference corpus

This tool builds the 48-amp TOP Arena corpus from 15 complete, real-guitar,
16-beat DI loops. It resamples each selected loop to 48 kHz and matches the
190-second reference with constant linear gain. It never uses a limiter. All
uploaded audio is lossless 24-bit FLAC.

Run with a Python environment containing `numpy`, `scipy`, `soundfile`, and
`pedalboard` (the local `qlamp` environment has them):

```bash
PY=/Users/michalwarda/Projects/qlamp/.venv/bin/python
$PY -m tools.reference_corpus prepare-loops
$PY -m tools.reference_corpus generate-settings
$PY -m tools.reference_corpus render --amp "Blackface 63"
```

`--amp` accepts an amp name, UUID, catalog index, repeated selectors, or `all`.
The renderer resets and prerolls the single BIAS X instance for every dry sound.
It feeds a bounded queue (32 files by default) drained by eight concurrent S3 upload
workers, records successful uploads transactionally in local SQLite, then removes
each staging file. Re-running the same command resumes from the completed-object state.

A small two-amp pilot is:

```bash
$PY -m tools.reference_corpus render --amp "Blackface 63" --amp "BE 101" \
  --sound-limit 1 --position-limit 2
```

Launch the complete job detached:

```bash
$PY -m tools.reference_corpus launch --amp all
```
