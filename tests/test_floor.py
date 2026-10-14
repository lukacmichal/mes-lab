"""The live picture of the plant: the floor plan, the conveyors, and the snapshot the
simulation publishes. What the page draws must be what the plant really holds."""

import json
import math
from collections import Counter
from datetime import UTC, datetime

import pytest

from mes_lab.layout import SLOT, build_layout
from mes_lab.model.schema import AreaKind
from mes_lab.sim.core import PlantClock, State
from mes_lab.sim.dispatch import LocalDispatcher
from mes_lab.sim.plant import PANEL_RACK, PlantSim


def _plant(model, seed=4):
    return PlantSim(model, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(model, seed), seed=seed)


# ---------------------------------------------------------------- floor plan

def test_every_station_has_a_place_and_nothing_overlaps(model):
    layout = build_layout(model)
    assert set(layout.stations) == set(model.stations)
    rects = list(layout.stations.items())
    for i, (a_code, a) in enumerate(rects):
        assert a["w"] > 0 and a["h"] > 0
        assert 0 <= a["x"] and a["x"] + a["w"] <= layout.world["w"], a_code
        assert 0 <= a["y"] and a["y"] + a["h"] <= layout.world["h"], a_code
        for b_code, b in rects[i + 1:]:
            apart = (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
                     or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])
            assert apart, f"{a_code} overlaps {b_code}"


def test_cells_follow_each_other_and_buffers_show_the_real_capacity(model):
    layout = build_layout(model)
    for line in model.lines.values():
        info = layout.lines[line.code]
        if line.area_kind == AreaKind.REPAIR:
            continue
        x = info["x1"]
        for cell in info["cells"]:
            if cell["buf"]:
                assert cell["buf"]["slots"] == line.buffer
                assert cell["buf"]["x0"] == pytest.approx(x)
                assert cell["buf"]["x1"] - cell["buf"]["x0"] >= line.buffer * SLOT   # room for every place
                x = cell["buf"]["x1"]
            assert cell["x"] == pytest.approx(x)
            x += cell["w"]
            ys = {round(layout.stations[s]["cy"], 1) for s in cell["stations"]}
            assert len(ys) == len(cell["stations"])                              # lanes do not coincide
        assert info["x2"] == pytest.approx(x)


def test_every_conveyor_the_plant_uses_exists_and_connects(model):
    layout = build_layout(model)
    routes = layout.routes
    for line in model.lines.values():
        info = layout.lines[line.code]
        if line.area_kind == AreaKind.SMT:
            assert {f"supply:{line.code}", f"sm:{line.code}", f"ret:{line.code}", f"wash:{line.code}"} <= set(routes)
            sm = routes[f"sm:{line.code}"]
            assert sm.pts[0] == (info["x2"], info["spine"])                         # starts where the line ends
            assert routes[f"supply:{line.code}"].pts[-1] == (info["x1"], info["spine"])
        if line.area_kind == AreaKind.FA:
            assert f"feed:{line.code}" in routes and f"pack:{line.code}" in routes
        if line.area_kind == AreaKind.PACK:
            assert f"wh:{line.code}" in routes
        if line.area_kind != AreaKind.REPAIR:
            # no conveyor to repair: a robot stops in the gap after every cell (its NOK tote, pieces back to the
            # next cell) and left of the first machine
            assert f"drop:{line.code}:0" in layout.amr.places
            for cell in line.cells:
                assert f"gap:{line.code}:{cell.index}" in layout.amr.places and f"{line.code}:{cell.index}" in layout.nok
    assert not [k for k in routes if k.split(":")[0] in ("rwi", "rwo", "rpr2sm")]
    assert {"drop:SM", "rpr:in", "rpr:out", "rpr:quar"} <= set(layout.amr.places)
    for key, route in routes.items():
        assert len(route.pts) >= 2 and route.length > 0, key
        assert route.duration >= 6, key
    # a piece rides exactly as long as the picture is long (belt speed is a constant per kind)
    sm = routes["sm:SMT-01"]
    assert sm.duration == pytest.approx(sm.length / 1.5)
    # washing takes the time of the SPI type, the route only shows where it goes
    assert routes["wash:SMT-01"].duration == model.station_types["SPI"].wash_min * 60


