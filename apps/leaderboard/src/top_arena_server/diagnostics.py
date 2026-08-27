from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from numpy.typing import NDArray
from scipy import signal, stats

if TYPE_CHECKING:
    from .models import BenchmarkCase, RunCase

Audio = NDArray[np.float32] | NDArray[np.float64]

_EPSILON = 1e-12
_DB_FLOOR = -120.0
_NAM_FULL_REALTIME_TARGET = 31.0
_ACCEPTABLE_REALTIME_FRACTION = 0.5
_BASELINE_REGRESSION_PERCENT = 5.0
_BASELINE_WORSE_CASE_FRACTION = 0.6
_TONAL_FINDING_DB = 0.75
_TONAL_DIRECTION_FRACTION = 0.75
_TONAL_EVIDENCE_DB = 1.5
_ERROR_CONCENTRATION_FRACTION = 0.25
_CASE_ESR_MULTIPLE = 2.0
_ATTACK_FINDING_MS = 2.0
_ASSOCIATION_RHO = 0.5
_BANDS = (
    ("sub", "Sub / rumble", 20.0, 80.0, "very-low energy or rumble"),
    ("bass", "Bass", 80.0, 150.0, "low-end weight"),
    ("low_mids", "Low mids", 150.0, 400.0, "body, warmth, or muddiness"),
    ("mids", "Mids", 400.0, 800.0, "central body or box-like colour"),
    ("upper_mids", "Upper mids", 800.0, 2_000.0, "definition or forwardness"),
    ("presence", "Presence", 2_000.0, 4_000.0, "pick articulation or edge"),
    ("treble", "Treble", 4_000.0, 8_000.0, "brightness, bite, or high-order content"),
    ("high_treble", "High treble", 8_000.0, 20_000.0, "noise-like high-frequency edge"),
)
_PHASES = (
    ("transient", "Transient", 0.0, 0.05),
    ("early_body", "Early body", 0.05, 0.2),
    ("sustain", "Sustain", 0.2, 0.5),
)
_ERROR_METRICS = (
    ("esr", "ESR"),
    ("human_weighted_esr", "Human-weighted ESR"),
    ("mrstft", "MRSTFT"),
)


def _mono(value: Audio) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim == 2:
        array = array.mean(axis=1)
    if array.ndim != 1:
        msg = "audio must be mono samples or samples-by-channels"
        raise ValueError(msg)
    return np.asarray(np.nan_to_num(array, nan=0.0, posinf=1.0, neginf=-1.0))


def _fit(value: NDArray[np.float64], samples: int) -> NDArray[np.float64]:
    if len(value) < samples:
        return np.pad(value, (0, samples - len(value)))
    return value[:samples]


def _db(value: float, *, power: bool = False) -> float:
    multiplier = 10.0 if power else 20.0
    return max(_DB_FLOOR, multiplier * math.log10(max(value, _EPSILON)))


def _rms(value: NDArray[np.float64]) -> float:
    return math.sqrt(float(np.mean(value * value))) if len(value) else 0.0


def _signed_level_delta(reference: NDArray[np.float64], candidate: NDArray[np.float64]) -> float:
    return float(np.clip(_db(_rms(candidate)) - _db(_rms(reference)), -120.0, 120.0))


def _peak_db(value: NDArray[np.float64]) -> float:
    return _db(float(np.max(np.abs(value)))) if len(value) else _DB_FLOOR


def _crest_db(value: NDArray[np.float64]) -> float:
    return _peak_db(value) - _db(_rms(value))


def _band_profile(
    reference: NDArray[np.float64],
    candidate: NDArray[np.float64],
    sample_rate: int,
) -> list[dict[str, Any]]:
    if len(reference) < 2:
        return []
    segment = min(4_096, len(reference))
    overlap = segment // 2
    frequencies, reference_psd = signal.welch(
        reference,
        fs=sample_rate,
        window="hann",
        nperseg=segment,
        noverlap=overlap,
        scaling="spectrum",
    )
    _, candidate_psd = signal.welch(
        candidate,
        fs=sample_rate,
        window="hann",
        nperseg=segment,
        noverlap=overlap,
        scaling="spectrum",
    )
    _, error_psd = signal.welch(
        candidate - reference,
        fs=sample_rate,
        window="hann",
        nperseg=segment,
        noverlap=overlap,
        scaling="spectrum",
    )
    reference_total = max(float(np.sum(reference_psd)), _EPSILON)
    candidate_total = max(float(np.sum(candidate_psd)), _EPSILON)
    error_total = max(float(np.sum(error_psd)), _EPSILON)
    result: list[dict[str, Any]] = []
    nyquist = sample_rate / 2
    for band_id, name, low, configured_high, interpretation in _BANDS:
        high = min(configured_high, nyquist)
        if low >= high:
            continue
        mask = (frequencies >= low) & (frequencies < high)
        reference_energy = float(np.sum(reference_psd[mask]))
        candidate_energy = float(np.sum(candidate_psd[mask]))
        error_energy = float(np.sum(error_psd[mask]))
        signed_delta = 10.0 * math.log10(
            (candidate_energy + _EPSILON) / (reference_energy + _EPSILON)
        )
        result.append(
            {
                "id": band_id,
                "name": name,
                "hz": [low, high],
                "signed_delta_db": float(np.clip(signed_delta, -120.0, 120.0)),
                "reference_energy_share": reference_energy / reference_total,
                "candidate_energy_share": candidate_energy / candidate_total,
                "error_energy_share": error_energy / error_total,
                "sound_hypothesis": interpretation,
            }
        )
    return result


