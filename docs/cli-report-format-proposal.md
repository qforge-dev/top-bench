# CLI report format proposal

> Implementation status: the shipped `agent` report keeps Iteration 4's evidence-packet
> calculations but deliberately omits every recommended action and “what to do next”
> field. It also keeps a separate measured-signature section so the complete signed
> profile remains available. Exact commands and output-mode behavior are documented in
> [`running-benchmark-cli.md`](running-benchmark-cli.md). The examples below remain the
> design history and use illustrative values. Current speed classification uses a 31x
> NAM-FULL target and a 15.5x acceptable floor; the rejected historical iterations below
> used the incorrect assumption that merely exceeding realtime was a strength.

This proposal targets two audiences at once: a person reading a terminal and an AI agent
capturing process output. The default report should resemble a small test runner, remain
append-only, and answer three questions:

1. Did the benchmark complete?
2. How did the model perform relative to useful context?
3. Which measured patterns have the strongest signal?

Examples below use illustrative values. They are format examples, not quality thresholds.

## Iteration 1: conventional summary

```text
Top Arena
model: top-golden-grail-v22
amp:   Dumbel 50

..................................................  50/150
.................................................. 100/150
.................................................. 150/150

COMPLETED · 4m 32s · run 83803263

QUALITY
ESR                 0.00681 mean  0.01358 p90  0.03837 worst
Human-weighted ESR  0.00681 mean  0.01253 p90  0.03164 worst
MRSTFT              0.41885 mean  0.54454 p90  0.61986 worst
Correlation         0.99684 mean                 0.98073 worst
Level delta          0.137 dB mean                0.454 dB worst
Peak delta           0.457 dB mean                2.191 dB worst
Speed                 4.51x mean                    2.70x worst

VERSUS NAM-A2-FULL
+ ESR is 63.0% lower; model is lower on 132/150 paired cases
+ Human-weighted ESR is 74.8% lower
+ MRSTFT is 15.7% lower

STRENGTHS
+ ESR is substantially better than NAM.
+ Waveform correlation is high.
+ Every case runs faster than real time.

WEAKNESSES
! MRSTFT improves much less than ESR.
! Worst ESR is 5.6x the mean.
! Peak mismatch reaches 2.19 dB.

FOCUS
1. Improve spectral fidelity; MRSTFT has the smallest baseline gain.
2. Investigate the ESR tail; the worst case is 5.6x the mean.
3. Inspect peak handling; the maximum delta is 2.19 dB.
```

### Deduplication review

This is readable but inefficient. The ESR comparison appears in the comparison table and
again under strengths. Every weakness is then repeated under focus with nearly identical
evidence. `Won 132/150` and `lost 18/150` would be another avoidable duplication, so only
one side is retained here. Even after that fix, the structure still says most important
facts two or three times.

Decision: reject as the default. It resembles many benchmark reports but spends too much
output restating itself.

## Iteration 2: findings first

```text
Top Arena · top-golden-grail-v22 · Dumbel 50

..................................................  50/150
.................................................. 100/150
.................................................. 150/150

COMPLETED · 4m 32s · run 83803263

FINDINGS
1. Broad sample-domain improvement
   ESR 0.00681; 63.0% below NAM and lower on 132/150 paired cases.

2. Spectral fidelity is the clearest remaining opportunity
   MRSTFT 0.41885; only 15.7% below NAM. Inspect case-a, case-b, case-c.

3. A small error tail limits robustness
   ESR p90 0.01358, worst 0.03837. Five cases contribute 31% of ESR.
   Inspect case-d, case-e, case-f.

4. Peak handling is the strongest secondary diagnostic
   Peak delta 0.457 dB mean, 2.191 dB worst; 14 cases exceed 1 dB.

CONTEXT
Human-weighted ESR  0.00681 mean  0.01253 p90
Correlation         0.99684 mean  0.98073 worst
Level delta          0.137 dB mean
Speed                 4.51x mean    2.70x worst

details: https://.../runs/83803263
```

### Deduplication review

This is shorter and the evidence sits next to its interpretation. It eliminates separate
strength, weakness, and focus sections. However, actions are inconsistent: some findings
include cases, while others do not. The `CONTEXT` block also looks secondary even though
human-weighted ESR is a primary fidelity metric. An agent must infer which findings are
positive, which require action, and why some metrics became findings while others did not.

Decision: better, but insufficiently systematic.

## Iteration 3: scorecard plus priorities

```text
Top Arena · top-golden-grail-v22 · Dumbel 50
scoring (`.` complete, `E` error)
..................................................  50/150
.................................................. 100/150
.................................................. 150/150

COMPLETED · 4m 32s · run 83803263

FIT                    MEAN       P90      WORST    VS NAM (mean; cases better)
ESR                  0.00681   0.01358    0.03837   63.0% lower; 132/150
Human-weighted ESR   0.00681   0.01253    0.03164   74.8% lower; 140/150
MRSTFT               0.41885   0.54454    0.61986   15.7% lower; 110/150

DIAGNOSTICS              MEAN      WORST
Correlation           0.99684    0.98073
Level delta           0.137 dB   0.454 dB
Peak delta            0.457 dB   2.191 dB
Model speed             4.51x      2.70x

PRIORITIES
1. Spectral detail — smallest paired improvement; concentrated at positions 07–09.
   Inspect case-a, case-b, case-c.
2. ESR robustness — five cases contribute 31% of error.
   Start with case-d, case-e, case-f.
3. Peak behavior — position 07 mean is 2.1x the run mean.
   Inspect its highest-delta 100 ms regions.

details: https://.../runs/83803263
```