def test_layout_is_plain_json(model):
    data = build_layout(model).data
    text = json.dumps(data)
    assert len(text) < 200_000
    again = json.loads(text)
    assert set(again["lines"]) == set(model.lines)
    assert again["products"]["BCM-200"]["boards_per_panel"] == 4
    assert again["products"]["LED-DRV-40"]["boards_per_panel"] == 8


# ---------------------------------------------------------------- the snapshot

PHASES = {"idle", "in", "work", "verify", "pass", "out"}


def _ids(snap):
    """Every piece id the snapshot mentions, with where."""
    seen = []
    for code, e in snap["stations"].items():
        if "it" in e:
            seen.append(("station", code, e["it"]["i"]))
        for d in e.get("out", []):
            seen.append(("out", code, d["i"]))
        for d, _ in e.get("ov", []):
            seen.append(("oven", code, d["i"]))
    for line, cells in snap["bufs"].items():
        for cell in cells:
            for d in cell["it"]:
                seen.append(("buffer", line, d["i"]))
    for line, q in snap["feed"].items():
        for d in q["it"]:
            seen.append(("feed", line, d["i"]))
    for r in snap["tr"]:
        seen.append(("ride", r["r"], r["it"]["i"]))
    for place, box in snap["boxes"].items():
        for d in box:
            seen.append(("tote", place, d["i"]))
    for robot in snap["amr"]:
        for d in robot.get("ld", []):
            seen.append(("robot", robot["n"], d["i"]))
    for d in snap["rack"]:
        seen.append(("rack", "in", d["i"]))
    for d in snap["cage"]:
        seen.append(("cage", "quarantine", d["i"]))
    for person in snap["people"]:
        if "h" in person:
            seen.append(("hands", person["n"], person["h"]["i"]))
    return seen


def _check_snapshot(plant, snap):
    layout = plant.layout
    now = plant.env.now
    assert json.loads(json.dumps(snap)) == snap                       # plain JSON
    where = Counter(i for *_, i in _ids(snap))
    twice = [i for i, n in where.items() if n > 1]
    assert not twice, f"pieces in two places at once: {twice[:5]}"
    assert set(snap["stations"]) == set(layout.stations)
    for code, e in snap["stations"].items():
        assert e["ph"] in PHASES, code
        assert e["s"] in {s.value for s in State}
        if e["ph"] == "work":
            assert "it" in e, f"{code} works without a piece"
            total, left = e["w"]
            assert 0 <= left <= total + 1e-6
        if e["ph"] == "verify":
            assert "it" in e and "w" in e
        if "out" in e:
            assert e["ph"] == "out"
        if e["ph"] == "idle":
            assert "it" not in e and "out" not in e
        if "ov" in e:
            assert all(0 <= p <= 1 for _, p in e["ov"]), code
            assert len(e["ov"]) <= layout.data["types"][layout.stations[code]["type"]]["capacity"]
    for line, cells in snap["bufs"].items():
        info = plant.lines[line]
        if info.spec.area_kind == AreaKind.REPAIR:                 # its pieces lie in the inbound rack
            assert cells[0]["n"] == len(plant.inrack) and not cells[0]["it"]
            continue
        for index, cell in enumerate(cells):
            inbox = info.cells[index].inbox
            assert cell["n"] == (len(inbox.items) + len(info.pending_in.get(index, [])) if inbox is not None else 0)
            if inbox is not None and info.spec.area_kind in (AreaKind.SMT, AreaKind.FA) or index > 0:
                assert len(cell["it"]) <= max(info.spec.buffer, 1) + len(info.pending_in.get(index, []))
    for r in snap["tr"]:
        route = layout.routes[r["r"]]
        assert r["t"] - 0.1 <= now <= r["t"] + r["d"] + 0.1, r
        if route.kind != "wash":
            assert r["d"] == pytest.approx(route.duration, abs=0.1)
    for r, n in snap["panels"].items():
        assert 0 <= n <= PANEL_RACK
    for line, q in snap["feed"].items():
        assert q["n"] <= 8


def _descs(snap):
    for e in snap["stations"].values():
        if "it" in e:
            yield e["it"]
        yield from e.get("out", [])
        for d, _ in e.get("ov", []):
            yield d
    for cells in snap["bufs"].values():
        for cell in cells:
            yield from cell["it"]
    for q in snap["feed"].values():
        yield from q["it"]
    for r in snap["tr"]:
        yield r["it"]
    for box in snap["boxes"].values():
        yield from box
    for robot in snap["amr"]:
        yield from robot.get("ld", [])
    yield from snap["rack"]
    yield from snap["cage"]
    for person in snap["people"]:
        if "h" in person:
            yield person["h"]


