"""Key performance indicators of the plant, computed the same way from the MES database (the KPI page)
and from an offline run of the simulation (the tuning game).

Both condense what happened in a time window into `Facts`; `compute` turns them into numbers:

* OEE of a line = availability x performance x quality, at its constraint (the cell with the most
  work). Planned time is the time the line had work (no order = not planned). Availability loses
  breakdowns, changeovers and planned maintenance; performance loses minor stops, waiting for
  pieces (starved), a full next buffer (blocked) and slow cycles; quality is the first pass yield.
* FPY (first pass yield) of a line: the share of pieces that pass every station of the line the
  first time = the product of the station yields (good / (good + bad)). RTY (rolled throughput
  yield) of a product: FPY of its SMT passes x FPY of its final assembly.
* Loading: how much of the time a line had work at all (low = more lines than the demand needs).
* MTBF / MTTR: running hours per breakdown, minutes per breakdown (until the machine runs again,
  waiting for the technician included).
* Automation degree: the share of the work content (ideal seconds of what was made) done by
  machines; manual are loading, visual inspection, packing, palletizing, repair, an operator
  checking the calls of an inspection and clearing minor stops.
* Productivity: good units of final assembly per person-hour on site.
* Cost of a good unit: people, robots, lines (depreciation, service), energy and scrapped material,
  per good unit, plus its material; with the price it gives the margin (config/costs.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mes_lab.model.schema import AreaKind, PlantModel

DOWNTIME = ("DOWN", "CHANGEOVER", "MAINTENANCE")
LOSSES = ("DOWN", "CHANGEOVER", "MAINTENANCE", "MINOR_STOP", "STARVED", "BLOCKED", "NO_ORDER")
BOARD_SHARE = 0.6        # a scrapped board costs this share of the material of its unit


@dataclass
class Facts:
    """What happened in a time window, condensed (simulation or MES database)."""
    hours: float
    state_s: dict[str, dict[str, float]]                          # station -> state -> seconds
    counts: dict[str, tuple[int, int, int, int]]                  # station -> processed, good, bad, false calls
    by_material: dict[tuple[str, str], tuple[int, int, int, int]]  # (station, material) -> the same
    earned_s: dict[str, float]                                    # station -> ideal seconds of what it processed
    down: dict[str, tuple[int, float]]                            # station -> breakdowns (how many, seconds)
    person_h: dict[str, float] = field(default_factory=dict)      # role -> person-hours on site
    robots: dict = field(default_factory=dict)                    # n, busy_s, transports, by_kind
    shipped: dict[str, int] = field(default_factory=dict)         # product -> units shipped
    scrapped: dict[str, int] = field(default_factory=dict)        # material (FG or PCBA) -> pieces scrapped
    repairs: int = 0


def _ratio(a: float, b: float) -> float | None:
    return a / b if b > 0 else None


def staff_per_shift(model: PlantModel, seats: int) -> dict[str, dict[str, int]]:
    """People on site per shift and role: an operator in every seat plus the relief people, the rest
    from the roster (config/crew.yaml)."""
    out = {}
    for code, s in model.crew.shifts.items():
        out[code] = {"OPERATOR": seats + s.relief, "MAINTENANCE": s.maintenance, "IT": s.it,
                     "FOREMAN": s.foreman, "LOGISTICS": s.logistics, "MANAGER": s.manager}
    return out


def line_kpis(model: PlantModel, code: str, f: Facts) -> dict:
    line = model.lines[code]
    types = model.station_types
    total_h = f.hours
    # the constraint: the cell whose machines had the most work (ovens hold several pieces, they never are)
    best, best_load = None, -1.0
    for cell in line.cells:
        if types[cell.type].kind == "oven":
            continue
        load = sum(f.earned_s.get(st.code, 0.0) for st in cell.stations) / len(cell.stations)
        if load > best_load:
            best, best_load = cell, load
    cell = best or line.cells[0]
    states: dict[str, float] = {}
    for st in cell.stations:
        for state, sec in f.state_s.get(st.code, {}).items():
            states[state] = states.get(state, 0.0) + sec
    total = sum(states.values())
    planned = total - states.get("NO_ORDER", 0.0)
    down = sum(states.get(s, 0.0) for s in DOWNTIME)
    run = planned - down
    earned = sum(f.earned_s.get(st.code, 0.0) for st in cell.stations)
    availability = _ratio(run, planned)
    performance = min(1.0, earned / run) if run > 0 else None
    # first pass yield: every station of the line passes its pieces the first time
    fpy = 1.0
    for st in line.stations:
        p, g, b, _fc = f.counts.get(st.code, (0, 0, 0, 0))
        if g + b > 0:
            fpy *= g / (g + b)
    output_type = line.output
    output = sum(f.counts.get(st.code, (0, 0, 0, 0))[1] for st in line.stations if st.type == output_type)
    if line.area_kind == AreaKind.SMT:
        output = sum(f.counts.get(st.code, (0, 0, 0, 0))[1] for st in line.stations if st.type == "DEPANELER")
    oee = availability * performance * fpy if availability is not None and performance is not None else None
    if not line.is_job_line:
        # a flow line (packing) works on whatever arrives: waiting for pieces is the demand, not a loss
        oee = availability = performance = None
    n_down = sum(f.down.get(st.code, (0, 0.0))[0] for st in line.stations)
    down_s = sum(f.down.get(st.code, (0, 0.0))[1] for st in line.stations)
    run_all = sum(f.state_s.get(st.code, {}).get("RUNNING", 0.0) for st in line.stations)
    losses = {s: round(states.get(s, 0.0) / total, 4) if total else 0.0 for s in LOSSES}
    worst = max((s for s in LOSSES if s != "NO_ORDER"), key=lambda s: states.get(s, 0.0), default=None)
    return {
        "code": code, "area": line.area_kind.value, "constraint": cell.type,
        "availability": availability, "performance": performance, "quality": fpy if planned else None,
        "oee": oee, "fpy": fpy, "output": output,
        "loading": _ratio(planned, total), "utilization": _ratio(earned, total),
        "mtbf_h": _ratio(run_all / 3600, n_down), "mttr_min": _ratio(down_s / 60, n_down), "breakdowns": n_down,
        "losses": losses, "main_loss": worst if worst and states.get(worst, 0.0) > 0 else None,
        "running_h": run / 3600 if run > 0 else 0.0, "hours": total_h,
    }


def compute(model: PlantModel, f: Facts) -> dict:
    lines = {code: line_kpis(model, code, f) for code, line in model.lines.items()
             if line.area_kind in (AreaKind.SMT, AreaKind.FA, AreaKind.PACK)}
    areas = {}
    for kind in ("SMT", "FA", "PACK"):
        rows = [r for r in lines.values() if r["area"] == kind]
        weight = [(r, r["running_h"] / max(r["availability"] or 1, 1e-9) if r["availability"] else 0.0) for r in rows]
        tot = sum(w for _, w in weight)
        def avg(key):
            vals = [(r[key], w) for r, w in weight if r[key] is not None]
            s = sum(w for _, w in vals)
            return sum(v * w for v, w in vals) / s if s else None
        areas[kind] = {"oee": avg("oee"), "availability": avg("availability"), "performance": avg("performance"),
                       "quality": avg("quality"), "loading": sum(r["loading"] or 0 for r in rows) / len(rows) if rows else None,
                       "output": sum(r["output"] for r in rows), "lines": len(rows), "planned_h": tot / 3600 if tot else 0,
                       # the work content of the area in lines running flat out at their constraint
                       "work_lines": sum(r["utilization"] or 0 for r in rows)}

    # yields per product: its SMT passes (on whatever SMT line) and its final assembly line
    types = model.station_types
    products = {}
    for fg in model.finished_goods():
        smt_y = fa_y = 1.0
        smt_seen = fa_seen = False
        by_type: dict[str, list[int]] = {}
        for (station, mat), (p, g, b, _fc) in f.by_material.items():
            st = model.stations.get(station)
            if st is None or types[st.type].kind == "repair":
                continue
            if mat == fg.pcba or mat == fg.code:
                key = f"{mat}|{st.type}"
                acc = by_type.setdefault(key, [0, 0])
                acc[0] += g
                acc[1] += b
        for key, (g, b) in by_type.items():
            mat, _t = key.split("|")
            if g + b == 0:
                continue
            if mat == fg.pcba:
                smt_y *= g / (g + b)
                smt_seen = True
            else:
                fa_y *= g / (g + b)
                fa_seen = True
        good = sum(f.counts.get(st.code, (0, 0, 0, 0))[1] for line in model.lines.values()
                   if line.area_kind == AreaKind.FA and fg.code in line.materials
                   for st in line.stations if st.type == line.output)
        products[fg.code] = {"fpy_smt": smt_y if smt_seen else None, "fpy_fa": fa_y if fa_seen else None,
                             "rty": smt_y * fa_y if (smt_seen or fa_seen) else None, "good": good,
                             "shipped": f.shipped.get(fg.code, 0),
                             "scrapped": f.scrapped.get(fg.code, 0) + f.scrapped.get(fg.pcba, 0)}
    good_fa = sum(p["good"] for p in products.values())
    rty_vals = [(p["rty"], p["good"]) for p in products.values() if p["rty"] is not None and p["good"]]
    rty = sum(v * w for v, w in rty_vals) / sum(w for _, w in rty_vals) if rty_vals else None

    # automation degree: machine work content against people's work content
    auto_s = manual_s = 0.0
    for code, st in model.stations.items():
        t = types[st.type]
        earned = f.earned_s.get(code, 0.0)
        if t.kind == "repair":
            manual_s += f.state_s.get(code, {}).get("RUNNING", 0.0)       # the repair takes as long as it takes
        elif t.manual:
            manual_s += earned
        else:
            auto_s += earned
        p, g, b, fc = f.counts.get(code, (0, 0, 0, 0))
        if t.kind == "inspection" and t.verify_s and not t.manual:
            manual_s += (b + fc) * t.verify_s                 # the operator checks every call of the machine
        manual_s += f.state_s.get(code, {}).get("MINOR_STOP", 0.0)
    automation = _ratio(auto_s, auto_s + manual_s)

    n_down = sum(v[0] for v in f.down.values())
    run_all = sum(s.get("RUNNING", 0.0) for s in f.state_s.values())
    down_s = sum(v[1] for v in f.down.values())
    person_h = sum(f.person_h.values())
    scrap = sum(f.scrapped.values())
    produced = good_fa + sum(f.scrapped.get(fg.code, 0) for fg in model.finished_goods())
    robots = f.robots or {}
    robot_h = robots.get("n", 0) * f.hours

    plant = {
        "hours": f.hours, "oee": areas["FA"]["oee"], "oee_smt": areas["SMT"]["oee"],
        "fpy_fa": areas["FA"]["quality"], "fpy_smt": areas["SMT"]["quality"], "rty": rty,
        "good_units": good_fa, "units_per_h": _ratio(good_fa, f.hours),
        "shipped": sum(f.shipped.values()),
        "scrap_rate": _ratio(sum(f.scrapped.get(fg.code, 0) for fg in model.finished_goods()), produced),
        "scrap_pieces": scrap, "repairs": f.repairs, "repair_rate": _ratio(f.repairs, produced),
        "automation": automation, "manual_s_per_unit": _ratio(manual_s, good_fa),
        "person_h": person_h, "units_per_person_h": _ratio(good_fa, person_h),
        "mtbf_h": _ratio(run_all / 3600, n_down), "mttr_min": _ratio(down_s / 60, n_down), "breakdowns": n_down,
        "robots": robots.get("n", 0), "robot_utilization": _ratio(robots.get("busy_s", 0.0), robot_h * 3600),
        "transports_per_h": _ratio(robots.get("transports", 0), f.hours),
        "smt_loading": areas["SMT"]["loading"], "fa_loading": areas["FA"]["loading"],
    }
    return {"lines": list(lines.values()), "areas": areas, "products": products, "plant": plant,
            "costs": costs(model, f, lines, products)}


def costs(model: PlantModel, f: Facts, lines: dict, products: dict) -> dict | None:
    c = model.costs
    if c is None:
        return None
    labor = sum(h * c.labor_per_h.get(role, 0.0) for role, h in f.person_h.items())
    robots = (f.robots or {}).get("n", 0) * f.hours * c.robot_per_h
    fixed = sum(c.line_per_h.get(line.area_kind.value, 0.0) * f.hours for line in model.lines.values()
                if line.area_kind != AreaKind.WAREHOUSE)
    energy = 0.0
    for code, line in model.lines.items():
        sts = line.stations
        if not sts:
            continue
        running = sum(f.state_s.get(st.code, {}).get("RUNNING", 0.0) for st in sts) / len(sts) / 3600
        energy += running * c.energy_per_h.get(line.area_kind.value, 0.0)
    scrap = 0.0
    for fg in model.finished_goods():
        scrap += f.scrapped.get(fg.code, 0) * c.material.get(fg.code, 0.0)
        scrap += f.scrapped.get(fg.pcba, 0) * c.material.get(fg.code, 0.0) * BOARD_SHARE
    good = sum(p["good"] for p in products.values())
    revenue = sum(p["good"] * c.price.get(code, 0.0) for code, p in products.items())
    material = sum(p["good"] * c.material.get(code, 0.0) for code, p in products.items())
    conversion = labor + robots + fixed + energy + scrap
    margin = revenue - material - conversion
    return {"currency": c.currency, "labor": labor, "robots": robots, "lines": fixed, "energy": energy,
            "scrap": scrap, "conversion": conversion, "material": material, "revenue": revenue, "margin": margin,
            "conversion_per_unit": _ratio(conversion, good), "cost_per_unit": _ratio(conversion + material, good),
            "margin_per_h": _ratio(margin, f.hours), "margin_share": _ratio(margin, revenue)}


# ---------------------------------------------------------------------- from an offline run
def facts_from_plant(plant, since_s: float = 0.0) -> Facts:
    """The facts of an offline run of the simulation (whole run; the totals of the edge gateway)."""
    edge = plant.edge
    edge.checkpoint()
    hours = (plant.env.now - since_s) / 3600
    state_s: dict[str, dict[str, float]] = {}
    for (station, state), sec in edge.total_state.items():
        state_s.setdefault(station, {})[state] = sec
    seats = len(plant.layout.seats)
    per_shift = staff_per_shift(plant.model, seats)
    # person-hours: the roster of the shift that was on, hour by hour
    person_h: dict[str, float] = {}
    t = since_s
    while t < plant.env.now:
        code, _start, end = plant.crew.shift_window(t)
        step = min(end, plant.env.now) - t
        for role, n in per_shift[code].items():
            person_h[role] = person_h.get(role, 0.0) + n * step / 3600
        t += max(step, 1.0)
    fleet = plant.fleet
    shipped = {mat: v["units"] for mat, v in plant.shipping.shipped.items()}
    repairs = sum(edge.total_count.get(st.code, [0])[0] for st in plant.repair_line.stations)
    return Facts(hours=hours, state_s=state_s,
                 counts={k: tuple(v) for k, v in edge.total_count.items()},
                 by_material={k: tuple(v) for k, v in edge.total_by_material.items()},
                 earned_s=dict(edge.total_earned),
                 down={k: (int(v[0]), v[1]) for k, v in edge.total_down.items()},
                 person_h=person_h,
                 robots={"n": len(fleet.robots), "busy_s": sum(r.busy_s for r in fleet.robots),
                         "transports": fleet.done, "by_kind": dict(fleet.by_kind)},
                 shipped=shipped, scrapped=dict(plant.scrapped), repairs=repairs)
