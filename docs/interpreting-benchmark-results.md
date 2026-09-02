# Interpreting Top Arena benchmark results

This guide defines what Top Arena results mean, what conclusions the data supports, and
where interpretation must remain cautious. It is the reference for any CLI summary,
dashboard explanation, or agent-readable report built on benchmark results.

## What is being compared

Each candidate output is compared with a latency-aligned BIAS X wet reference for the
same dry input and control position. When a NAM-A2-FULL result is available, that output
is independently compared with the same BIAS X reference. A NAM value is therefore a
paired baseline, not a direct comparison between the candidate waveform and NAM.

Before scoring, audio is converted to mono. Candidate audio is resampled to the reference
sample rate when necessary. A short candidate is padded with silence and a long candidate
is truncated to the reference length. The stored reference latency is removed before the
metrics are calculated.

The current aggregate contract is `top-arena-audio-v3` at 48 kHz. Interpretation and
cross-run comparisons should always check the contract version first.

## Run identity, status, and submitted metadata

`run_id` is the stable identifier used by result, event, case-detail, and dashboard URLs.
`total_cases` is the manifest size and `completed_cases` is the number scored successfully.
The run status has operational meaning:

- `running`: the client may still be rendering or uploading cases.
- `finalizing`: the client requested completion, but server scoring is still outstanding.
- `completed`: every expected case was scored and aggregate metrics were stored.
- `failed`: a client or scoring failure made the run terminal.

Case statuses provide the finer sequence: `pending`, `uploaded`, `scoring`, `completed`,
or `failed`.

The model and training fields are supplied by the submitter; the benchmark does not
derive or independently verify them:

| Field | Intended meaning |
|---|---|
| `name`, `creator`, `description` | Model identity and provenance supplied for the run. |
| `unique_positions_used` | Number of distinct control settings represented in training. |
| `audio_duration_sum` | Total declared training-audio duration in seconds. |
| `turns` | Declared training turns or passes. The project does not yet enforce a more precise unit. |
| `training_time` | Declared training time in seconds. Hardware is not encoded with it. |
| `parameter_count` | Declared number of model parameters. |

These fields provide efficiency and data-budget context, but they do not change the
reference-error metrics. `audio_duration_sum` is also the client's fallback duration for
realtime calculation if manifest cases do not provide durations. Comparisons involving
submitted metadata should use a consistent reporting convention.

## Metrics

### ESR

Error-to-signal ratio is the candidate-minus-reference error energy divided by reference
energy:

```text
sum((candidate - reference)²) / sum(reference²)
```

- Direction: lower is better.
- Ideal value: `0`, meaning sample-identical audio after preprocessing and alignment.
- Range: zero or greater, with no fixed upper bound.
- Best use: the primary measure of sample-domain fidelity.

ESR is sensitive to any sample difference, including gain, phase, timing, dynamics, and
spectral errors. It identifies that a mismatch exists but does not identify its cause.
A few high-energy failures can strongly affect mean ESR, so it must be read with tail and
per-case results.

### Human-weighted ESR

Human-weighted ESR applies an A-weighting-derived frequency weighting to the reference
and error spectra before taking their energy ratio.

- Direction: lower is better.
- Ideal value: `0`.
- Range: zero or greater, with no fixed upper bound.
- Best use: distinguishing errors concentrated in more audible frequency regions from
  errors that ESR weights equally.

This is a signal metric, not a listening test or a calibrated perceptual quality score.
It should not be described as what a person will hear without listening evidence.

### MRSTFT

Multi-resolution short-time Fourier transform loss averages spectral-convergence and
log-magnitude differences at three resolutions:

| FFT | Hop | Window |
|---:|---:|---:|
| 512 | 50 | 240 |
| 1024 | 120 | 600 |
| 2048 | 240 | 1200 |

- Direction: lower is better.
- Ideal value: `0`.
- Range: zero or greater, with no fixed upper bound.
- Best use: detecting time-frequency magnitude and spectral-texture differences that a
  sample-domain summary may not explain clearly.