def test_pieces_carry_their_serial_numbers_and_the_warehouse_its_pallets(model):
    plant = _plant(model)
    kinds = Counter()
    for minute in range(30, 12 * 60, 30):
        plant.env.run(until=minute * 60)
        snap = plant.floor_snapshot()
        sns = [d["sn"] for d in _descs(snap) if "sn" in d]
        assert len(sns) == len(set(sns)), "two pieces with one serial number"
        kinds.update(d["k"] for d in _descs(snap) if "sn" in d)
        assert snap["fgw"] == {m: [p.serial for p in q] for m, q in plant.stock.items()}
        for m, serials in snap["fgw"].items():
            assert len(serials) == len(set(serials)) == plant.warehouse[m]["pallets"]
    assert {"P", "B", "U"} <= set(kinds), kinds          # panels, boards and products have their number on the plan


def test_snapshot_tells_the_truth_all_day(model):
    plant = _plant(model)
    sampled = Counter()
    for minute in range(10, 30 * 60, 10):
        plant.env.run(until=minute * 60)
        snap = plant.floor_snapshot()
        _check_snapshot(plant, snap)
        for e in snap["stations"].values():
            sampled[e["ph"]] += 1
        sampled["rides"] += len(snap["tr"])
    # the run covered every kind of moment the page has to draw
    assert {"idle", "in", "work", "out", "pass"} <= set(sampled), sampled
    assert sampled["rides"] > 100


def test_a_machine_only_works_when_a_piece_is_inside(model):
    """The complaint that started this: tools moving over an empty machine."""
    plant = _plant(model, seed=9)
    checked = 0
    for second in range(120, 8 * 3600, 90):
        plant.env.run(until=second)
        for st in plant.all_stations():
            if st.phase in ("work", "verify"):
                assert st.item is not None, f"{st.code} works on nothing at {second}"
                total, left = st.work_state()
                assert total >= left >= 0
                checked += 1
            if st.phase == "idle":
                assert st.item is None and not st.out_items
    assert checked > 500


def test_pieces_look_like_their_product_and_their_history(model):
    plant = _plant(model, seed=3)
    plant.env.run(until=6 * 3600)
    snap = plant.floor_snapshot()
    products = set()
    pieces = [d for e in snap["stations"].values() for d in ([e["it"]] if "it" in e else []) + e.get("out", [])]
    pieces += [r["it"] for r in snap["tr"]]
    assert pieces
    for d in pieces:
        if d["k"] in ("P", "B", "U"):
            assert d["p"] in model.materials or f"PCBA-{d['p']}" in model.materials
        if d["k"] == "P":
            assert d["n"] == model.materials[f"PCBA-{d['p']}"].boards_per_panel
            assert set(d["d"]) <= {"LDR", "LSR", "PRN", "SPI", "PP1", "PP2", "OVN", "AOI", "ULD", "DPN"}
        if d["k"] == "U":
            assert d["p"] in model.materials
            done = set(d["d"])
            # final assembly steps of a product are the ones it was routed through
            allowed = {plant.tags[t] for t in model.materials[d["p"]].routing[0].steps}
            assert done <= allowed, (d["p"], done - allowed)
            products.add(d["p"])
    assert len(products) >= 2


# ---------------------------------------------------------------- conveyors take time

def test_a_piece_arrives_when_the_belt_has_carried_it(model):
    plant = _plant(model, seed=5)
    plant.env.run(until=3 * 3600)
    ride = next(r for r in plant.logistics.rides.values() if r.route.startswith("sm:") and r.t0 + r.dur - plant.env.now > 5)
    shelf = plant.supermarket[ride.item.material]
    arrival = ride.t0 + ride.dur
    assert ride.dur == pytest.approx(plant.layout.duration(ride.route))
    plant.env.run(until=arrival - 1)
    assert ride.id in plant.logistics.rides                      # still on the belt one second before the end
    assert ride.item.id not in {i.id for i in shelf.items}       # and not on the shelf yet
    plant.env.run(until=arrival + 1)
    assert ride.id not in plant.logistics.rides                  # delivered


