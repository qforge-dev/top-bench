# Running the benchmark from the CLI

## See the complete experience without a server or dataset

From the repository root:

```bash
uv sync --locked --all-packages --all-groups
uv run python examples/local_diagnostic_demo.py
```

It accepts the same `--format agent|text|json|jsonl|none`, `--no-progress`,
`--min-finding-signal`, and `--min-evidence-signal` options as the passthrough example.

The demo creates a temporary SQLite database, temporary object storage, a synthetic
onset-rich source, 15 benchmark cases, a paired baseline, and a deliberately imperfect
candidate. It runs the real HTTP API, scoring workers, SDK pipeline, diagnostic
aggregation, progress reporter, and final agent report in one process. All temporary
data is deleted when the command exits.

One dot means one case has been scored by the server. It does not mean only that the
candidate was rendered or uploaded. Progress is append-only and goes to stderr; the
final report goes to stdout.

## Run the passthrough callback against a benchmark server

This creates and submits a real benchmark run:

```bash
uv run python examples/passthrough_benchmark.py \
  --server-url http://127.0.0.1:8000 \
  --format agent
```

Omit `--server-url` to use `TOP_ARENA_SERVER_URL` or the public default. Use
`--no-progress` when another program only wants the final document.

## Enable reporting in a model runner

The output mode belongs to `PipelineOptions`; the model callback API is unchanged:

```python
from top_arena import PipelineOptions, benchmark

run = benchmark.create(
    # model metadata omitted here
    options=PipelineOptions(
        report_format="agent",
        show_progress=True,
        report_min_finding_signal=1.0,
        report_min_evidence_signal=1.0,
    ),
)

result = run.run(amp_id, render)
```

Available formats:

| Format | Progress | Final output | Intended consumer |
|---|---|---|---|
| `agent` | Dots on stderr | Self-contained diagnostic report on stdout | AI agents and detailed terminal use |
| `text` | Dots on stderr | Short score and significant-finding summary on stdout | Humans and CI logs |
| `json` | Dots on stderr | One indented `BenchmarkResult` JSON object on stdout | Programs that want one document |
| `jsonl` | JSON events on stdout | Final result event on stdout | Streaming agent/tool integrations |
| `none` | None | None | Library use that handles the returned object itself |

Because JSON progress never shares stdout with `--format json`, this is safe:

```bash
uv run python examples/passthrough_benchmark.py --format json > result.json
```

For a machine-readable event stream:

```bash
uv run python examples/passthrough_benchmark.py --format jsonl | jq -c .
```

## What the agent report contains

Every candidate finding and evidence case has a normalized `signal_strength` measured
in multiples of its diagnostic-specific default threshold. `1.0` meets the default,
`2.0` is twice the threshold, and a value below `1.0` stays out of the default report.
`report_min_finding_signal` and `report_min_evidence_signal` set the minimum displayed
strength. Raise them for a stricter report or use `0` to show every calculated candidate.
These settings only curate `agent` and `text`; JSON retains the complete result.

Default reporting thresholds:

| Signal | `1.0x` boundary |
|---|---:|
| Paired ESR regression | At least 5% regression and 60% of paired cases worse |
| Systematic tone | At least 0.75 dB median and 75% directional consistency |
| Tonal evidence case | At least 1.5 dB absolute case-band delta |
| ESR concentration | Half of summed case ESR in 25% or fewer cases |
| ESR evidence case | At least 2x the uniform case share |
| Attack timing | At least 2 ms absolute median delta |
| Condition association | Absolute Spearman rho of at least 0.5 |
| Speed | Slowest case below the 15.5x acceptable floor |

For multi-part signals, any component below `1.0x` keeps the combined signal below the
floor. Once every component passes, their geometric mean preserves how far the signal is
above threshold. These are report-selection defaults, not validated universal quality or
audibility thresholds.

The report performs the calculations before returning the result. It includes:

- Completion and coverage, scalar fit distributions, and model speed against the 31x
  NAM-FULL target and 15.5x acceptable floor.
- Paired candidate/NAM case counts, median and geometric-mean percentage changes, and a
  95% input-chunk-clustered bootstrap interval when at least two input chunks are available.
- Signed candidate/reference level, peak, crest-factor, and fixed-band energy deltas.
- Dry-anchored attack timing and transient, early-body, and sustain summaries.
- The number of cases carrying 25%, 50%, and 75% of summed case ESR.
- Input/control associations with ESR, labeled descriptive rather than causal.
- Signal-qualified findings with complete case IDs, grouped control settings, input
  chunks, time bounds, frequency evidence, basis, and interpretation.
- Setting-level parameter patterns such as “absolute upper-mid error increases with
  volume,” calculated from one median outcome per distinct control setting. Repeated
  input chunks therefore do not inflate the association.

Findings are not displayed merely because a difference is non-zero. Tonal findings
require a published magnitude and consistency floor, error-concentration findings require
actual concentration, and timing/association findings have their own signal scales.
Because the benchmark reference is the target, signed reference differences are treated
as errors with an ideal value of zero.

Numeric setting IDs remain only as stable case locators. The report does not require the
reader to resolve “position 2”: it prints the complete named control values once, groups
all affected input chunks beneath them, summarizes the error range, and embeds the exact
case IDs and supporting time regions. The report contains no recommended actions or
instructions about what to do next.

The same information is stored under `result.metrics["diagnostics"]`; the terminal report
is a curated view, while JSON exposes the full versioned evidence packet.

See [Interpreting benchmark results](interpreting-benchmark-results.md) for metric
definitions and interpretation limits. The full mathematical design is in
[Agent-ready diagnostic analysis](agent-diagnostic-analysis.md).
