# BIAS X reference corpus

This tool builds the versioned 48-amp TOP Arena corpus. It selects five test-split
recordings from each of ten electric-guitar sources, extracts exactly 15 seconds,
and matches the 190-second reference with constant linear gain. It never uses a
limiter. All uploaded audio is lossless 24-bit FLAC.

Run with a Python environment containing `numpy`, `scipy`, `soundfile`, and
`pedalboard` (the local `qlamp` environment has them):

```bash
PY=/Users/michalwarda/Projects/qlamp/.venv/bin/python
$PY -m tools.reference_corpus query-candidates
$PY -m tools.reference_corpus prepare-dry \
  --candidates data/reference-corpus-v1/source/athena-candidates.csv
$PY -m tools.reference_corpus generate-settings
$PY -m tools.reference_corpus render --amp "Blackface 63"
```

`--amp` accepts an amp name, UUID, catalog index, repeated selectors, or `all`.
The renderer resets and prerolls the plugin for every dry sound, uploads each wet
FLAC immediately, records it transactionally in local SQLite, then removes the
staging file. Re-running the same command resumes from the completed-object state.

A small two-amp pilot is:

```bash
$PY -m tools.reference_corpus render --amp "Blackface 63" --amp "BE 101" \
  --sound-limit 1 --position-limit 2
```

Launch the complete job detached:

```bash
$PY -m tools.reference_corpus launch --amp all
```
