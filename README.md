# Top Bench

Top Bench is an open audio-model benchmark with two parts:

- `packages/top-arena`: the typed Python client that runs a model locally.
- `apps/leaderboard`: the FastAPI API, scoring worker, PostgreSQL data model, S3 storage,
  and live HTML leaderboard.

The starter dataset is Blackface 63: ten frame-aligned five-second dry excerpts at five
static control positions, for 50 benchmark cases. Dry audio, reference wet audio, and
submitted wet audio live in S3. PostgreSQL stores manifests, run state, per-case scores,
aggregates, and the append-only progress event log.

## Run a model

Until a PyPI release exists, install the client directly from GitHub:

```bash
uv add "top-arena @ git+https://github.com/qforge-dev/top-bench.git@main#subdirectory=packages/top-arena"
```

Python distribution names may contain a hyphen, but imports may not, so the valid import
is `from top_arena import benchmark`.

```python
from pathlib import Path

from top_arena import PipelineOptions, PositionMatrix, benchmark

from my_model import run_model

run = benchmark.create(
    name="super-model-v1",
    creator="your-name",
    unique_positions_used=1,
    audio_duration_sum=4_000.0,
    turns=1,
    training_time=5_000.0,
    description="Model description",
    parameter_count=40_000,
    server_url="https://top-arena.54-90-214-165.sslip.io",
    options=PipelineOptions(
        download_concurrency=4,
        run_concurrency=1,
        upload_concurrency=4,
    ),
)


async def render(dry_audio: Path, positions: PositionMatrix) -> Path:
    return await run_model(dry_audio, positions)


result = run.run("D3D21964-8E80-11EE-B9D1-0242AC120002", render)
print(result)
```

Use `await run.run_async(...)` inside an existing async application. A callback may be
synchronous or asynchronous and must return a path to its wet WAV. `run_concurrency`
defaults to one because many GPU models are not safe to invoke concurrently; increase it
when the model supports parallel calls.

The client uses bounded download → inference → upload queues. Each stage overlaps the
others, dry files are cached by content hash, and every stage transition is sent to the
server event log. `realtime_x` is audio duration divided by model wall time.

See [`examples/passthrough_benchmark.py`](examples/passthrough_benchmark.py) for a runnable
smoke test.

## Scores

Lower is better for all three error metrics:

- ESR: sample-domain error energy divided by reference energy.
- Human-weighted ESR: the same energy ratio after A-weighting in the frequency domain.
- MRSTFT: mean spectral-convergence plus log-magnitude loss at the fixed
  `512/1024/2048` FFT resolutions.

Every completed run stores mean, P90, worst, and best summaries. The metric contract and
FFT configuration are versioned with the result. The dashboard also plots the
minimization Pareto frontier for mean ESR versus unique positions used.

## Local development

The workspace targets regular CPython 3.13 and 3.14 and locks current stable dependencies
with uv.

```bash
uv sync --locked --all-packages --all-groups
uv run --package top-arena-leaderboard top-arena-server
```

Development defaults to SQLite and filesystem object storage under `data/`. Seed a local
copy from one dry source and one aligned wet source for each position:

```bash
uv run --package top-arena-leaderboard top-arena-seed \
  --source /path/to/190-second-dry.wav \
  --wet /path/to/setting-01.wav \
  --wet /path/to/setting-02.wav \
  --wet /path/to/setting-03.wav \
  --wet /path/to/setting-04.wav \
  --wet /path/to/setting-05.wav
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000). API documentation is at
`/docs`.

Quality gates:

```bash
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run alembic -c infra/alembic.ini upgrade head
uv run alembic -c infra/alembic.ini check
```

CI runs the same checks on Python 3.13 and 3.14. Production deployment notes, systemd,
Caddy, PostgreSQL, and rollback instructions are in [`infra/README.md`](infra/README.md).

## Configuration

Server settings use the `TOP_ARENA_` prefix. The production template is
[`infra/systemd/top-arena.env.example`](infra/systemd/top-arena.env.example). The important
values are:

- `TOP_ARENA_DATABASE_URL`
- `TOP_ARENA_STORAGE_BACKEND=filesystem|s3`
- `TOP_ARENA_S3_BUCKET` and `TOP_ARENA_S3_PREFIX`
- `TOP_ARENA_SERVER_HOST` and `TOP_ARENA_SERVER_PORT`

There is deliberately no authentication or private API surface in this first version.