def test_bare_panels_and_boards_arrive_by_belt(model):
    plant = _plant(model, seed=2)
    low = {code: PANEL_RACK for code in plant.panel_rack}
    for minute in range(1, 8 * 60):
        plant.env.run(until=minute * 60)
        for code, rack in plant.panel_rack.items():
            low[code] = min(low[code], rack.level)
        for code, room in plant.panel_room.items():
            # room is only claimed for magazines on their way, never more than the rack has space for
            assert rack.level + room.level <= PANEL_RACK + 10
    assert all(v < PANEL_RACK for v in low.values()), "SMT lines used panels from the rack"
    assert all(v > 0 for v in low.values()), "the goods-in warehouse keeps the racks stocked"


# ---------------------------------------------------------------- ovens

def test_oven_belt_stands_still_while_the_oven_is_broken(model):
    plant = _plant(model, seed=4)
    oven = plant.lines["SMT-03"].stations[6]
    assert oven.type.code == "REFLOW_OVEN"
    t = 0
    while len(oven.inside_items) < 5 and t < 20000:
        t += 10
        plant.env.run(until=t)
    assert len(oven.inside_items) >= 5
    oven.ttf = 0                                    # breaks down when the next piece arrives
    while oven.state is not State.DOWN and t < 40000:
        t += 2
        plant.env.run(until=t)
    assert oven.state is State.DOWN and oven.belt_since is None

    def progress():
        return {i.id: round(oven.belt_time() - entered, 6) for i, entered in oven.inside_items}

    before = progress()
    assert before
    waited = 0
    while oven.state is State.DOWN:
        t += 15
        plant.env.run(until=t)
        if oven.state is State.DOWN:
            now = progress()
            assert all(now[i] == before[i] for i in before if i in now), "pieces moved with the belt stopped"
            assert set(before) <= set(now), "a piece left the oven while it was broken"
            waited += 15
    assert waited > 0
    plant.env.run(until=t + 60)
    assert oven.belt_since is not None
    after = progress()
    assert any(after[i] > before[i] for i in before if i in after)


# ---------------------------------------------------------------- packing and repair details

def test_boxes_and_pallets_report_their_filling(model):
    plant = _plant(model, seed=6)
    plant.env.run(until=8 * 3600)
    snap = plant.floor_snapshot()
    fills = [(code, e["fill"]) for code, e in snap["stations"].items() if "fill" in e]
    assert fills
    for code, (n, size, sn) in fills:
        assert 0 <= n < size or size == 0, (code, n, size)
    klt = [d for e in snap["stations"].values() for d in ([e["it"]] if "it" in e else []) if d["k"] == "K"]
    for d in klt:
        assert d["n"] <= d["c"]


def test_robots_carry_rejected_pieces_to_repair_and_back(model):
    """Nothing flies: a rejected piece goes into the NOK tote at the machine, a robot takes it to the
    inbound rack of the repair centre, the repair person fetches it, and after the repair a robot
    takes it from the outbound rack back to the line (boards to the supermarket)."""
    plant = _plant(model, seed=7)
    kinds = Counter()
    send = plant.logistics.send

    def counting(route, *args, **kwargs):
        kinds[route.split(":")[0]] += 1
        return send(route, *args, **kwargs)

    plant.logistics.send = counting
    delivered = Counter()
    deliver = plant.fleet._deliver

    def counting_deliver(item, dest):
        delivered[dest[0]] += 1
        return deliver(item, dest)

    plant.fleet._deliver = counting_deliver
    held = carried = 0
    for minute in range(10, 36 * 60, 10):
        plant.env.run(until=minute * 60)
        snap = plant.floor_snapshot()
        held += sum(1 for p in snap["people"] if "h" in p)
        carried += sum(len(r.get("ld", [])) for r in snap["amr"])
        for robot in snap["amr"]:
            assert 0 <= robot["b"] <= 100
    assert delivered["repair"] > 0 and delivered["cell"] > 0 and delivered["sm"] > 0, delivered
    assert held > 0, "the repair people never had a piece in their hands"
    assert carried > 0, "no robot was ever seen with a load"
    assert kinds["wash"] > 0, kinds                              # misprinted panels go to the washer
    assert kinds["ret"] > 0 and kinds["feed"] > 0 and kinds["pack"] > 0 and kinds["wh"] > 0, kinds
    assert not [k for k in kinds if k in ("rwi", "rwo", "rpr2sm")]
    assert plant.fleet.done > 0
    assert math.isfinite(plant.env.now)
