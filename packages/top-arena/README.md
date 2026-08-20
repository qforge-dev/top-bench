# top-arena

The local, fully typed benchmark runner for Top Bench.

Install it straight from the public GitHub repository:

```bash
uv add "top-arena @ git+https://github.com/qforge-dev/top-bench.git@main#subdirectory=packages/top-arena"
```

Create one run and pass it a synchronous or asynchronous function that writes a wet
WAV file. The SDK caches dry audio and overlaps its bounded download, inference, and
upload stages.

```python
from pathlib import Path

from top_arena import PipelineOptions, benchmark

run = benchmark.create(
    name="super-model-v1",
    creator="your-name",
    unique_positions_used=1,
    audio_duration_sum=250.0,
    turns=1,
    training_time=5_000.0,
    description="Model description",
    parameter_count=40_000,
    server_url="https://top-arena.54-90-214-165.sslip.io",
    options=PipelineOptions(run_concurrency=1),
)


async def model(dry_audio: Path, positions: tuple[tuple[float, ...], ...]) -> Path:
    # Run your model and return the path of its wet WAV output.
    return await render_wet_audio(dry_audio, positions)


result = run.run("D3D21964-8E80-11EE-B9D1-0242AC120002", model)
print(result)
```

Call `await run.run_async(...)` when the surrounding application already owns an
async event loop. The online leaderboard is the default server; set
`TOP_ARENA_SERVER_URL` to target another deployment without changing application code.
