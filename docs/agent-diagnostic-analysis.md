# Agent-ready diagnostic analysis

> Implementation status: `top-arena-case-diagnostics-v1` and
> `top-arena-run-diagnostics-v7` implement signed fixed-band energy, signed global
> dynamics, dry-anchored attack/body/sustain regions, paired NAM log ratios with a
> input-chunk cluster bootstrap, error concentration, condition associations, exact
> control-setting and case evidence, and nearest-training-position distance analysis.
> Sections describing standardized
> loudness, release/decay, gain curves, isolated control response, and calibrated probes
> remain the next analysis-contract work rather than claims made by v1.

This document proposes the calculations needed to turn a completed Top Arena run into a
self-contained diagnostic report for an AI agent. The report should remove the need to
fetch cases, calculate deltas, identify time regions, or infer vague meanings from metric
names.

This is a design specification, not a description of measurements already implemented.
Every new field must be versioned and tested before it can be presented as benchmark data.

## Output objective

An agent should be able to answer all of these questions from one result:

- Did the run complete correctly, and is it comparable with the baseline?
- Is the model broadly better or worse than the paired NAM baseline?
- Is error widespread or concentrated in a small tail?
- Does the candidate have more or less bass, mids, presence, or treble than the reference?
- Do differences happen during attack, body, sustain, release, or silence?
- Does the candidate react correctly to input level and amp controls?
- Are timing, polarity, loudness, peaks, noise, or dynamics responsible for a mismatch?
- Which exact control settings, dry inputs, and time regions support each conclusion?
- Which measured patterns have the strongest signal?
- Which questions cannot be answered from the current corpus?

The result should contain computed evidence, not instructions such as `inspect case-a`.

## Evidence language

Every diagnostic statement should carry one of three evidence levels:

- `measured`: a direct, versioned calculation, such as `presence energy +1.8 dB`.
- `derived`: a deterministic summary of measurements, such as `42/150 cases share the
  same positive presence error`.
- `hypothesis`: a cautious interpretation, such as `this pattern may sound more forward
  or harsh`.
Sound adjectives must never appear alone. `Brighter`, `darker`, `boomy`, `thin`, `harsh`,
`fizzy`, `compressed`, and `slow attack` need signed measurements, frequency bounds or
time bounds, scope, and uncertainty.

## 1. Global fidelity and paired baseline

Keep the existing ESR, human-weighted ESR, MRSTFT, level delta, peak delta, correlation,
and realtime summaries. Add paired candidate-versus-NAM analysis for every metric
available on both outputs.

For a lower-is-better metric `m`, define per-case paired log ratio:

```text
d_i = log((candidate_i + epsilon) / (nam_i + epsilon))
```

- `d_i < 0`: candidate is lower than NAM.
- `d_i = 0`: equal within numeric precision.
- `d_i > 0`: candidate is higher than NAM.

Report:

- Paired case count.
- Cases where the candidate is better, reporting only that side of the binary count.
- Median paired log ratio and its back-transformed percentage.
- Mean aggregate percentage for continuity with the leaderboard.
- Median absolute paired change.
- A 95% interval from a cluster bootstrap over input chunks, keeping all control settings
  for one input chunk together.

The input-chunk cluster bootstrap avoids pretending that all 150 cases are independent
when ten settings reuse one dry performance. Do not convert this interval into a causal or
perceptual-significance claim.

### Error concentration

For an equal-case benchmark metric such as ESR, define the share carried by a set `S`:

```text
share(S) = sum(ESR_i for i in S) / sum(ESR_i for every case)
```

Report the smallest number of cases accounting for 25%, 50%, and 75% of summed case ESR.
This directly distinguishes systemic error from isolated outliers. Label it `share of
summed case ESR`, not `share of signal-error energy`, because those are different
weightings.

## 2. Signed tonal balance

The current level and peak deltas are absolute and cannot say `more bass` or `less
treble`. Add signed, reference-relative band energy.

For frequency band `b`:

```text
band_delta_db(b) = 10 log10((candidate_energy_b + epsilon)
                            / (reference_energy_b + epsilon))
```

- Positive: candidate has more energy than the reference in that band.
- Negative: candidate has less energy.
- Zero: matched band energy.

Calculate the same signed delta for NAM and calculate candidate-minus-NAM only as a
secondary comparison. Restrict aggregation to reference-active frames so silence does
not dominate ratios.