def _frame_rms(value: NDArray[np.float64], frame: int, hop: int) -> NDArray[np.float64]:
    if len(value) < frame:
        value = np.pad(value, (0, frame - len(value)))
    frames = np.lib.stride_tricks.sliding_window_view(value, frame)[::hop]
    return np.sqrt(np.mean(frames * frames, axis=1))


def _dry_onsets(dry: NDArray[np.float64], sample_rate: int) -> NDArray[np.int64]:
    frame = max(8, round(0.02 * sample_rate))
    hop = max(1, round(0.01 * sample_rate))
    envelope = _frame_rms(dry, frame, hop)
    if not len(envelope) or float(np.max(envelope)) <= 1e-8:
        return np.array([], dtype=np.int64)
    novelty = np.maximum(np.diff(envelope, prepend=0.0), 0.0)
    median = float(np.median(novelty))
    mad = float(np.median(np.abs(novelty - median)))
    threshold = max(float(np.max(novelty)) * 0.08, median + 3.0 * mad, 1e-8)
    peaks, properties = signal.find_peaks(
        novelty,
        height=threshold,
        distance=max(1, round(0.05 * sample_rate / hop)),
    )
    if not len(peaks):
        return np.array([], dtype=np.int64)
    heights = cast("NDArray[np.float64]", properties["peak_heights"])
    active = envelope[peaks] >= float(np.max(envelope)) * 0.02
    selected = peaks[active] if np.any(active) else peaks[np.argsort(heights)[-1:]]
    return np.asarray(selected * hop, dtype=np.int64)


def _attack_time_ms(value: NDArray[np.float64], onset: int, sample_rate: int) -> float | None:
    stop = min(len(value), onset + round(0.12 * sample_rate))
    local = np.abs(value[onset:stop])
    if len(local) < 4:
        return None
    smooth_samples = max(1, round(0.002 * sample_rate))
    envelope = np.sqrt(
        np.convolve(local * local, np.ones(smooth_samples) / smooth_samples, mode="same")
    )
    peak_index = int(np.argmax(envelope))
    peak = float(envelope[peak_index])
    if peak <= 1e-8:
        return None
    attack = envelope[: peak_index + 1]
    below = np.flatnonzero(attack >= peak * 0.2)
    above = np.flatnonzero(attack >= peak * 0.9)
    if not len(below) or not len(above):
        return None
    return max(0.0, float(above[0] - below[0]) * 1_000.0 / sample_rate)


def _phase_diagnostics(
    reference: NDArray[np.float64],
    candidate: NDArray[np.float64],
    onsets: NDArray[np.int64],
    sample_rate: int,
) -> tuple[dict[str, Any], list[float]]:
    total_error = max(float(np.sum((candidate - reference) ** 2)), _EPSILON)
    phases: dict[str, Any] = {}
    for phase_id, name, start_seconds, stop_seconds in _PHASES:
        reference_segments: list[NDArray[np.float64]] = []
        candidate_segments: list[NDArray[np.float64]] = []
        for index, onset in enumerate(onsets):
            start = int(onset + round(start_seconds * sample_rate))
            stop = int(onset + round(stop_seconds * sample_rate))
            if index + 1 < len(onsets):
                stop = min(stop, int(onsets[index + 1]))
            stop = min(stop, len(reference))
            if stop > start:
                reference_segments.append(reference[start:stop])
                candidate_segments.append(candidate[start:stop])
        if not reference_segments:
            continue
        reference_phase = np.concatenate(reference_segments)
        candidate_phase = np.concatenate(candidate_segments)
        error = candidate_phase - reference_phase
        phases[phase_id] = {
            "name": name,
            "window_ms": [round(start_seconds * 1_000), round(stop_seconds * 1_000)],
            "event_count": len(reference_segments),
            "signed_level_delta_db": _signed_level_delta(reference_phase, candidate_phase),
            "crest_delta_db": _crest_db(candidate_phase) - _crest_db(reference_phase),
            "error_energy_share": float(np.sum(error * error)) / total_error,
            "bands": _band_profile(reference_phase, candidate_phase, sample_rate),
        }
    attack_deltas: list[float] = []
    for onset in onsets:
        reference_attack = _attack_time_ms(reference, int(onset), sample_rate)
        candidate_attack = _attack_time_ms(candidate, int(onset), sample_rate)
        if reference_attack is not None and candidate_attack is not None:
            attack_deltas.append(candidate_attack - reference_attack)
    return phases, attack_deltas


def _top_regions(
    reference: NDArray[np.float64],
    candidate: NDArray[np.float64],
    sample_rate: int,
) -> list[dict[str, Any]]:
    window = max(1, round(0.1 * sample_rate))
    region_values: list[tuple[float, int, NDArray[np.float64], NDArray[np.float64]]] = []
    total_error = max(float(np.sum((candidate - reference) ** 2)), _EPSILON)
    for start in range(0, len(reference), window):
        reference_window = reference[start : start + window]
        candidate_window = candidate[start : start + window]
        energy = float(np.sum((candidate_window - reference_window) ** 2))
        region_values.append((energy, start, reference_window, candidate_window))
    result: list[dict[str, Any]] = []
    ordered_regions = sorted(region_values, reverse=True)[:3]
    for energy, start, reference_window, candidate_window in ordered_regions:
        bands = _band_profile(reference_window, candidate_window, sample_rate)
        dominant = max(bands, key=lambda item: cast("float", item["error_energy_share"]))
        result.append(
            {
                "start_seconds": start / sample_rate,
                "stop_seconds": min(start + window, len(reference)) / sample_rate,
                "error_energy_share": energy / total_error,
                "signed_level_delta_db": _signed_level_delta(reference_window, candidate_window),
                "dominant_error_band": dominant["id"],
                "dominant_band_delta_db": dominant["signed_delta_db"],
            }
        )
    return result