### Deduplication review

Each section now has one job:

- Progress reports execution only.
- `FIT` reports primary fidelity, tail, and baseline context once.
- `DIAGNOSTICS` characterizes the error without reinterpreting primary fit.
- `PRIORITIES` adds new prevalence, grouping, case, and action information. It does not
  repeat raw values from the scorecard.
- The details link replaces a long worst-case listing.

There is no separate `STRENGTHS` section: positive evidence is already visible in the
paired comparison column. There is no separate `WEAKNESSES` section: only weaknesses
that add material measured evidence earn a place in the findings. This saves space and
prevents unsupported praise or criticism.

Decision: recommended compact report for a person. The next iteration extends it for an
agent that should not need follow-up API calls or manual calculation.

## Iteration 4: self-contained agent diagnostic

This version intentionally contains more text, but each finding remains non-duplicated.
The old `FIT`, `DIAGNOSTICS`, and `PRIORITIES` sections become evidence packets: the
measurement, sound pattern, scope, representative regions, and interpretation are
presented together once.

```text
Top Arena · top-golden-grail-v22 · Dumbel 50
scoring (`.` complete, `E` error)
..................................................  50/150
.................................................. 100/150
.................................................. 150/150

COMPLETED · 4m 32s · run 83803263 · analysis top-arena-diagnostic-v1
coverage: 150/150 cases · 15/15 dry loops · 10/10 positions · NAM 150/150

GLOBAL FIT [strength]
                         MEAN       P90      WORST    VS NAM (mean; cases better)
ESR                    0.00681   0.01358    0.03837   63.0% lower; 132/150
Human-weighted ESR     0.00681   0.01253    0.03164   74.8% lower; 140/150
MRSTFT                 0.41885   0.54454    0.61986   15.7% lower; 110/150
Paired ESR median change: -58.2% [loop-bootstrap 95%: -64.1%, -50.3%].

P1 · PRESENCE DURING HIGH-GAIN ATTACKS [weakness, high confidence]
Finding: candidate has excess 2–4 kHz energy during attacks at positions 08–10.
Math: +1.8 dB median presence delta; +2.5 dB in first 50 ms; +0.3 dB in sustain.
Scope: 42/150 cases · 12/15 dry loops · loop-bootstrap 95% [+1.3, +2.2] dB.
Context: candidate is +0.6 dB above NAM in the same band and conditions.
Sound hypothesis: more forward/harsh tone with a slightly less immediate pick attack.
Evidence:
  position-09 gain=.9 bass=.5 mid=.5 treble=.5
    sound-03  1.20–1.28s  presence +3.1 dB  attack +7.8 ms  ESR .041
    sound-11  4.62–4.69s  presence +2.8 dB  transient -1.1 dB  ESR .037
  position-10 gain=1 bass=.5 mid=.5 treble=.5
    sound-08  6.04–6.12s  presence +3.4 dB  attack +8.2 ms  ESR .044

P2 · LOW-MID CONTROL RANGE [weakness, high confidence]
Finding: bass-control response is compressed relative to the reference.
Math: reference 80–400 Hz range 5.8 dB; candidate 3.6 dB (62% of reference range).
Scope: same direction on 13/15 dry loops; largest gap at bass <= .2.
Sound hypothesis: extreme low-bass settings remain fuller/less lean than intended.
Evidence:
  bass .0→.2: candidate-reference low-mid delta +1.9 dB median, 15/15 loops
  bass .8→1: candidate-reference low-mid delta -0.2 dB median, 8/15 loops

P3 · ERROR TAIL [weakness, medium confidence]
Finding: five cases carry 31% of summed ESR; four share dense attacks and high gain.
Pattern: dense-onset cases median ESR .0142 vs .0049 for sparse cases (2.9x).
Timing: 68% of their error lies in transient windows, chiefly 2–8 kHz.
Sound hypothesis: fast repeated picks may lose attack shape and add high-frequency edge.
Evidence:
  case ...03-position-09  ESR .044  18 onsets/s  transient share 74%
  case ...08-position-10  ESR .041  16 onsets/s  transient share 71%
  case ...11-position-09  ESR .037  15 onsets/s  transient share 69%

S1 · LEVEL AND SUSTAIN [strength, high confidence]
Finding: sustained level and waveform shape are stable outside the attack weaknesses.
Math: level delta .137 dB mean/.454 dB worst; sustain correlation .9981 median.
Scope: 137/150 cases are within .3 dB during sustain; no position-specific regression.
Sound interpretation: body and sustain loudness closely follow the reference.

S2 · SPEED [strength, hardware-specific]
Finding: every case is faster than real time on the reported runtime.
Math: 4.51x mean; 2.70x slowest; 8.39x fastest.
Limit: excludes download, transcoding, upload, and scoring; hardware is not encoded.

OTHER MEASURED PATTERNS
tone, signed candidate-reference median:
  20–80    -0.1 dB   80–150  +0.2 dB   150–400 +0.5 dB   400–800 +0.1 dB
  800–2k  +0.4 dB   2–4k    +0.9 dB   4–8k    +0.6 dB   8–20k   +0.1 dB
envelope: attack +3.2 ms · early body -0.2 dB · sustain +0.0 dB · decay +0.3 dB/s
timing: median lag +0.06 ms · worst +0.42 ms · polarity agreement 150/150
dynamics: loudness +0.08 LU · crest factor -0.31 dB · high-level gain +0.22 dB
silence: sufficient gaps in 6/15 loops · residual noise +0.4 dB · gate finding omitted

details: https://.../runs/83803263
machine: top-arena-cli-summary-v1 JSON included on stdout with --format json
```