MRSTFT and ESR have different units and scales. Their raw numbers must not be compared
with each other. Compare each metric with the same metric from a baseline or previous
run.

### Level delta

Level delta is the absolute difference between candidate and reference RMS levels in
dBFS.

- Direction: lower is better.
- Ideal value: `0 dB`.
- Best use: diagnosing overall gain or loudness mismatch.

Because the aggregate value is absolute, it does not say whether the candidate is louder
or quieter. The windowed analysis retains separate reference and candidate levels when
direction is needed.

### Peak delta

Peak delta is the absolute difference between candidate and reference peak levels in
dBFS.

- Direction: lower is better.
- Ideal value: `0 dB`.
- Best use: finding transient, clipping, limiting, or peak-envelope mismatch.

Like level delta, the aggregate value does not contain the direction of the difference.

### Correlation

Correlation is the zero-lag Pearson correlation of the centered candidate and reference
waveforms.

- Direction: higher is better.
- Ideal value: `1`.
- Range: `-1` to `1`.
- Best use: describing waveform-shape agreement independently of much of the absolute
  gain difference.

Correlation is not a fidelity score by itself. A quieter copy can have correlation near
`1` while still having level and ESR error. A polarity-inverted signal can have matching
level and peak values while correlation is near `-1`. Silence receives explicit stable
handling in the scoring contract.

### Realtime factor

`realtime_x` is case audio duration divided by model callback wall time.

- Direction: higher is faster.
- `1×`: model processing takes approximately the audio duration.
- Above `1×`: faster than real time.
- Below `1×`: slower than real time.
- `nam_a2_speed_ratio`: candidate `realtime_x` divided by a native NAM-A2-FULL
  `realtime_x` measured at the start of the run on the same machine.
- `1.0× NAM-A2`: matches local native NAM-A2 speed.
- Acceptable floor: `0.5× NAM-A2`, or half of the machine-local baseline.

The native baseline loads an official `.nam` model directly in NeuralAmpModelerCore; it
does not use ONNX. Model loading is outside the timer, three inference measurements are
combined by their median, and simultaneous local benchmark processes share a short-lived
cached result. A run is a speed strength only when every scored case reaches the local
NAM-A2 speed. Any case below half of it is below the acceptable floor. Lower values are
worse.

Candidate `realtime_x` measures callback execution, not download, FLAC transcoding,
upload, or server scoring. The normalized ratio controls for much of the hardware
difference because both candidate and NAM-A2 are measured locally, though runtime and
system load can still influence results.

## Aggregate fields

Every metric is summarized across benchmark cases:

- `mean`: arithmetic mean across cases.
- `p90`: numerical 90th percentile.
- `worst`: direction-aware worst case.
- `best`: direction-aware best case.

For lower-is-better errors, P90 means 90% of cases have a value at or below it and is a
useful view of the high-error tail. For higher-is-better metrics such as correlation and
realtime factor, the stored P90 is the high-performing end of the distribution, not the
weak tail. A report should use `worst` for their weak-end behavior or label the stored
value explicitly as the numerical 90th percentile.

Mean describes typical aggregate performance but can be dominated by extreme cases.
P90 describes broad tail behavior but can hide a small number of severe failures. Worst
identifies the extreme but says nothing about prevalence. These fields are complementary,
not interchangeable.

## Reading the NAM-A2-FULL comparison

NAM-A2-FULL is most useful as a paired context baseline:

- Compare only cases where NAM data is available.
- Compare the same metric with the same metric.
- Prefer paired case wins, median paired change, and the distribution of paired changes
  over a comparison of aggregate means alone.
- Report one side of a binary count. For example, `model lower ESR on 132/150 cases`
  already implies the remaining 18 cases and should not be followed by a separate loss
  count.

Aggregate percentage improvement can be informative, but it can also be distorted by a
few baseline outliers. It should be accompanied by a prevalence statement such as the
paired win count when per-case data is available.

## Windowed analysis

