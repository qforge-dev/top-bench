# top-arena

`top-arena` is the typed Python SDK for [Top Arena](https://top-arena.labqoat.com),
an open benchmark for guitar-amplifier and neural-audio models. Your model runs on
your own computer. The package downloads the public dry inputs, calls your Python
function for every benchmark case, uploads the rendered audio, and returns the
scores calculated by the public service.

The SDK does not upload your model, weights, training data, or source code. It only
uploads the audio files returned by your callback and the model metadata you provide.

## Installation

`top-arena` supports Python 3.13 and 3.14.

```bash
uv add top-arena
```

or:

```bash
python -m pip install top-arena
```

The PyPI distribution is named `top-arena`; the Python import uses an underscore:

```python
from top_arena import benchmark
```

## Quick start

Create a run, define a function that renders one dry file, and select the amplifier
to benchmark. The callback may be synchronous or asynchronous and may return a
`Path` or string pointing to any audio format supported by SoundFile, including WAV
and FLAC.

```python
from pathlib import Path

from top_arena import PipelineOptions, PositionMatrix, benchmark

from my_model import render_audio

run = benchmark.create(
    name="super-model-v1",
    creator="your-name",
    unique_positions_used=1,
    audio_duration_sum=4_000.0,
    turns=1,
    training_time=5_000.0,
    description="A short, useful explanation of the model and training setup.",
    parameter_count=40_000,
    amp_control_count=5,
    options=PipelineOptions(
        download_concurrency=4,
        run_concurrency=1,
        upload_concurrency=4,
        report_format="agent",
        report_min_finding_signal=1.0,
        report_min_evidence_signal=1.0,
    ),
)


async def model(dry_audio: Path, positions: PositionMatrix) -> Path:
    return await render_audio(dry_audio, positions)


result = run.run("D3D21964-8E80-11EE-B9D1-0242AC120002", model)
print(result.run_id, result.status, result.metrics)
```

Use `await run.run_async(amp_id, model)` when your application already has an async
event loop, such as a notebook, FastAPI application, or async test. `run.run(...)`
deliberately raises an error when called from an active event loop so it cannot nest
event-loop ownership accidentally.

The amplifier IDs currently available from the service can be read from
[`GET /api/v1/amps`](https://top-arena.labqoat.com/api/v1/amps). A complete runnable
identity-model example is available in
[`examples/passthrough_benchmark.py`](https://github.com/qforge-dev/top-bench/blob/main/examples/passthrough_benchmark.py).

## What the callback receives

The callback is invoked as `callback(dry_audio, positions)`:

- `dry_audio` is a cached local path to the dry benchmark input.
- `positions` is an immutable matrix of normalized control values for that case.
- the return value is a path to the model's wet output for the same input.

Before timed inference begins, the SDK invokes the callback once with a randomly selected
benchmark case to warm up model loading and runtime initialization. That warm-up render is
not uploaded, scored, included in per-case realtime measurements, or included in the
reported run timer. The callback is therefore invoked once more than the number of scored
benchmark cases.

Stereo output is folded to mono by the scoring service, and output at a different
sample rate is resampled to the 48 kHz reference rate. Returning audio with the same
duration and alignment as the dry input produces the most meaningful comparison.
The SDK converts the returned file to lossless PCM-24 FLAC before upload.

## Run metadata

The fields passed to `benchmark.create(...)` make leaderboard comparisons auditable:

| Field | Meaning |
| --- | --- |
| `name` | Display name and version of the submitted model. |
| `creator` | Person, team, or organization responsible for it. |
| `unique_positions_used` | Number of distinct control positions used in training. |
| `audio_duration_sum` | Total training-audio duration in seconds. |
| `turns` | Number of complete passes or turns through the training material. |
| `training_time` | End-to-end training time in seconds. |
| `description` | Architecture, data, or other context needed to understand the result. |
| `parameter_count` | Total trainable parameter count. |
| `amp_control_count` | Optional per-run override for the amp's knob and switch count. Use this when the published amp definition is incorrect for the run. |

These values are reported by the submitter; benchmark audio scores are calculated by
the server. Existing run metadata can be corrected without changing its scores through
the Python client:

```python
from top_arena import benchmark

benchmark.update_metadata("RUN_ID", amp_control_count=5)
```

Use `await benchmark.update_metadata_async(...)` inside an async event loop. The
equivalent HTTP endpoint is `PATCH /api/v1/runs/{run_id}`:

```bash
curl --request PATCH \
  --header 'content-type: application/json' \
  --data '{"amp_control_count": 5}' \
  https://top-arena.labqoat.com/api/v1/runs/RUN_ID
```

The same endpoint accepts `name`, `creator`, `unique_positions_used`,
`audio_duration_sum`, `turns`, `training_time`, `description`, and `parameter_count`.
Send `null` for `amp_control_count` to return to the shared amp definition. The amp ID
and calculated audio scores cannot be overwritten.

## How the pipeline works

After the untimed warm-up render, the SDK uses three bounded stages:

1. Download benchmark inputs and verify/cache them by content hash.
2. Invoke the model callback and measure its wall-clock render speed.
3. Convert the output to PCM-24 FLAC and upload it for scoring.

The stages overlap, while each queue remains bounded so large benchmark runs do not
grow memory use without limit. `run_concurrency` defaults to `1` because many GPU
models and plugin hosts are not safe to invoke concurrently. Increase it only when
your runtime supports parallel inference. Download and upload concurrency default to
`4`.

Every stage transition is appended to the run's server-side event log. Dry inputs are
cached in the platform-appropriate user cache directory, and completed upload staging
files are removed automatically. Set `cache_dir=` on `benchmark.create(...)` to choose
a different cache location.

## Configuration

The public service at `https://top-arena.labqoat.com` is used by default. To run
against a local or private deployment, either pass `server_url=` or set:

```bash
export TOP_ARENA_SERVER_URL=http://127.0.0.1:8000
```

Explicit `server_url=` values take precedence over the environment variable.

`PipelineOptions` controls stage concurrency, queue capacity, score polling, and the
overall completion timeout:

```python
from top_arena import PipelineOptions

options = PipelineOptions(
    download_concurrency=8,
    run_concurrency=1,
    upload_concurrency=8,
    queue_capacity=16,
    poll_interval_seconds=1.0,
    completion_timeout_seconds=1_800.0,
    report_format="agent",
    show_progress=True,
    report_min_finding_signal=1.0,
    report_min_evidence_signal=1.0,
)
```

## CLI-style progress and reports

`report_format="agent"` prints one dot to stderr for each case fully scored by the
server, followed by one self-contained report on stdout. The report is descriptive:
it contains measurements, signal-selection math, interpretations, complete control
settings, parameter patterns, exact case IDs, and supporting time regions. It does not
contain recommended actions or “what to do next” fields.

Available formats are:

| Format | Output |
| --- | --- |
| `agent` | Detailed data-first diagnostic report; progress dots remain on stderr. |
| `text` | Compact metric and significant-finding summary. |
| `json` | One complete `BenchmarkResult` object on stdout. |
| `jsonl` | Machine-readable lifecycle, progress, and final-result events. |
| `none` | No console output; use the returned result directly. |

Findings and supporting cases are selected by normalized signal strength, not a fixed
count. A value of `1.0` reaches that diagnostic's published default threshold. Raise
`report_min_finding_signal` or `report_min_evidence_signal` for stricter terminal
output, or set either to `0` to display all calculated candidates. These display
thresholds do not remove data from the returned result or JSON.

The complete local demonstration exercises the real API, workers, SDK, diagnostics, and
reporter without an external service:

```bash
uv run python examples/local_diagnostic_demo.py
```

Detailed output semantics and mathematical interpretation are documented in
[`docs/running-benchmark-cli.md`](https://github.com/qforge-dev/top-bench/blob/main/docs/running-benchmark-cli.md)
and
[`docs/interpreting-benchmark-results.md`](https://github.com/qforge-dev/top-bench/blob/main/docs/interpreting-benchmark-results.md).
Release changes are listed in the
[`CHANGELOG`](https://github.com/qforge-dev/top-bench/blob/main/CHANGELOG.md).

## Scores and results

`run(...)` returns a `BenchmarkResult` after server-side scoring completes. Its
`metrics` mapping contains mean, P90, best, and worst summaries for the versioned
metric contract. The primary metrics are ESR, human-weighted ESR, and MRSTFT; lower
is better. Correlation and render speed are also reported; higher is better.
Speed is evaluated against the 31x NAM-FULL target, with 15.5x as the acceptable floor.

When the server provides diagnostic contract `top-arena-run-diagnostics-v6`,
`result.metrics["diagnostics"]` also contains signed level and band measurements,
attack/body/sustain summaries, paired NAM comparisons, ESR concentration,
control-setting relationships, strengths, and signal-qualified significant findings.
The structured `findings` object contains `strengths` and `significant`; it contains
no `action` field.

The run appears on the public leaderboard while it progresses. If the callback,
download, conversion, upload, or server-side scoring fails, the SDK raises the
underlying error and records a failure event when a run ID has already been created.

## Development

The SDK lives in the `packages/top-arena` workspace package of
[`qforge-dev/top-bench`](https://github.com/qforge-dev/top-bench). From the repository
root:

```bash
uv sync --locked --all-packages --all-groups
uv run pytest packages/top-arena/tests
uv run ruff check packages/top-arena
uv run mypy
uv build --package top-arena
```

The project is licensed under the MIT License.
