"""The tuning game: change the plant, run it offline and see what it does to output, quality and money.

A scenario is a handful of knobs (how many SMT lines, robots, technicians, relief people, bins of parts
at the lines, places between the cells of final assembly). The plant model is built from the same
config files with the knobs applied, the simulation runs it for some hours of plant time with a fixed
seed (so the same knobs always give the same result, and two scenarios differ only by the knobs), and
the KPIs (mes_lab.kpi) are computed from the run, the margin per hour being the score.

Runs are slow (a day of plant time takes a minute), so the MES runs one at a time in a worker process
and keeps the results (see mes.tuning_views).
"""

from __future__ import annotations

import copy
import time
from datetime import UTC, datetime

from mes_lab import kpi
from mes_lab.model.loader import _read, build_model

KNOBS = {
    # name: (min, max, default from the config is read at run time, what it is)
    "smt_lines": (2, 5, "SMT lines (they can make every board)"),
    "robots": (1, 8, "robots (AMR) for repairs, quarantine, parts and boxes"),
    "maintenance_day": (1, 3, "maintenance technicians on the morning and the afternoon shift"),
    "maintenance_night": (1, 2, "maintenance technicians on the night shift"),
    "relief": (2, 8, "relief operators per shift (they sit in during the breaks)"),
    "kit_bins": (2, 6, "bins of parts in the rack at the loader of final assembly"),
    "fa_buffer": (1, 6, "places on the conveyor between two cells of final assembly"),
}
HOURS = (8, 12, 24)


def defaults(raw: dict | None = None) -> dict:
    """The knobs as the plant has them now (config/*.yaml)."""
    raw = raw or _read_default()
    shifts = raw["crew"]["shifts"]
    day = [s for code, s in shifts.items() if code != "C"] or list(shifts.values())
    smt = next(a for a in raw["areas"] if a["kind"] == "SMT")
    return {"smt_lines": len(smt["lines"]), "robots": raw.get("amr", {}).get("robots", 4),
            "maintenance_day": max(s["maintenance"] for s in day),
            "maintenance_night": shifts.get("C", day[0])["maintenance"],
            "relief": max(s["relief"] for s in shifts.values()),
            "kit_bins": raw.get("amr", {}).get("supply", {}).get("kit_bins", 3),
            "fa_buffer": next(t["buffer"] for name, t in raw["line_templates"].items() if name.startswith("FA_"))}


def _read_default() -> dict:
    from mes_lab import DEFAULT_CONFIG_DIR
    return _read(DEFAULT_CONFIG_DIR)


def clean(knobs: dict, base: dict) -> dict:
    """The knobs within their limits (unknown ones are dropped, missing ones are the plant's)."""
    out = dict(base)
    for name, (lo, hi, _) in KNOBS.items():
        if name in knobs:
            try:
                out[name] = max(lo, min(hi, int(knobs[name])))
            except (TypeError, ValueError):
                pass
    return out


def model_for(knobs: dict | None, config_dir=None):
    """The plant of the live simulation: as configured (knobs None), or with the knobs of a scenario."""
    from pathlib import Path

    from mes_lab import DEFAULT_CONFIG_DIR

    raw = _read(Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR)
    if not knobs:
        return build_model(raw)
    return build(clean(knobs, defaults(raw)), raw)


def build(knobs: dict, raw: dict | None = None):
    """The plant model with the knobs applied."""
    raw = copy.deepcopy(raw or _read_default())
    smt = next(a for a in raw["areas"] if a["kind"] == "SMT")
    smt["lines"] = smt["lines"][:knobs["smt_lines"]]
    amr = raw.setdefault("amr", {})
    amr["robots"] = knobs["robots"]
    amr.setdefault("supply", {})["kit_bins"] = knobs["kit_bins"]
    for code, s in raw["crew"]["shifts"].items():
        s["maintenance"] = knobs["maintenance_night"] if code == "C" else knobs["maintenance_day"]
        s["relief"] = knobs["relief"]
    for name, template in raw["line_templates"].items():
        if name.startswith("FA_"):
            template["buffer"] = knobs["fa_buffer"]
    return build_model(raw)


def run(knobs: dict, hours: int = 12, seed: int = 1) -> dict:
    """Run a scenario offline; the KPIs, the costs and how long it took."""
    from mes_lab.sim.runner import run_offline

    t0 = time.monotonic()
    model = build(knobs)
    plant = run_offline(model, hours, seed=seed, start=datetime(2026, 1, 5, 6, 0, tzinfo=UTC))
    facts = kpi.facts_from_plant(plant)
    result = kpi.compute(model, facts)
    result["extra"] = {
        "material_wait_h": sum(r.starved_s for r in plant.supply.racks.values()) / 3600,
        "robot_waits": plant.traffic.waits, "transports": plant.fleet.done,
        "battery_min": min(plant.fleet.level(r) for r in plant.fleet.robots),
        "wall_s": round(time.monotonic() - t0, 1),
    }
    result["knobs"] = knobs
    result["hours"] = hours
    return result