### Versioned display bands

Natural-language band names have no universal boundaries. Top Arena should publish fixed
bounds as part of the analysis contract:

| Display name | Range | Intended reading |
|---|---:|---|
| Sub/rumble | 20–80 Hz | Very low energy below most guitar fundamentals. |
| Bass | 80–150 Hz | Low fundamentals and weight. |
| Low mids | 150–400 Hz | Body, warmth, or possible muddiness. |
| Mids | 400–800 Hz | Central body and box-like coloration. |
| Upper mids | 800–2,000 Hz | Definition and forwardness. |
| Presence | 2,000–4,000 Hz | Pick articulation and edge. |
| Treble | 4,000–8,000 Hz | Brightness, bite, and high-order content. |
| High treble | 8,000–20,000 Hz | Noise-like edge and very high-frequency content. |

These descriptions are hypotheses about possible perception, not alternate metric names.
Internally, finer Bark or ERB bands can preserve resolution; the table is the stable
display aggregation. The analysis should also report:

- Error-energy share per band.
- Median signed delta, P10/P90 signed delta, and cases with consistent direction.
- Spectral-tilt error in dB per octave from a robust regression of signed spectral delta
  against log-frequency.
- Spectral centroid and roll-off deltas only when they add information not already clear
  from the band profile.
- Spectral flux delta around onsets as a description of changing spectral energy.

Avoid a long list of correlated descriptors. If `+2.1 dB from 2–8 kHz` fully explains a
centroid increase, report the band finding and keep the centroid in structured detail.

## 3. Attack, body, sustain, and release

Detect onset anchors from the shared dry input, not independently from candidate and
reference. This gives both wet outputs identical events and prevents onset-detector
differences from becoming model differences.

For each sufficiently isolated onset, calculate:

### Attack

- Envelope attack time from 20% to 90% of local peak.
- Candidate-minus-reference attack-time delta in milliseconds.
- Onset lag from local cross-correlation in milliseconds.
- Peak-time delta in milliseconds.
- Signed transient energy delta over the first 50 ms.
- Signed peak overshoot in dB.
- Transient spectral-flux ratio.
- Band deltas during the transient window.

The 20%–90% envelope definition follows established attack-time descriptor practice. A
statement such as `attack is softer` should expand to evidence like:

```text
Candidate reaches 90% envelope 8.4 ms later (median over 37 isolated onsets),
has -1.6 dB transient energy in the first 50 ms, and has lower spectral flux
on 31/37 onsets.
```

Only after that measurement may the report add the hypothesis `likely softer or less
immediate pick attack`.

### Body and sustain

Use versioned, onset-relative regions, skipping events that do not have enough separation:

- `transient`: 0–50 ms after dry onset.
- `early body`: 50–200 ms.
- `sustain`: 200 ms until the next onset or detected release.

For every region, report signed RMS/loudness delta, ESR, correlation, crest-factor delta,
and tonal-band deltas. Compare region shares of total error to answer whether the model
fails mainly on transients or steady content.

### Decay and release

When the dry input contains a sufficiently long decay or gap, calculate:

- Envelope decay slope difference in dB/s.
- Time-to-decay by 20 dB relative to local peak.
- Sustain-level delta before release.
- Release-tail energy delta.
- Time at which output reaches the versioned silence threshold.

If the loop lacks isolated releases, say `release behavior not covered` once. Do not infer
release or gate quality from dense passages.

## 4. Loudness, peaks, and dynamics

Add signed measurements so the report can explain the existing absolute deltas:

- Candidate-minus-reference integrated loudness in LU using a versioned BS.1770 method.
- Candidate-minus-reference momentary loudness in active windows.
- Sample-peak delta and optional true-peak delta in dBTP.
- Crest-factor delta: `(peak - RMS)_candidate - (peak - RMS)_reference` in dB.
- DC-offset difference.
- Positive/negative peak asymmetry difference.

True-peak and programme loudness should use a named standard rather than an ad hoc
formula. They complement the current RMS and sample-peak metrics; they must not silently
replace them.

### Level-dependent behavior

For every active 100 ms window, calculate wet gain relative to dry input:

```text
gain_ref  = level_ref  - level_dry
gain_cand = level_cand - level_dry
gain_error = gain_cand - gain_ref
```

Bin windows by dry-input level and fit robust gain curves per control position. Report:

- Gain error at low, medium, and high input levels.
- Difference between candidate and reference gain-curve slope.
- Dynamic-range difference.
- Whether a signed error grows consistently with input level.

This can support `candidate under-compresses high-level passages relative to reference`
when high-level gain error is systematically positive and the candidate gain curve is
less compressive. It cannot identify which internal amplifier stage caused the behavior.

### Silence, noise, and gating

Using dry-silent or dry-near-silent windows defined by the versioned contract, report:

- Candidate and reference noise-floor level.
- Signed residual-noise difference.
- High-frequency share of residual noise.
- Gate closure lag and release-tail truncation when the material contains suitable gaps.

Do not call high-frequency residual `aliasing` from programme material alone.

## 5. Timing, shape, and phase-related evidence

The current zero-lag correlation should be supplemented with:

- Best local lag from bounded cross-correlation, in samples and milliseconds.
- Correlation before and after that small alignment.
- Polarity agreement.
- Frequency-dependent magnitude-squared coherence or equivalent cross-spectral agreement.
- Error-energy change after local alignment.

Interpretation examples:

- Correlation improves strongly after a 0.4 ms shift: residual timing mismatch is a major
  contributor.
- Correlation remains low after alignment while level is matched: waveform shape or
  nonlinear behavior differs.
- Correlation is high but signed band levels differ: tonal/gain balance is a clearer
  description than waveform-shape failure.

Raw phase difference on nonlinear, polyphonic material is difficult to explain and should
remain structured evidence unless it forms a stable frequency-dependent pattern.

## 6. Amp-control fidelity

The same dry loops appear at multiple static positions, so the report should analyze how
the candidate follows the reference as controls change.

For each named control and each sound descriptor:

- Group exact settings and retain the full position vector.
- Compare candidate and reference response range from minimum to maximum observed setting.
- Compare the signed slope of descriptor versus control value.
- Count direction disagreements between adjacent settings.
- Identify regions where error grows, not merely the single worst setting.
- Test pairwise control interactions when the position design supports them.

Examples of useful conclusions:

```text
Bass-control range is compressed: reference bass-band response spans 5.8 dB,
candidate spans 3.6 dB (62% of reference range), consistent on 13/15 dry loops.

Presence error begins above gain=0.7 and grows from +0.3 dB to +2.2 dB;
the lower-gain region remains within +/-0.4 dB.
```

Do not label a control as the cause when controls covary in the sampled position design.
Report `associated with` unless the design isolates that control.

## 7. Input-content patterns

Case IDs are not useful descriptions. Characterize each dry loop with measured features:

- RMS level and crest factor.
- Onset density and median inter-onset interval.
- Transient-to-sustain energy ratio.
- Low-, mid-, and high-band energy shares.
- Spectral centroid/roll-off where useful.
- Silence/gap share.
- Pitch range or salience only when estimation confidence is sufficient.

Use these features to express scope in audio terms:

```text
The regression affects 11/12 high-onset-density cases but only 3/38 sparse cases.
Error rises with dry crest factor (robust slope +0.018 ESR per dB; loop-bootstrap
95% interval +0.010 to +0.026).
```

Do not assign semantic labels such as `palm-muted`, `single-coil`, or `lead` unless the
corpus contains verified labels. Derived labels should stay literal: `bass-heavy`,
`dense-onset`, `high-level`, or `sparse` with their defining thresholds included.

## 8. Complete evidence packet for every reported finding

Every reported finding should be self-contained:

```text
id: P1
type: weakness
finding: Candidate has excess presence during high-gain attacks.
measurement:
  band: 2,000–4,000 Hz
  signed_delta: +1.8 dB median
  paired_nam_context: +0.6 dB
scope:
  cases: 42/150
  dry_loops: 12/15
  positions: 08–10
  condition: gain >= 0.7
uncertainty:
  loop_bootstrap_95: [+1.3, +2.2] dB
time_character:
  transient: +2.5 dB
  early_body: +1.4 dB
  sustain: +0.3 dB
evidence:
  - case_id: ...position-09
    controls: {gain: 0.9, bass: 0.5, mid: 0.5, treble: 0.5}
    regions:
      - 1.20–1.28 s: +3.1 dB presence, attack +7.8 ms
      - 4.62–4.69 s: +2.8 dB presence, transient energy -1.1 dB
interpretation:
  level: hypothesis
  text: May sound more forward or harsh while the pick attack is slightly softened.
```