def _band_regions(
    reference: NDArray[np.float64],
    candidate: NDArray[np.float64],
    sample_rate: int,
) -> dict[str, dict[str, Any]]:
    window_samples = max(2, round(0.1 * sample_rate))
    records: dict[str, list[tuple[float, float, float, int, int]]] = defaultdict(list)
    for start in range(0, len(reference), window_samples):
        stop = min(start + window_samples, len(reference))
        reference_window = reference[start:stop]
        candidate_window = candidate[start:stop]
        if len(reference_window) < 2:
            continue
        window = signal.windows.hann(len(reference_window), sym=False)
        frequencies = np.fft.rfftfreq(len(reference_window), 1.0 / sample_rate)
        reference_power = np.abs(np.fft.rfft(reference_window * window)) ** 2
        candidate_power = np.abs(np.fft.rfft(candidate_window * window)) ** 2
        error_power = np.abs(np.fft.rfft((candidate_window - reference_window) * window)) ** 2
        for band_id, _name, low, configured_high, _interpretation in _BANDS:
            high = min(configured_high, sample_rate / 2)
            mask = (frequencies >= low) & (frequencies < high)
            records[band_id].append(
                (
                    float(np.sum(reference_power[mask])),
                    float(np.sum(candidate_power[mask])),
                    float(np.sum(error_power[mask])),
                    start,
                    stop,
                )
            )
    result: dict[str, dict[str, Any]] = {}
    for band_id, entries in records.items():
        max_reference = max(entry[0] for entry in entries)
        if max_reference <= _EPSILON:
            continue
        active = [entry for entry in entries if entry[0] >= max_reference * 0.01]
        total_error = max(sum(entry[2] for entry in active), _EPSILON)

        def signed_delta(entry: tuple[float, float, float, int, int]) -> float:
            return 10.0 * math.log10((entry[1] + _EPSILON) / (entry[0] + _EPSILON))

        selected = max(active, key=lambda entry: abs(signed_delta(entry)))
        delta = float(np.clip(signed_delta(selected), -120.0, 120.0))
        result[band_id] = {
            "start_seconds": selected[3] / sample_rate,
            "stop_seconds": selected[4] / sample_rate,
            "selected_band": band_id,
            "selected_band_delta_db": delta,
            "band_error_energy_share": selected[2] / total_error,
            "selection": "largest absolute signed band delta among reference-active 100 ms windows",
        }
    return result


def calculate_case_diagnostics(
    dry: Audio,
    reference: Audio,
    candidate: Audio,
    *,
    sample_rate: int,
) -> dict[str, Any]:
    """Return versioned, signed diagnostics for one aligned benchmark case."""
    if sample_rate <= 0:
        msg = "sample rate must be positive"
        raise ValueError(msg)
    reference_array = _mono(reference)
    if not len(reference_array):
        msg = "reference audio must contain at least one sample"
        raise ValueError(msg)
    samples = len(reference_array)
    candidate_array = _fit(_mono(candidate), samples)
    dry_array = _fit(_mono(dry), samples)
    onsets = _dry_onsets(dry_array, sample_rate)
    phases, attack_deltas = _phase_diagnostics(
        reference_array, candidate_array, onsets, sample_rate
    )
    reference_peak = _peak_db(reference_array)
    candidate_peak = _peak_db(candidate_array)
    dry_levels = _frame_rms(
        dry_array,
        max(8, round(0.1 * sample_rate)),
        max(1, round(0.1 * sample_rate)),
    )
    active_floor = max(float(np.max(dry_levels)) * 1e-3, 1e-8)
    active_levels = dry_levels[dry_levels >= active_floor]
    dynamic_range = (
        20.0
        * math.log10(
            max(float(np.quantile(active_levels, 0.9)), _EPSILON)
            / max(float(np.quantile(active_levels, 0.1)), _EPSILON)
        )
        if len(active_levels) >= 2
        else 0.0
    )
    return {
        "version": "top-arena-case-diagnostics-v1",
        "signed": {
            "level_delta_db": _signed_level_delta(reference_array, candidate_array),
            "peak_delta_db": candidate_peak - reference_peak,
            "crest_delta_db": _crest_db(candidate_array) - _crest_db(reference_array),
            "dc_offset_delta": float(np.mean(candidate_array) - np.mean(reference_array)),
        },
        "bands": _band_profile(reference_array, candidate_array, sample_rate),
        "onsets": {
            "detector": "dry RMS novelty; 20 ms frames / 10 ms hop",
            "event_count": len(onsets),
            "attack_time_delta_ms_median": (
                float(np.median(attack_deltas)) if attack_deltas else None
            ),
            "attack_time_event_count": len(attack_deltas),
        },
        "phases": phases,
        "top_regions": _top_regions(reference_array, candidate_array, sample_rate),
        "band_regions": _band_regions(reference_array, candidate_array, sample_rate),
        "input": {
            "level_dbfs": _db(_rms(dry_array)),
            "crest_db": _crest_db(dry_array),
            "dynamic_range_db": dynamic_range,
            "onset_rate_hz": len(onsets) / max(len(dry_array) / sample_rate, _EPSILON),
        },
    }


