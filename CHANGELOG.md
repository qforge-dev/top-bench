# Changelog

## 0.4.0 — 2026-09-03

- Added human-oriented run and position reports with aggregate distributions, P90 tails,
  training-distance/ESR plots, exact controls, dry-input sensitivity, and drill-down links
  between the leaderboard, run, position, and case views.
- Added nearest-training-position distances for every measured control setting,
  setting-level ESR/distance correlations, and highest-ESR coverage evidence to JSON
  and agent reports.
- Required exact training-position vectors and dry-file identifiers for every new run,
  derive the position count from those vectors, and show both provenance lists on run pages.
- Added a native, non-ONNX NAM-A2-FULL speed calibration using pinned
  NeuralAmpModelerCore runners. Runs now report candidate speed relative to NAM-A2 on
  the same machine while retaining absolute realtime, and concurrent processes share a
  five-minute calibration cache.
- Added the `blackface63-less-simple` benchmark with a sampled Master control while
  keeping Reverb and Bright disabled, including calibrated BIAS X references and NAM
  A2 baselines.
- Deduplicated dry-input downloads across concurrent local processes with a shared
  filesystem lock, and made uploads resilient to longer proxy and deployment outages.
- Changed server scoring to round-robin fairly across active runs and use multiple
  single-threaded workers without native-library CPU oversubscription.
- Limited chart labels to key Pareto results and added server-backed leaderboard
  pagination so filtering and sorting remain global while each page loads only 25 full runs.
- Added ordered amp parameter names and every stored benchmark-position value to amp
  detail pages.
- Restored sortable mobile tables inside vertically and horizontally scrollable regions
  on leaderboard and amp-detail pages.
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
for example `0.4.0.post2`.