Each case contains non-overlapping 100 ms analysis points, including a final partial
window. Every point records ESR, separate reference and candidate RMS and peak levels,
their absolute deltas, and correlation. NAM points are stored alongside candidate points
when available.

Windowed analysis can locate when a problem happens. It cannot establish why it happens.
Descriptions such as `transient mismatch` or `gain mismatch` are diagnostic hypotheses
and should be labeled accordingly.

## A defensible interpretation order

1. Verify completion, case coverage, and scoring-contract compatibility.
2. Describe primary fidelity with ESR, human-weighted ESR, and MRSTFT.
3. Describe robustness with P90 and worst cases instead of repeating the mean.
4. Compare the candidate with NAM on paired cases.
5. Use level delta, peak delta, and correlation to characterize likely error shape.
6. Group per-case results by dry loop and control position to distinguish broad weaknesses
   from isolated failures.
7. Use 100 ms points to identify the evidence behind the strongest-signaled cases.

## What counts as good or bad

Top Arena does not currently define validated universal quality bands such as `excellent
ESR` or `poor MRSTFT`. A raw value should therefore not receive an absolute quality label.
Use one or more explicit comparison contexts:

- The paired NAM-A2-FULL baseline on the same cases.
- A previous version of the same model on the same amp and contract.
- Other runs on the same amp, with training budget and position coverage kept visible.
- A user-supplied regression threshold.

Listening validation remains necessary for claims about perceived audio quality.

## Turning results into self-contained findings

A useful finding contains three non-duplicated parts:

```text
Finding: one concise conclusion
Evidence: the smallest set of metrics and cases that supports it
Scope: how common the behavior is
```

Prefer broad, high-impact findings over isolated extremes. A reasonable display order is:

1. Regressions affecting many paired cases.
2. A weak distribution tail affecting a meaningful group of cases.
3. A small number of severe outliers.
4. Secondary diagnostic differences with little effect on the primary fidelity metrics.

Do not repeat the same fact under metrics, strengths, weaknesses, and focus. Present the
measurement once. Do not turn
correlation into a second claim when it merely supports an ESR finding, and do not report
both sides of complementary counts.

## Claims the data does not support by itself

The benchmark alone cannot prove that:

- A listener will prefer one model.
- A metric difference is perceptually significant.
- A specific architecture or training choice caused a result.
- An absolute score is good or bad across different amps or scoring-contract versions.
- A level or peak mismatch is specifically too loud or too quiet from the aggregate delta.
- A higher realtime factor comes from a more efficient model without equivalent hardware
  and runtime context.

CLI summaries should preserve these boundaries while making the strongest supported
findings easy to act on.

## Extended diagnostic contracts

`top-arena-case-diagnostics-v1` and `top-arena-run-diagnostics-v6` add measurements that
explain likely sources and scope of the scalar error:

- Signed candidate/reference level, sample peak, crest factor, and DC offset.
- Signed energy and error-energy share in eight published display bands from 20 Hz to
  20 kHz.
- Onsets anchored to the shared dry input, 20%-90% attack-time delta, and fixed
  transient (0-50 ms), early-body (50-200 ms), and sustain (200-500 ms) regions.
- Top 100 ms error regions with exact time bounds and dominant error band.
- Affected cases grouped by complete named control settings rather than unexplained
  numeric position IDs.
- Control-setting relationships computed from one median result per distinct setting;
  repeated input chunks do not count as independent settings.
- Paired candidate/NAM log-ratio summaries with an input-chunk cluster bootstrap interval.
- Error concentration, expressed as share of summed equally weighted case ESR.
- Descriptive Spearman associations between ESR and input/control features.

These diagnostics preserve direction that the absolute `level_db` and `peak_db` scores
discard. A positive signed band value means that the candidate has more energy than the
BIAS X reference in that frequency interval; a negative value means less. The display
band names are stable labels, not universal psychoacoustic definitions. Possible sound
descriptions remain hypotheses attached to the numerical bounds.

The mathematical design and selection rules for future diagnostic contracts are
documented in [`agent-diagnostic-analysis.md`](agent-diagnostic-analysis.md).
