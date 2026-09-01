# Changelog

## Unreleased

- Fixed mobile table cards so desktop minimum widths and long model names no longer
  shrink or overflow leaderboard and amp-detail pages.
- Added normal/simple/all amp filtering to the Pareto chart and leaderboard, with
  `blackface63-simple` classified as the simple amp.
- Replaced verbose leaderboard baseline comparisons with compact percentage pills and
  full NAM-A2-FULL details on hover.
- Added one random, untimed, unscored callback warm-up before benchmark inference so
  model-loading cost does not distort render-speed measurements.
- Added optional per-run amp control-count overrides at submission time.
- Added typed sync and async client helpers for correcting existing run metadata
  without changing calculated scores.

## 0.3.0 — 2026-08-27

- Added test-runner-style scored-case progress on stderr.
- Added `agent`, `text`, `json`, `jsonl`, and `none` report formats.
- Added versioned signed diagnostics for level, peaks, frequency bands, attack phases,
  ESR concentration, paired NAM comparisons, and control-setting relationships.
- Added signal-threshold filtering for findings and supporting cases instead of fixed
  item-count limits.
- Added self-contained control-setting descriptions, grouped input chunks, exact case
  IDs, and supporting time regions.
- Kept reports data-only: structured and rendered findings contain no recommended
  actions or “what to do next” fields.
- Classified speed against the 31x NAM-FULL target and 15.5x acceptable floor.

The publishing workflow creates an immutable PEP 440 post-release from this base version,
for example `0.3.0.post2`.