def _summary(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "cases": 0,
            "median": None,
            "p10": None,
            "p90": None,
            "positive_cases": 0,
        }
    array = np.asarray(values, dtype=np.float64)
    return {
        "cases": len(values),
        "median": float(np.median(array)),
        "p10": float(np.quantile(array, 0.1)),
        "p90": float(np.quantile(array, 0.9)),
        "positive_cases": int(np.sum(array > 0)),
    }


def _number_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _joint_signal(*components: float) -> float:
    """Require every component to pass 1x, then retain above-threshold magnitude."""
    if not components:
        return 0.0
    if any(component < 1.0 for component in components):
        return min(components)
    return math.prod(components) ** (1.0 / len(components))


def _benchmark_case(row: RunCase) -> BenchmarkCase | None:
    try:
        return row.benchmark_case
    except (AttributeError, TypeError):
        return None


def _case_context(row: RunCase) -> dict[str, Any]:
    benchmark_case = _benchmark_case(row)
    context: dict[str, Any] = {"case_id": str(row.benchmark_case_id)}
    if benchmark_case is None:
        return context
    context["input_chunk"] = int(benchmark_case.chunk_index) + 1
    context["control_setting_id"] = int(benchmark_case.position_index) + 1
    matrix = benchmark_case.position_matrix
    if matrix:
        names = getattr(getattr(benchmark_case, "amp", None), "control_names", [])
        steps = [
            {
                str(names[index] if index < len(names) else f"control_{index + 1}"): float(value)
                for index, value in enumerate(values)
            }
            for values in matrix
        ]
        if len(steps) == 1:
            context["controls"] = steps[0]
        else:
            context["control_sequence"] = steps
    return context


def _cluster_bootstrap_log_ratio(
    pairs: Sequence[tuple[int, float]],
) -> list[float] | None:
    clusters: dict[int, list[float]] = defaultdict(list)
    for cluster, value in pairs:
        clusters[cluster].append(value)
    keys = sorted(clusters)
    if len(keys) < 2:
        return None
    rng = np.random.default_rng(20_260_827)
    samples = np.empty(500, dtype=np.float64)
    for index in range(len(samples)):
        selected = rng.choice(keys, size=len(keys), replace=True)
        values = [value for key in selected for value in clusters[int(key)]]
        samples[index] = np.median(values)
    interval = np.quantile(samples, [0.025, 0.975])
    return [float((1.0 - math.exp(value)) * 100.0) for value in interval[::-1]]