All numbers above are illustrative. The important format change is that each priority
contains the calculations an agent would otherwise have to retrieve or derive: exact
frequency/time bounds, signed direction, prevalence, uncertainty, controls, timestamps,
representative cases, baseline context, and interpretation level.

### Deduplication review

- `GLOBAL FIT` owns aggregate fidelity and baseline facts.
- Each finding owns one distinct measured pattern.
- Strengths describe capabilities not already used as priority evidence.
- `OTHER MEASURED PATTERNS` completes the sound profile without interpreting every small
  delta as a finding.
- Case IDs appear only with the measurements and timestamps that make them useful.

Decision: recommended agent report. Iteration 3 remains a useful compact human mode.

## Recommended rules

### Progress

- Print one `.` only when a case is fully scored.
- Print `E` for a terminal case error and explain errors after the progress block.
- Use append-only lines rather than carriage-return animation.
- Put progress on stderr and the final report on stdout.
- Keep download, cache, HTTP, and queue events behind verbose modes; they diagnose the
  runner, not model quality.

### Final report

- Show status, duration, and run ID once.
- Separate primary fit metrics from diagnostic metrics.
- Use P90 only for lower-is-better error tails. Use `worst` for correlation and speed.
- Put model value, tail, baseline magnitude, and paired prevalence on one metric row.
- Report only one side of complementary counts.
- Filter and rank findings and evidence by normalized signal strength rather than
  count. The default `1.0x` floor means a diagnostic-specific selection rule is met;
  omit the section when no finding clears it.
- Every significant finding must add at least one fact not already in the scorecard: prevalence,
  contribution to total error, affected group, case IDs, or time regions.
- Avoid absolute labels such as `excellent` unless a validated threshold is supplied.
- Omit unavailable comparisons instead of filling the report with `N/A` explanations.

### Agent output

The text layout should use stable headings and labels, but a lossless structured mode
should also exist:

```text
--format agent   self-contained iteration-4 diagnostic report (recommended default)
--format text    compact iteration-3 report
--format json    one versioned result object, no progress on stdout
--format jsonl   versioned lifecycle events followed by the result object
```

The structured summary should preserve the same information architecture:

```json
{
  "schema_version": "top-arena-cli-summary-v1",
  "status": "completed",
  "run": {},
  "coverage": {},
  "global_fit": {},
  "sound_profile": {
    "tone_bands": {},
    "envelope_phases": {},
    "timing": {},
    "dynamics": {},
    "silence": {},
    "controls": {},
    "input_patterns": {}
  },
  "findings": [
    {
      "id": "P1",
      "rank": 1,
      "type": "significant_finding",
      "confidence": "high",
      "finding": "candidate has excess 2–4 kHz energy during high-gain attacks",
      "measurement": {},
      "scope": {},
      "uncertainty": {},
      "baseline_context": {},
      "evidence": [],
      "interpretation": {"level": "hypothesis", "text": "..."}
    }
  ],
  "cases": [
    {
      "case_id": "...",
      "controls": {},
      "input_features": {},
      "metrics": {},
      "tone_bands": {},
      "envelope_phases": {},
      "top_error_regions": []
    }
  ],
  "details_url": "https://..."
}
```

The JSON should embed every case-level summary and every time region used by a finding.
Links are navigation aids, not substitutes for evidence. Full 100 ms series can remain
behind a separate explicit detail option because embedding every point for every case may
be unnecessarily large, but no reported conclusion may depend on an omitted point.

## Information needed for the recommended report

The existing result already provides aggregate metrics and per-case scores. Producing the
full recommended report additionally requires deterministic analysis of:

- Paired candidate-versus-NAM wins and per-case changes.
- Each worst case's contribution to total error.
- Metric breakdowns by dry loop, position, and control values.
- Threshold-free ranking of notable level, peak, and correlation patterns.
- The 100 ms regions that support each selected finding.

These should be computed facts. The CLI can then render cautious templates from them; it
does not need an unconstrained language model to invent diagnoses.

The complete calculation and evidence design is specified in
[`agent-diagnostic-analysis.md`](agent-diagnostic-analysis.md).