The packet gives an agent the calculations, conditions, and timestamps without another
API request. It intentionally contains no recommended action. Keep all ranked evidence
cases with normalized signal strength in structured output and let the renderer apply its
configurable signal floor.

## 9. Selecting strengths and significant findings

Do not generate a single opaque `quality score`. Metrics have different scales and describe
different failure modes.

Rank candidate findings lexicographically using transparent fields:

1. Evidence reliability: measured pattern and loop-bootstrap stability.
2. Scope: affected loops, positions, cases, and time windows.
3. Primary impact: effect on ESR, human-weighted ESR, or MRSTFT.
4. Baseline context: systematic regression or smallest improvement versus NAM/previous run.
5. Reproducibility: whether exact conditions, case IDs, and time regions are available.

Include every finding that reaches its published signal threshold. Do not impose a count
cap; threshold strength, not list position, determines inclusion.

Two findings supported by the same underlying evidence should be merged. For example, excess
presence, increased centroid, and higher treble energy may be one tonal finding, not three
weaknesses.

## 10. Questions requiring dedicated probes

The existing guitar-loop corpus cannot cleanly isolate every amplifier property. Add a
separate probe suite before reporting these as measurements:

| Question | Required probe |
|---|---|
| Linearized frequency response by level | Low-level and multi-level swept sine or noise. |
| Harmonic distortion by order | Sine tones or synchronized exponential swept sine. |
| Intermodulation distortion | Two-tone or multitone signals. |
| Aliasing-to-signal ratio | Controlled tones/sweeps with a defined in-band alias mask. |
| Compression attack/release constants | Calibrated level steps or bursts. |
| Hysteresis and long memory | Repeated probes with controlled preceding signal history. |
| Control interpolation between trained points | Isolated dense control sweeps. |
| Sample-rate robustness | Equivalent probes at multiple sample rates. |

Synchronized swept-sine methods can separate nonlinear harmonic responses, and recent
neural-amp work treats aliasing as a metric separate from ESR. Until such probes exist,
the report may say `excess high-frequency residual` but not `the model aliases`.

## 11. Research basis and limits

- ESR is established in neural guitar-amplifier modelling, while listening studies show
  why time-domain error alone is not a perceptual verdict: [Real-Time Guitar Amplifier
  Emulation with Deep Learning](https://www.mdpi.com/2076-3417/10/3/766) and
  [Perceptual Loss Function for Neural Modelling of Audio
  Systems](https://arxiv.org/abs/1911.08922).
- Multi-resolution STFT losses capture time-frequency structure at multiple resolutions:
  [Parallel WaveGAN](https://arxiv.org/abs/1910.11480).
- Attack time based on envelope thresholds, spectral flux, perceptual bands, and common
  spectral descriptors are documented by [Essentia's attack-time
  descriptor](https://essentia.upf.edu/reference/std_LogAttackTime.html) and
  [music extractor](https://essentia.upf.edu/streaming_extractor_music.html).
- Loudness and true-peak measurements should follow a published contract such as
  [ITU-R BS.1770-5](https://www.itu.int/rec/R-REC-BS.1770-5-202311-I).
- Perceptual objective measures require domain validation and are aids rather than
  replacements for listening: [ITU-R
  BS.1387-2](https://www.itu.int/rec/R-REC-BS.1387-2-202305-I/en).
- Controllable amp models must be evaluated across the control range, not only at static
  snapshots: [End-to-End Amp Modeling](https://arxiv.org/abs/2403.08559).
- Harmonic separation for nonlinear systems requires controlled excitation:
  [Simultaneous Measurement of Impulse Response and Distortion with a Swept-Sine
  Technique](https://angelofarina.it/Public/Papers/134-AES00.PDF) and [Nonlinear System
  Identification Using Exponential Swept-Sine
  Signal](https://www.ant-novak.com/publications/papers/2010_ieee_novak.pdf).
- Aliasing deserves its own controlled measurement rather than an inference from ESR:
  [Aliasing Reduction in Neural Amp Modeling by Smoothing
  Activations](https://arxiv.org/abs/2505.04082).

These sources motivate descriptors and test design. They do not validate universal Top
Arena quality thresholds. Thresholds and natural-language interpretations still require
calibration against this corpus and controlled listening results.