def _paired_baseline(rows: Sequence[RunCase]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field, label in _ERROR_METRICS:
        pairs: list[tuple[RunCase, float, float]] = []
        for row in rows:
            candidate = getattr(row, field, None)
            baseline = getattr(row, f"nam_{field}", None)
            if candidate is not None and baseline is not None:
                pairs.append((row, float(candidate), float(baseline)))
        if not pairs:
            continue
        log_values = [
            math.log((candidate + _EPSILON) / (baseline + _EPSILON))
            for _row, candidate, baseline in pairs
        ]
        clustered: list[tuple[int, float]] = []
        for (row, _candidate, _baseline), value in zip(pairs, log_values, strict=True):
            benchmark_case = _benchmark_case(row)
            cluster = int(getattr(benchmark_case, "chunk_index", len(clustered)))
            clustered.append((cluster, value))
        median_log = float(np.median(log_values))
        result[field] = {
            "label": label,
            "cases": len(pairs),
            "candidate_better_cases": sum(
                candidate < baseline for _row, candidate, baseline in pairs
            ),
            "median_candidate_improvement_percent": (1.0 - math.exp(median_log)) * 100.0,
            "geometric_mean_candidate_improvement_percent": (
                1.0 - math.exp(float(np.mean(log_values)))
            )
            * 100.0,
            "cluster_bootstrap_95_percent": _cluster_bootstrap_log_ratio(clustered),
            "comparison": "candidate relative to paired NAM-A2-FULL; positive is better",
        }
    return result


def _error_concentration(rows: Sequence[RunCase]) -> dict[str, Any]:
    available = [(row, float(row.esr)) for row in rows if row.esr is not None]
    ordered = sorted(available, key=lambda item: item[1], reverse=True)
    total = sum(value for _row, value in ordered)
    counts: dict[str, int] = {}
    cumulative = 0.0
    if total > _EPSILON:
        for index, (_row, value) in enumerate(ordered, start=1):
            cumulative += value
            for target in (25, 50, 75):
                if str(target) not in counts and cumulative >= total * target / 100:
                    counts[str(target)] = index
    top_cases = []
    uniform_share = 1.0 / len(ordered) if ordered else 0.0
    for row, value in ordered:
        diagnostics = cast("dict[str, Any]", (row.analysis or {}).get("diagnostics", {}))
        share = value / max(total, _EPSILON)
        top_cases.append(
            {
                **_case_context(row),
                "esr": value,
                "share_of_summed_case_esr": share,
                "signal_strength": (
                    share / max(uniform_share * _CASE_ESR_MULTIPLE, _EPSILON)
                    if total > _EPSILON
                    else 0.0
                ),
                "signal_definition": (
                    f"case ESR share / {_CASE_ESR_MULTIPLE:g}x uniform case share"
                ),
                "top_regions": diagnostics.get("top_regions", []),
            }
        )
    return {
        "definition": "share of summed case ESR; cases are equally weighted",
        "case_count": len(ordered),
        "cases_for_25_percent": counts.get("25", 0),
        "cases_for_50_percent": counts.get("50", 0),
        "cases_for_75_percent": counts.get("75", 0),
        "top_5_share": sum(item["share_of_summed_case_esr"] for item in top_cases[:5]),
        "top_cases": top_cases,
    }


def _band_aggregate(rows: Sequence[RunCase]) -> list[dict[str, Any]]:
    values: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        diagnostics = cast("dict[str, Any]", (row.analysis or {}).get("diagnostics", {}))
        for band in cast("list[dict[str, Any]]", diagnostics.get("bands", [])):
            values[str(band["id"])].append(band)
    result = []
    for band_id, name, low, high, interpretation in _BANDS:
        entries = values.get(band_id, [])
        if not entries:
            continue
        summary = _summary([float(entry["signed_delta_db"]) for entry in entries])
        result.append(
            {
                "id": band_id,
                "name": name,
                "hz": [low, high],
                **summary,
                "mean_error_energy_share": float(
                    np.mean([float(entry["error_energy_share"]) for entry in entries])
                ),
                "sound_hypothesis": interpretation,
            }
        )
    return result


def _signed_aggregate(rows: Sequence[RunCase]) -> dict[str, Any]:
    fields: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        diagnostics = cast("dict[str, Any]", (row.analysis or {}).get("diagnostics", {}))
        signed = cast("dict[str, Any]", diagnostics.get("signed", {}))
        for key, value in signed.items():
            if isinstance(value, int | float):
                fields[key].append(float(value))
    return {field: _summary(values) for field, values in fields.items()}


def _phase_aggregate(rows: Sequence[RunCase]) -> dict[str, Any]:
    phase_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    event_counts: dict[str, int] = defaultdict(int)
    attack_deltas: list[float] = []
    attack_events = 0
    for row in rows:
        diagnostics = cast("dict[str, Any]", (row.analysis or {}).get("diagnostics", {}))
        onsets = cast("dict[str, Any]", diagnostics.get("onsets", {}))
        attack = onsets.get("attack_time_delta_ms_median")
        if isinstance(attack, int | float):
            attack_deltas.append(float(attack))
            attack_events += int(onsets.get("attack_time_event_count", 0))
        case_phases = cast("dict[str, dict[str, Any]]", diagnostics.get("phases", {}))
        for phase_id, phase in case_phases.items():
            event_counts[phase_id] += int(phase.get("event_count", 0))
            for field in ("signed_level_delta_db", "crest_delta_db", "error_energy_share"):
                value = phase.get(field)
                if isinstance(value, int | float):
                    phase_values[phase_id][field].append(float(value))
    phases: dict[str, Any] = {}
    for phase_id, name, start, stop in _PHASES:
        if phase_id in phase_values:
            phases[phase_id] = {
                "name": name,
                "window_ms": [round(start * 1_000), round(stop * 1_000)],
                "events": event_counts[phase_id],
                **{field: _summary(values) for field, values in phase_values[phase_id].items()},
            }
    return {
        "onset_anchor": "shared dry input",
        "attack_time_delta_ms": _summary(attack_deltas),
        "attack_time_events": attack_events,
        "regions": phases,
    }


def _associations(rows: Sequence[RunCase]) -> list[dict[str, Any]]:
    series: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        if row.esr is None:
            continue
        esr = float(row.esr)
        diagnostics = cast("dict[str, Any]", (row.analysis or {}).get("diagnostics", {}))
        for key, value in cast("dict[str, Any]", diagnostics.get("input", {})).items():
            if isinstance(value, int | float):
                series[f"input.{key}"].append((float(value), esr))
        context = _case_context(row)
        for key, value in cast("dict[str, float]", context.get("controls", {})).items():
            series[f"control.{key}"].append((float(value), esr))
    result: list[dict[str, Any]] = []
    for feature, pairs in series.items():
        x = np.asarray([pair[0] for pair in pairs], dtype=np.float64)
        y = np.asarray([pair[1] for pair in pairs], dtype=np.float64)
        if len(pairs) < 4 or len(np.unique(x)) < 3 or len(np.unique(y)) < 2:
            continue
        coefficient = float(stats.spearmanr(x, y).statistic)
        if math.isfinite(coefficient):
            result.append(
                {
                    "feature": feature,
                    "spearman_rho_with_esr": coefficient,
                    "cases": len(pairs),
                    "reading": "descriptive association, not a causal effect",
                }
            )
    return sorted(result, key=lambda item: abs(item["spearman_rho_with_esr"]), reverse=True)[:5]


def _case_evidence(rows: Sequence[RunCase], band_id: str) -> list[dict[str, Any]]:
    ranked: list[tuple[float, RunCase, dict[str, Any]]] = []
    for row in rows:
        diagnostics = cast("dict[str, Any]", (row.analysis or {}).get("diagnostics", {}))
        bands = cast("list[dict[str, Any]]", diagnostics.get("bands", []))
        ranked.extend(
            (abs(float(band["signed_delta_db"])), row, band)
            for band in bands
            if band.get("id") == band_id
        )
    result = []
    for _magnitude, row, band in sorted(ranked, reverse=True, key=lambda item: item[0]):
        diagnostics = cast("dict[str, Any]", row.analysis.get("diagnostics", {}))
        band_regions = cast("dict[str, dict[str, Any]]", diagnostics.get("band_regions", {}))
        region = band_regions.get(band_id)
        result.append(
            {
                **_case_context(row),
                "signed_delta_db": float(band["signed_delta_db"]),
                "signal_strength": abs(float(band["signed_delta_db"])) / _TONAL_EVIDENCE_DB,
                "signal_definition": (f"absolute case band delta / {_TONAL_EVIDENCE_DB:g} dB"),
                "top_regions": [region] if region is not None else [],
            }
        )
    return sorted(result, key=lambda item: float(item["signal_strength"]), reverse=True)


def _control_setting_relationships(
    cases: Sequence[dict[str, Any]],
    *,
    value_key: str,
    outcome_label: str,
) -> list[dict[str, Any]]:
    """Relate a case outcome to controls without counting repeated chunks as new settings."""
    grouped: dict[
        tuple[tuple[str, float], ...],
        dict[str, Any],
    ] = {}
    for case in cases:
        controls = cast("dict[str, float]", case.get("controls", {}))
        value = _number_or_none(case.get(value_key))
        if not controls or value is None:
            continue
        key = tuple(sorted((str(name), float(level)) for name, level in controls.items()))
        group = grouped.setdefault(
            key,
            {
                "controls": dict(key),
                "values": [],
            },
        )
        cast("list[float]", group["values"]).append(abs(value))

    setting_summaries = [
        {
            "controls": cast("dict[str, float]", group["controls"]),
            "outcome": float(np.median(cast("list[float]", group["values"]))),
        }
        for group in grouped.values()
    ]
    if len(setting_summaries) < 4:
        return []

    control_names = set.intersection(
        *(set(cast("dict[str, float]", item["controls"])) for item in setting_summaries)
    )
    relationships: list[dict[str, Any]] = []
    outcome = np.asarray(
        [cast("float", item["outcome"]) for item in setting_summaries],
        dtype=np.float64,
    )
    for control_name in sorted(control_names):
        levels = np.asarray(
            [
                cast("dict[str, float]", item["controls"])[control_name]
                for item in setting_summaries
            ],
            dtype=np.float64,
        )
        if len(np.unique(levels)) < 3 or len(np.unique(outcome)) < 2:
            continue
        coefficient = float(stats.spearmanr(levels, outcome).statistic)
        if not math.isfinite(coefficient):
            continue
        direction = "increases" if coefficient > 0 else "decreases"
        relationships.append(
            {
                "control": control_name,
                "outcome": outcome_label,
                "spearman_rho": coefficient,
                "settings": len(setting_summaries),
                "control_range": [float(np.min(levels)), float(np.max(levels))],
                "outcome_range": [float(np.min(outcome)), float(np.max(outcome))],
                "summary": (
                    f"Across {len(setting_summaries)} tested control settings, {outcome_label} "
                    f"{direction} as {control_name} increases (Spearman rho {coefficient:+.2f})."
                ),
                "basis": (
                    "one median outcome per distinct control setting; repeated input chunks "
                    "do not increase the setting count"
                ),
                "caveat": "descriptive pattern; controls may covary in the tested settings",
                "signal_strength": abs(coefficient) / _ASSOCIATION_RHO,
                "signal_definition": (
                    f"absolute setting-level Spearman rho / {_ASSOCIATION_RHO:g}"
                ),
            }
        )
    return sorted(
        relationships,
        key=lambda item: float(item["signal_strength"]),
        reverse=True,
    )


def _speed_assessment(rows: Sequence[RunCase]) -> dict[str, Any]:
    available = [(row, float(row.realtime_x)) for row in rows if row.realtime_x is not None]
    if not available:
        return {}
    target = _NAM_FULL_REALTIME_TARGET
    acceptable_floor = target * _ACCEPTABLE_REALTIME_FRACTION
    values = [value for _row, value in available]
    target_cases = sum(value >= target for value in values)
    acceptable_cases = sum(value >= acceptable_floor for value in values)
    if target_cases == len(values):
        status = "target_met"
    elif acceptable_cases == len(values):
        status = "acceptable"
    else:
        status = "below_acceptable"
    below_floor = sorted(
        ((row, value) for row, value in available if value < acceptable_floor),
        key=lambda item: item[1],
    )
    return {
        "direction": "higher_is_better",
        "basis": "NAM-FULL speed target",
        "target_realtime_x": target,
        "acceptable_realtime_x": acceptable_floor,
        "acceptable_fraction_of_target": _ACCEPTABLE_REALTIME_FRACTION,
        "status": status,
        "cases": len(values),
        "target_met_cases": target_cases,
        "acceptable_cases": acceptable_cases,
        "mean_realtime_x": float(np.mean(values)),
        "slowest_realtime_x": min(values),
        "fastest_realtime_x": max(values),
        "below_acceptable_cases": [
            {
                **_case_context(row),
                "realtime_x": value,
                "signal_strength": acceptable_floor / max(value, _EPSILON),
                "signal_definition": "acceptable speed floor / measured case speed",
            }
            for row, value in below_floor
        ],
    }


def _findings(  # noqa: PLR0912
    rows: Sequence[RunCase],
    *,
    paired: dict[str, Any],
    bands: list[dict[str, Any]],
    concentration: dict[str, Any],
    phases: dict[str, Any],
    associations: list[dict[str, Any]],
    speed: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    strengths: list[dict[str, Any]] = []
    significant: list[dict[str, Any]] = []
    esr_pair = cast("dict[str, Any]", paired.get("esr", {}))
    if esr_pair:
        improvement = float(esr_pair["median_candidate_improvement_percent"])
        cases = int(esr_pair["cases"])
        wins = int(esr_pair["candidate_better_cases"])
        finding: dict[str, Any] = {
            "title": "Paired ESR versus NAM-A2-FULL",
            "evidence_level": "derived",
            "evidence": (
                f"Candidate has {improvement:+.1f}% median paired ESR improvement and "
                f"is lower in {wins}/{cases} cases."
            ),
            "scope": f"{cases} paired cases",
            "confidence": "cluster-bootstrap interval reported in structured data",
        }
        if improvement > 0 and wins > cases / 2:
            strength_signal = _joint_signal(
                improvement / _BASELINE_REGRESSION_PERCENT,
                (wins / cases) / _BASELINE_WORSE_CASE_FRACTION,
            )
            if strength_signal >= 1.0:
                strengths.append(
                    {
                        **finding,
                        "signal_strength": strength_signal,
                        "signal_definition": (
                            "joint magnitude and better-case prevalence versus the paired "
                            "ESR default reporting thresholds"
                        ),
                    }
                )
        elif improvement < 0 and wins < cases / 2:
            worse_fraction = (cases - wins) / cases
            signal_strength = _joint_signal(
                abs(improvement) / _BASELINE_REGRESSION_PERCENT,
                worse_fraction / _BASELINE_WORSE_CASE_FRACTION,
            )
            significant.append(
                {
                    **finding,
                    "title": "Paired ESR regression versus NAM-A2-FULL",
                    "signal_strength": signal_strength,
                    "signal_definition": (
                        f"joint regression magnitude / {_BASELINE_REGRESSION_PERCENT:g}% and "
                        f"worse-case fraction / {_BASELINE_WORSE_CASE_FRACTION:g} score"
                    ),
                }
            )
    if speed.get("status") == "target_met":
        strengths.append(
            {
                "title": "NAM-FULL speed target met on every case",
                "evidence_level": "measured",
                "evidence": (
                    f"{float(speed['mean_realtime_x']):.2f}x mean; "
                    f"{float(speed['slowest_realtime_x']):.2f}x slowest; target "
                    f"{float(speed['target_realtime_x']):.1f}x."
                ),
                "scope": f"{speed['cases']} callback executions",
                "confidence": "wall time measured by the client",
                "limit": "excludes download, transcoding, upload, and server scoring",
            }
        )
    elif speed.get("status") == "below_acceptable":
        speed_signal = float(speed["acceptable_realtime_x"]) / max(
            float(speed["slowest_realtime_x"]), _EPSILON
        )
        significant.append(
            {
                "title": "Cases below the acceptable speed floor",
                "evidence_level": "measured",
                "evidence": (
                    f"{speed['acceptable_cases']}/{speed['cases']} cases reach "
                    f"{float(speed['acceptable_realtime_x']):.1f}x; NAM-FULL target is "
                    f"{float(speed['target_realtime_x']):.1f}x and lower is worse."
                ),
                "scope": f"{speed['cases']} callback executions",
                "confidence": "wall time measured by the client",
                "selection": "at least one case is below 50% of the NAM-FULL speed target",
                "signal_strength": speed_signal,
                "signal_definition": "acceptable speed floor / slowest case speed",
                "cases": speed["below_acceptable_cases"],
            }
        )
    if bands:
        band = max(bands, key=lambda item: abs(float(item["median"])))
        median = float(band["median"])
        case_count = int(band["cases"])
        positive_cases = int(band["positive_cases"])
        if median > 0:
            direction = "higher"
            directional_cases = positive_cases
            percent_difference = (10.0 ** (median / 10.0) - 1.0) * 100.0
        else:
            direction = "lower"
            directional_cases = case_count - positive_cases
            percent_difference = (1.0 - 10.0 ** (median / 10.0)) * 100.0
        consistent_cases = max(
            positive_cases,
            case_count - positive_cases,
        )
        consistency = consistent_cases / max(case_count, 1)
        signal_strength = _joint_signal(
            abs(median) / _TONAL_FINDING_DB,
            consistency / _TONAL_DIRECTION_FRACTION,
        )
        tone_cases = _case_evidence(rows, str(band["id"]))
        significant.append(
            {
                "title": f"Largest systematic tonal error: {band['name']}",
                "evidence_level": "derived",
                "evidence": (
                    f"Candidate energy from {band['hz'][0]:g}-{band['hz'][1]:g} Hz is "
                    f"{percent_difference:.1f}% {direction} than the reference "
                    f"({median:+.2f} dB median); the candidate is {direction} in "
                    f"{directional_cases}/{case_count} cases."
                ),
                "interpretation": (
                    f"The candidate systematically "
                    f"{'underproduces' if median < 0 else 'overproduces'} the reference's "
                    f"{band['sound_hypothesis']}. Because the benchmark target is the "
                    "reference, zero signed difference is the correct target."
                ),
                "scope": f"{case_count} cases",
                "basis": "corpus-wide median; P10/P90 remain in structured data",
                "signal_strength": signal_strength,
                "signal_definition": (
                    f"joint absolute median / {_TONAL_FINDING_DB:g} dB and "
                    f"directional consistency / {_TONAL_DIRECTION_FRACTION:g} score"
                ),
                "signal_components": [
                    {
                        "label": "absolute median band error",
                        "value": abs(median),
                        "threshold": _TONAL_FINDING_DB,
                        "unit": "dB",
                        "normalized": abs(median) / _TONAL_FINDING_DB,
                    },
                    {
                        "label": "same-direction case fraction",
                        "value": consistency * 100.0,
                        "threshold": _TONAL_DIRECTION_FRACTION * 100.0,
                        "unit": "%",
                        "normalized": consistency / _TONAL_DIRECTION_FRACTION,
                    },
                ],
                "signal_combination": (
                    "geometric mean of normalized components after every component reaches 1x"
                ),
                "condition_patterns": _control_setting_relationships(
                    tone_cases,
                    value_key="signed_delta_db",
                    outcome_label=f"absolute {band['id']} reference error",
                ),
                "cases": tone_cases,
            }
        )
    top_cases = cast("list[dict[str, Any]]", concentration.get("top_cases", []))
    case_count = int(concentration.get("case_count", 0))
    cases_for_half = int(concentration.get("cases_for_50_percent", 0))
    if top_cases and cases_for_half > 0:
        half_case_fraction = cases_for_half / case_count
        concentration_signal = _ERROR_CONCENTRATION_FRACTION / half_case_fraction
        significant.append(
            {
                "title": "Concentrated ESR error tail",
                "evidence_level": "derived",
                "evidence": (
                    f"{concentration['cases_for_50_percent']} cases account for 50% of summed "
                    f"case ESR; the top five account for {100 * concentration['top_5_share']:.1f}%."
                ),
                "scope": f"{concentration['case_count']} cases",
                "confidence": "exact deterministic concentration calculation",
                "signal_strength": concentration_signal,
                "signal_definition": (
                    f"{_ERROR_CONCENTRATION_FRACTION:g} / fraction of cases carrying half ESR"
                ),
                "cases": top_cases,
            }
        )
    attack = cast("dict[str, Any]", phases.get("attack_time_delta_ms", {}))
    attack_median = _number_or_none(attack.get("median"))
    if attack_median is not None and int(phases.get("attack_time_events", 0)):
        significant.append(
            {
                "title": "Attack-time deviation",
                "evidence_level": "derived",
                "evidence": (
                    f"Candidate 20%-90% attack time differs by {attack_median:+.2f} ms "
                    f"across {phases['attack_time_events']} detected events."
                ),
                "scope": "dry-anchored onset windows only",
                "confidence": "envelope detector v1 over the reported onset events",
                "signal_strength": abs(attack_median) / _ATTACK_FINDING_MS,
                "signal_definition": (f"absolute median attack delta / {_ATTACK_FINDING_MS:g} ms"),
            }
        )
    for association in associations:
        coefficient = float(association["spearman_rho_with_esr"])
        significant.append(
            {
                "title": f"ESR association with {association['feature']}",
                "evidence_level": "derived",
                "evidence": (f"Spearman rho {coefficient:+.2f} over {association['cases']} cases."),
                "scope": "observed corpus values only",
                "confidence": "descriptive association; correlated controls are not isolated",
                "signal_strength": abs(coefficient) / _ASSOCIATION_RHO,
                "signal_definition": (f"absolute descriptive Spearman rho / {_ASSOCIATION_RHO:g}"),
            }
        )
    significant.sort(key=lambda item: float(item["signal_strength"]), reverse=True)
    for rank, finding in enumerate(significant, start=1):
        finding["rank"] = rank
    return {"strengths": strengths, "significant": significant}


def aggregate_diagnostics(rows: Sequence[RunCase]) -> dict[str, Any]:
    """Aggregate per-case diagnostics into a self-contained agent evidence packet."""
    available = [row for row in rows if isinstance((row.analysis or {}).get("diagnostics"), dict)]
    paired = _paired_baseline(rows)
    bands = _band_aggregate(available)
    concentration = _error_concentration(rows)
    phases = _phase_aggregate(available)
    associations = _associations(available)
    speed = _speed_assessment(rows)
    return {
        "version": "top-arena-run-diagnostics-v6",
        "coverage": {
            "diagnostic_cases": len(available),
            "total_cases": len(rows),
            "paired_nam_cases": sum(getattr(row, "nam_esr", None) is not None for row in rows),
        },
        "reading": {
            "signed_delta": "candidate minus/reference-relative: positive means more or later",
            "error_metrics": "ESR, human-weighted ESR, and MRSTFT are lower-is-better",
            "correlation": "higher is better; polarity inversion can make it negative",
            "evidence_levels": {
                "measured": "direct versioned calculation",
                "derived": "deterministic summary of measurements",
                "hypothesis": "cautious possible sound interpretation",
            },
            "signal_strength": (
                "multiples of a diagnostic-specific reporting threshold; 1.0 meets default"
            ),
            "reference_target": (
                "the BIAS X reference is the target; signed reference differences should move "
                "toward zero"
            ),
            "control_setting_patterns": (
                "computed from one median outcome per distinct control setting so repeated "
                "input chunks do not inflate the relationship"
            ),
            "default_reporting_thresholds": {
                "paired_esr_regression_percent": _BASELINE_REGRESSION_PERCENT,
                "paired_esr_worse_case_fraction": _BASELINE_WORSE_CASE_FRACTION,
                "tone_median_db": _TONAL_FINDING_DB,
                "tone_direction_fraction": _TONAL_DIRECTION_FRACTION,
                "tone_case_evidence_db": _TONAL_EVIDENCE_DB,
                "cases_for_half_esr_fraction": _ERROR_CONCENTRATION_FRACTION,
                "case_esr_uniform_multiple": _CASE_ESR_MULTIPLE,
                "attack_delta_ms": _ATTACK_FINDING_MS,
                "association_absolute_rho": _ASSOCIATION_RHO,
                "speed_acceptable_realtime_x": (
                    _NAM_FULL_REALTIME_TARGET * _ACCEPTABLE_REALTIME_FRACTION
                ),
            },
        },
        "paired_nam": paired,
        "signed": _signed_aggregate(available),
        "tone_bands": bands,
        "phases": phases,
        "error_concentration": concentration,
        "condition_associations": associations,
        "speed": speed,
        "findings": _findings(
            available,
            paired=paired,
            bands=bands,
            concentration=concentration,
            phases=phases,
            associations=associations,
            speed=speed,
        ),
    }
