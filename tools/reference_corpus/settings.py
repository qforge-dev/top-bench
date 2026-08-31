from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .config import CorpusConfig
from .process import s3_upload, sha256, write_json

BLACKFACE_63_AMP_ID = "D3D21964-8E80-11EE-B9D1-0242AC120002"
BLACKFACE_63_SIMPLE_AMP_ID = "blackface63-simple"


def _amp_seed(base_seed: int, amp_id: str) -> int:
    digest = hashlib.sha256(f"{base_seed}:{amp_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _maximin_latin_hypercube(count: int, dimensions: int, seed: int) -> np.ndarray:
    if dimensions == 0:
        return np.empty((count, 0))
    rng = np.random.default_rng(seed)
    best: np.ndarray | None = None
    best_distance = -1.0
    for _ in range(256):
        sample = np.empty((count, dimensions), dtype=np.float64)
        for dimension in range(dimensions):
            sample[:, dimension] = (rng.permutation(count) + rng.random(count)) / count
        differences = sample[:, None, :] - sample[None, :, :]
        distances = np.sqrt(np.sum(differences * differences, axis=2))
        distances += np.eye(count) * 1e9
        minimum = float(np.min(distances))
        if minimum > best_distance:
            best, best_distance = sample, minimum
    if best is None:
        msg = "could not construct Latin hypercube"
        raise RuntimeError(msg)
    return 0.05 + 0.90 * best


def _build_positions(
    amp: dict[str, Any],
    controls: list[dict[str, Any]],
    count: int,
    seed: int,
) -> list[dict[str, Any]]:
    variable = [
        control for control in controls if control["sampling"] != "fixed_time_effect_bypass"
    ]
    continuous = [control for control in variable if control["kind"] == "knob"]
    sampled = _maximin_latin_hypercube(count, len(continuous), seed)
    rng = np.random.default_rng(seed ^ 0xA5A5A5A5)
    discrete_values = {
        control["name"]: [
            [float(choice) for choice in control.get("choices", [0.0, 1.0])][
                int(index) % len(control.get("choices", [0.0, 1.0]))
            ]
            for index in rng.permutation(count)
        ]
        for control in variable
        if control["kind"] != "knob"
    }
    values: list[dict[str, float]] = []
    for row_index in range(count):
        row: dict[str, float] = {}
        dimension = 0
        for control in controls:
            if control["sampling"] == "fixed_time_effect_bypass":
                row[control["name"]] = 0.0
            elif control["kind"] == "knob":
                row[control["name"]] = float(sampled[row_index, dimension])
                dimension += 1
            else:
                row[control["name"]] = discrete_values[control["name"]][row_index]
        values.append(row)

    defaults = {
        name: float(value)
        for name, value in zip(amp["controls"], amp["settings"]["default"]["values"], strict=True)
    }
    for control in controls:
        if control["sampling"] == "fixed_time_effect_bypass":
            defaults[control["name"]] = 0.0
        elif control["kind"] != "knob":
            choices = [float(choice) for choice in control.get("choices", [0.0, 1.0])]
            defaults[control["name"]] = min(
                choices,
                key=lambda choice: abs(choice - defaults[control["name"]]),
            )
    nearest = min(
        range(count),
        key=lambda index: sum(
            (values[index][control["name"]] - defaults[control["name"]]) ** 2
            for control in variable
        ),
    )
    values[nearest] = defaults
    ordered_indices = [nearest] + [index for index in range(count) if index != nearest]
    return [
        {
            "position_id": f"position-{position:02d}",
            "kind": "factory_default" if index == nearest else "maximin_latin_hypercube",
            "values": values[index],
            "vector": [values[index][control["name"]] for control in controls],
        }
        for position, index in enumerate(ordered_indices, start=1)
    ]


def _derive_fixed_amp(
    source: dict[str, Any],
    *,
    amp_id: str,
    amp_name: str,
    amp_index: int,
    fixed_controls: dict[str, float],
) -> dict[str, Any]:
    derived = copy.deepcopy(source)
    derived.update(
        {
            "amp_id": amp_id,
            "amp_index": amp_index,
            "amp_name": amp_name,
            "renderer_amp_id": source["amp_id"],
            "fixed_controls": dict(fixed_controls),
        }
    )
    control_names = [str(control["name"]) for control in derived["controls"]]
    unknown = sorted(set(fixed_controls) - set(control_names))
    if unknown:
        msg = f"cannot fix unknown controls: {', '.join(unknown)}"
        raise ValueError(msg)
    for position in derived["positions"]:
        position["values"].update(fixed_controls)
        position["vector"] = [position["values"][name] for name in control_names]
    return derived


def generate_settings(config: CorpusConfig, *, upload: bool = True) -> Path:
    report = json.loads(config.amp_report.read_text())
    old_plan = json.loads(config.old_capture_plan.read_text())
    plan_by_amp: dict[str, dict[str, Any]] = {}
    for row in old_plan["settings"]:
        plan_by_amp.setdefault(row["amp_id"], row)
    amps: list[dict[str, Any]] = []
    for amp in report["results"]:
        old = plan_by_amp[amp["amp_id"]]
        controls = [
            {
                key: control[key]
                for key in ("index", "name", "kind", "sampling", "choices")
                if key in control
            }
            for control in old["controls"]
        ]
        amps.append(
            {
                "amp_index": int(old["amp_catalog_index"]),
                "amp_id": amp["amp_id"],
                "amp_name": amp["amp"],
                "series": amp["series"],
                "category": amp["category"],
                "hidden": bool(amp["hidden"]),
                "controls": controls,
                "positions": _build_positions(
                    amp,
                    controls,
                    config.position_count,
                    _amp_seed(config.seed, amp["amp_id"]),
                ),
            }
        )
    blackface = next(amp for amp in amps if amp["amp_id"] == BLACKFACE_63_AMP_ID)
    amps.append(
        _derive_fixed_amp(
            blackface,
            amp_id=BLACKFACE_63_SIMPLE_AMP_ID,
            amp_name=BLACKFACE_63_SIMPLE_AMP_ID,
            amp_index=max(int(amp["amp_index"]) for amp in amps) + 1,
            fixed_controls={"Bright": 0.0, "Master": 0.5},
        )
    )
    amps.sort(key=lambda row: int(row["amp_index"]))
    manifest_path = config.root / "manifests" / "amps.json"
    manifest = {
        "format": "top-arena.bias-x-position-plan.v1",
        "seed": config.seed,
        "method": "256-candidate maximin Latin hypercube over [0.05, 0.95] plus factory default",
        "time_effects": "fixed at zero",
        "amp_count": len(amps),
        "positions_per_amp": config.position_count,
        "source_report": str(config.amp_report),
        "source_report_sha256": sha256(config.amp_report),
        "amps": amps,
    }
    write_json(manifest_path, manifest)
    for amp in amps:
        path = config.root / "positions" / f"{amp['amp_id']}.json"
        write_json(path, amp)
    if upload:
        s3_upload(manifest_path, f"{config.s3_root}/manifests/amps.json")
        for amp in amps:
            path = config.root / "positions" / f"{amp['amp_id']}.json"
            s3_upload(path, f"{config.s3_root}/amps/{amp['amp_id']}/positions.json")
    return manifest_path


def resolve_amps(manifest: dict[str, Any], selectors: list[str]) -> list[dict[str, Any]]:
    if selectors == ["all"]:
        return list(manifest["amps"])
    wanted = {selector.casefold() for selector in selectors}
    result = [
        amp
        for amp in manifest["amps"]
        if str(amp["amp_id"]).casefold() in wanted
        or str(amp["amp_name"]).casefold() in wanted
        or str(amp["amp_index"]) in wanted
    ]
    if len(result) != len(wanted):
        found = {
            value.casefold()
            for amp in result
            for value in (str(amp["amp_id"]), str(amp["amp_name"]), str(amp["amp_index"]))
        }
        missing = sorted(wanted - found)
        msg = f"unknown amp selector(s): {', '.join(missing)}"
        raise ValueError(msg)
    return result
