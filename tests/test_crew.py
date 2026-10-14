"""The people of the plant: seats, shifts, breaks, technicians and where they walk.
What the page draws is what the crew model really does."""

import math
from collections import Counter
from datetime import UTC, datetime

import pytest

from mes_lab.layout import build_layout
from mes_lab.sim.core import PlantClock
from mes_lab.sim.dispatch import LocalDispatcher
from mes_lab.sim.plant import PlantSim

SETTLE_S = 900           # around a shift change the seats are in motion


def _plant(model, seed=4):
    return PlantSim(model, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(model, seed), seed=seed)


def _calm(plant, t):
    """True when t is not near a shift change (people are walking in and out then)."""
    _, start, end = plant.crew.shift_window(t)
    return t - start > SETTLE_S and end - t > SETTLE_S


@pytest.fixture(scope="module")
def day(model):
    """One plant run of 31 hours, looked at every quarter of an hour."""
    plant = _plant(model)
    samples = []
    peak_break: dict[int, float] = {}
    for minute in range(15, 31 * 60, 15):
        plant.env.run(until=minute * 60)
        crew = plant.crew
        for person in crew.persons.values():
            peak_break[person.id] = max(peak_break.get(person.id, 0.0), person.break_s)
        samples.append({
            "t": minute * 60.0,
            "snap": plant.floor_snapshot(),
            "occupants": {k: (s.occupant.id if s.occupant else None) for k, s in crew.seats.items()},
            "empty_at_wc": {k: crew.roster[crew.current_shift]["primaries"][k].task == "wc"
                            for k, s in crew.seats.items() if s.occupant is None},
            "calm": _calm(plant, minute * 60.0),
            "shift": crew.shift_window(minute * 60.0),
        })
    plant.peak_break = peak_break
    return plant, samples


# ---------------------------------------------------------------- the plan

def test_every_machine_with_an_operator_has_exactly_one_seat(model):
    layout = build_layout(model)
    owner: dict[str, list[str]] = {}
    for key, seat in layout.seats.items():
        for code in seat["attends"]:
            owner.setdefault(code, []).append(key)
    assert all(len(v) == 1 for v in owner.values()), {c: v for c, v in owner.items() if len(v) != 1}
    unattended = [c for c, s in model.stations.items() if c not in owner]
    # everything on a line that is not a pure machine belt has somebody; the rest are conveyors / stores
    assert len(unattended) <= len(model.stations) * 0.1, unattended
    for code in owner:
        assert code in layout.stations


def test_the_staff_rooms_the_board_and_the_bays_do_not_overlap_anything(model):
    layout = build_layout(model)
    rects = {f"station {c}": (s["x"], s["y"], s["w"], s["h"]) for c, s in layout.stations.items()}
    rects.update({f"building {b['id']}": (b["x"], b["y"], b["w"], b["h"]) for b in layout.data["buildings"]})
    board = layout.data["board"]
    rects["board"] = (board["x"], board["y"], board["w"], board["h"])
    depot = layout.data["depot"]
    rects["AMR depot"] = (depot["x"], depot["y"], depot["w"], depot["h"])
    items = list(rects.items())
    for i, (a_name, a) in enumerate(items):
        assert 0 <= a[0] and a[0] + a[2] <= layout.world["w"], a_name
        assert 0 <= a[1] and a[1] + a[3] <= layout.world["h"], a_name
        for b_name, b in items[i + 1:]:
            apart = a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]
            inside_repair = "RPR" in a_name + b_name and "station RPR-" in a_name + b_name     # the repair centre holds its benches
            assert apart or inside_repair, f"{a_name} overlaps {b_name}"
    fgw = next(b for b in layout.data["buildings"] if b["id"] == "FGW")
    for bay in layout.data["bays"]:
        assert fgw["x"] <= bay["x"] - bay["trailer_w"] / 2 and bay["x"] + bay["trailer_w"] / 2 <= fgw["x"] + fgw["w"]
        assert bay["rear_y"] + bay["trailer_l"] + bay["cab_l"] <= layout.world["h"]


# the street side of the entrance: from there into the plant only through an ESD gate (and out through the turnstile)
STREET = ("door", "esd:", "esdq", "exits")


def _inside(keys):
    return [k for k in keys if not k.startswith(STREET) and not k.startswith(("street:", "dockwait:", "dock:"))]


def test_every_place_can_be_reached_on_foot(model):
    layout = build_layout(model)
    walk = layout.walk
    places = list(walk.places)
    assert "door" in places and any(p.startswith("spot:") for p in places)
    for key in _inside(places):
        pts = walk.path("esdx:0", key)                    # just through the ESD gate
        assert len(pts) >= 2 or key == "esdx:0"
        for x, y in pts:
            assert 0 <= x <= layout.world["w"] and 0 <= y <= layout.world["h"], key
        assert math.isfinite(walk.duration("esdx:0", key))
    # the way in: entrance -> queue -> gate -> through it; the way out: queue -> turnstile -> entrance
    for a, b in (("door", "esdq0:4"), ("esdq0:0", "esd:0"), ("esd:0", "esdx:0"), ("exitq:0", "exitn"), ("exitn", "exits"),
                 ("exits", "door"), ("street:log", "dockwait:0")):
        assert math.isfinite(walk.duration(a, b)) and len(walk.path(a, b)) >= 2
    # the way there and back takes the same time
    far = next(k for k in places if k.startswith("spot:PACK-"))
    assert walk.duration("door", far) == pytest.approx(walk.duration(far, "door"))
    assert walk.duration("door", far) > 5


def _rects(layout):
    """What nobody may walk through: machines, the buildings that are not rooms, furniture, the racks,
    the quarantine cage and the scrap bin of the repair centre."""
    out = {f"station {c}": (s["x"], s["y"], s["w"], s["h"]) for c, s in layout.stations.items()}
    for b in layout.data["buildings"]:
        if b["id"] not in ("STAFF", "RPR"):
            out[f"building {b['id']}"] = (b["x"], b["y"], b["w"], b["h"])
    for f in layout.data["furniture"]:
        if f["kind"] in ("table", "locker", "rack", "desk", "shelf", "sink", "vending", "coffee", "printer", "andon", "cabinet", "railing"):
            out[f"{f['kind']} in {f['room']} at {f['x']:.0f},{f['y']:.0f}"] = (f["x"], f["y"], f["w"], f["h"])
    for d in layout.data["desks"]:
        out[f"team desk {d['id']}"] = (d["x"], d["y"], d["w"], d["h"])
    rpr = next(b for b in layout.data["buildings"] if b["id"] == "RPR")
    for key in ("inrack", "outrack", "quarantine", "scrap"):
        r = rpr[key]
        out[f"repair {key}"] = (r["x"], r["y"], r["w"], r["h"])
    return out


def _hits(a, b, rect, pad=0.3):
    x, y, w, h = rect
    x0, y0, x1, y1 = x + pad, y + pad, x + w - pad, y + h - pad
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                if t > t1:
                    return False
                t0 = max(t0, t)
            else:
                if t < t0:
                    return False
                t1 = min(t1, t)
    return t0 < t1


def _crosses(a, b, wall):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, wall[0]) * o(a, b, wall[1]) < 0 and o(wall[0], wall[1], a) * o(wall[0], wall[1], b) < 0


def _walls(layout):
    """The walls of the rooms with a gap at every door: the staff building and the repair centre."""
    data = layout.data
    staff = next(b for b in data["buildings"] if b["id"] == "STAFF")
    walls = [((r["x"], r["y"]), (r["x"], r["y"] + r["h"])) for r in data["rooms"]]
    walls.append(((staff["x"] + staff["w"], staff["y"]), (staff["x"] + staff["w"], staff["y"] + staff["h"])))
    gaps = sorted((r["door"][0] - 7, r["door"][0] + 7) for r in data["rooms"])
    x = staff["x"]
    for g0, g1 in gaps:                                                   # the top wall has a gap at every door
        walls.append(((x, staff["y"]), (g0, staff["y"])))
        x = g1
    walls.append(((x, staff["y"]), (staff["x"] + staff["w"], staff["y"])))
    ent = next(r for r in data["rooms"] if r["id"] == "lockers")["entrance"]
    walls.append(((staff["x"], staff["y"] + staff["h"]), (ent[0] - 8, staff["y"] + staff["h"])))
    walls.append(((ent[0] + 8, staff["y"] + staff["h"]), (staff["x"] + staff["w"], staff["y"] + staff["h"])))
    for f in data["furniture"]:                                           # the thin walls of the toilet cabins
        if f["kind"] == "cabin":
            walls += [((ax, ay), (bx, by)) for ax, ay, bx, by in f["walls"]]
    rpr = next(b for b in data["buildings"] if b["id"] == "RPR")
    x0, y0, x1, y1 = rpr["x"], rpr["y"], rpr["x"] + rpr["w"], rpr["y"] + rpr["h"]
    d0, d1 = rpr["door"][0] - rpr["door_w"] / 2, rpr["door"][0] + rpr["door_w"] / 2
    walls += [((x0, y0), (d0, y0)), ((d1, y0), (x1, y0)), ((x0, y0), (x0, y1)), ((x1, y0), (x1, y1)), ((x0, y1), (x1, y1))]
    return walls


def test_nobody_walks_through_walls_furniture_or_machines(model):
    layout = build_layout(model)
    walk = layout.walk
    rects = _rects(layout)
    walls = _walls(layout)
    import random
    keys = _inside(walk.places)
    rng = random.Random(1)
    street = [k for k in walk.places if k.startswith(STREET)]
    pairs = [("esdx:0", k) for k in keys] + [(k, "exitq:3") for k in keys] + [tuple(rng.sample(keys, 2)) for _ in range(1200)]
    pairs += [(a, b) for a in street for b in street if a != b and (a, b) != ("esd:0", "esdx:0")]   # within the street side
    rpr_keys = [k for k in keys if k.startswith(("rprin:", "rprout:", "scrap:", "spot:RPR"))]
    pairs += [(a, b) for a in rpr_keys for b in rpr_keys if a != b]      # inside the repair centre, around the benches
    furniture = {n: r for n, r in rects.items() if " in " in n or n.startswith(("repair ", "team desk"))}
    for a, b in pairs:
        pts = walk.path(a, b)
        for p, q in zip(pts, pts[1:]):
            for name, rect in rects.items():
                assert not _hits(p, q, rect), f"{a} -> {b} goes through {name}"
            for wall in walls:
                assert not _crosses(p, q, wall), f"{a} -> {b} goes through the wall {wall}"
            # a person is not a point: the way keeps the shoulders off the furniture
            n = max(1, int(math.dist(p, q) / 2))
            for i in range(n + 1):
                x, y = p[0] + (q[0] - p[0]) * i / n, p[1] + (q[1] - p[1]) * i / n
                for name, (rx, ry, rw, rh) in furniture.items():
                    gap = math.hypot(max(rx - x, 0, x - rx - rw), max(ry - y, 0, y - ry - rh))
                    assert gap >= 2.5, f"{a} -> {b} brushes {name} ({gap:.1f} from it at {x:.0f},{y:.0f})"


def test_toilets_have_cabins_you_walk_into_through_their_door(model):
    layout = build_layout(model)
    walk = layout.walk
    walls = _walls(layout)
    cabins = [f for f in layout.data["furniture"] if f["kind"] == "cabin"]
    assert {f["sex"] for f in cabins} == {"m", "w"} and len(cabins) >= 4
    for f in cabins:
        place = next(p for k, p in walk.places.items() if k.startswith(f"wc{f['sex']}:")
                     and f["x"] < p.x < f["x"] + f["w"] and f["y"] < p.y < f["y"] + f["h"])
        for start in ("esdx:0", "desk:FA-01", f"sink{f['sex']}:0"):
            pts = walk.path(start, place.key)
            for p, q in zip(pts, pts[1:]):
                for wall in walls:
                    assert not _crosses(p, q, wall), f"{start} -> {place.key} goes through {wall}"
            # the last bit goes in through the door of the cabin
            dx, dy = f["door"]
            assert any(abs(y - dy) < 4 and abs(x - dx) < f["door_w"] / 2 for x, y in _densify(pts)), (start, pts)


def _densify(pts, step=1.0):
    out = []
    for p, q in zip(pts, pts[1:]):
        n = max(1, int(math.dist(p, q) / step))
        out += [(p[0] + (q[0] - p[0]) * i / n, p[1] + (q[1] - p[1]) * i / n) for i in range(n + 1)]
    return out


def test_the_logistics_corner_has_a_table_people_walk_around(model):
    layout = build_layout(model)
    walk = layout.walk
    table = next(f for f in layout.data["furniture"] if f.get("cards"))
    rect = (table["x"], table["y"], table["w"], table["h"])
    lounge = [k for k in walk.places if k.startswith("lounge:")]
    waits = [k for k in walk.places if k.startswith("dockwait:")]
    assert len(lounge) >= 4 and waits
    pairs = [(a, b) for a in lounge + waits for b in lounge + waits if a != b]
    pairs += [(a, f"dock:{bay}") for a in lounge for bay in range(len(layout.data["bays"]))]
    pairs += [("street:log", a) for a in lounge]
    for a, b in pairs:
        pts = walk.path(a, b)
        for p, q in zip(pts, pts[1:]):
            assert not _hits(p, q, rect), f"{a} -> {b} walks over the table"
        for x, y in _densify(pts, 2.0):
            gap = math.hypot(max(rect[0] - x, 0, x - rect[0] - rect[2]), max(rect[1] - y, 0, y - rect[1] - rect[3]))
            assert gap >= 2.5, f"{a} -> {b} brushes the table"


def test_robots_drive_in_the_aisles_not_through_anything(model):
    layout = build_layout(model)
    amr = layout.amr
    rects = _rects(layout)
    rects.update({f"building {b['id']}": (b["x"], b["y"], b["w"], b["h"]) for b in layout.data["buildings"]})
    rects["board"] = tuple(layout.data["board"][k] for k in ("x", "y", "w", "h"))
    walls = _walls(layout)
    import random
    keys = list(amr.places)
    assert {"rpr:in", "rpr:out", "rpr:quar", "drop:SM", "gi"} <= set(keys)
    assert all(f"gap:{st['line']}:{st['cell']}" in keys for c, st in layout.stations.items() if not c.startswith("RPR"))
    rng = random.Random(2)
    pairs = [(f"charge:{i}", k) for i in range(model.amr.robots) for k in keys] + [tuple(rng.sample(keys, 2)) for _ in range(800)]
    for a, b in pairs:
        pts = amr.path(a, b)
        for p, q in zip(pts, pts[1:]):
            for name, rect in rects.items():
                assert not _hits(p, q, rect), f"robot {a} -> {b} drives through {name}"
            for wall in walls:
                assert not _crosses(p, q, wall), f"robot {a} -> {b} drives through the wall {wall}"


def test_where_a_walkway_passes_a_conveyor_the_conveyor_goes_over_it(model):
    """No person and no robot walks through a belt: every place where a path passes a conveyor is a
    bridge (the belts to the trucks: a lift gate), and the page draws it."""
    layout = build_layout(model)
    crossings = layout.data["crossings"]
    assert crossings and {c["kind"] for c in crossings} <= {"bridge", "gate"}
    belts = [(c, d, key) for key, r in layout.routes.items() if r.kind in ("supply", "sm", "feed", "pack", "wh", "ret", "wash", "ship")
             for c, d in zip(r.pts, r.pts[1:])]
    import random
    rng = random.Random(3)
    for net in (layout.walk, layout.amr):
        keys = list(net.places)
        pairs = [tuple(rng.sample(keys, 2)) for _ in range(900)]
        for a, b in pairs:
            pts = net.path(a, b)
            for p, q in zip(pts, pts[1:]):
                for c, d, key in belts:
                    if _crosses(p, q, (c, d)):
                        x, y = _meet(p, q, c, d)
                        near = min(math.dist((x, y), (k["x"], k["y"])) - k["len"] / 2 for k in crossings)
                        assert near < 1.0, f"{a} -> {b} passes {key} at {x:.0f},{y:.0f} without a bridge"
    # the board return belt of an SMT line runs underneath the machines: nobody crosses it
    assert not any(c["route"].startswith("ret:") for c in crossings)


def _meet(a, b, c, d):
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
    return a[0] + t * r[0], a[1] + t * r[1]


def test_everybody_has_a_workstation_of_their_own_and_faces_the_screen(model, day):
    plant, samples = day
    layout = plant.layout
    crew = plant.crew
    places = layout.walk.places
    for role in ("MAINTENANCE", "IT", "FOREMAN"):
        bases = [p.base for p in crew.persons.values() if p.role == role]
        assert len(bases) == len(set(bases)), role                         # nobody shares a desk, not even across the shifts
        assert all(places[b].pc and places[b].look is not None for b in bases)
    log = [p.base for p in crew.persons.values() if p.role == "LOGISTICS"]
    assert len(log) == len(set(log))
    relief = [p.base for p in crew.persons.values() if p.role == "OPERATOR" and p.home_seat is None]
    assert len({(b) for b in relief}) >= len(relief) // 2                  # reliefs of different shifts have their own places
    # a person at a computer looks at the screen: the angle in the snapshot points to it
    seen = 0
    for sample in samples:
        for rec in sample["snap"]["people"]:
            if rec.get("pc"):
                seen += 1
                assert "f" in rec
            if "f" in rec and "x" in rec and rec["a"] in ("idle", "post"):
                place = next((pl for pl in places.values() if abs(pl.x - rec["x"]) < 0.06 and abs(pl.y - rec["y"]) < 0.06 and pl.look), None)
                if place is not None:
                    want = round(math.degrees(math.atan2(place.look[1] - place.y, place.look[0] - place.x)))
                    assert rec["f"] == want
    assert seen > 200                                                       # people were typing


def test_it_takes_the_laptop_and_maintenance_fetches_the_instruments_when_the_fault_needs_them(model):
    plant = _stand(model)
    crew = plant.crew
    st = next(s for s in plant.all_stations() if s.code.startswith("PACK-01"))
    t0 = plant.env.now
    done = plant.env.process(crew.service(st, "PRINTER_FAULT", 300))
    plant.env.run(until=t0 + 2)
    person = st.call.person
    assert person.role == "IT" and person.carry == "laptop"
    assert any(r.get("c") == "laptop" for r in plant.floor_snapshot()["people"])
    plant.env.run(until=done)
    plant.env.run(until=plant.env.now + 400)
    assert person.carry is None                                             # back at the desk, the laptop is on it

    mech = next(s for s in plant.all_stations() if s.code.startswith("FA-03"))
    t1 = plant.env.now
    mech_done = plant.env.process(crew.service(mech, "CONVEYOR_JAM", 200))
    plant.env.run(until=t1 + 1)
    assert mech.call.person.carry == "tools"                                # a mechanical fault: the tool box is enough
    plant.env.run(until=mech_done)

    elec = next(s for s in plant.all_stations() if s.code.startswith("SMT-02"))
    t2 = plant.env.now
    elec_done = plant.env.process(crew.service(elec, "SENSOR_FAULT", 200))
    plant.env.run(until=t2 + 1)
    tech = elec.call.person
    assert tech.carry is None and tech.place != "instr" or tech.walk is not None      # first to the cabinet in the workshop
    plant.env.run(until=t2 + 600)
    assert tech.carry == "meter" or elec.call is None
    plant.env.run(until=elec_done)
    # the fetch and the walk to the machine are part of the repair
    assert plant.env.now - t2 > 200 + 15


def test_every_station_has_a_place_to_stand_at(model):
    layout = build_layout(model)
    for code in model.stations:
        assert f"spot:{code}" in layout.walk.places, code


# ---------------------------------------------------------------- one day of people

def test_every_seat_is_staffed_by_one_person_between_shift_changes(day):
    plant, samples = day
    calm = [s for s in samples if s["calm"]]
    assert len(calm) > 60
    for s in calm:
        occupants = s["occupants"]
        # a seat is empty only while its person is on the toilet
        assert all(v is not None or s["empty_at_wc"][k] for k, v in occupants.items()), (s["t"], [k for k, v in occupants.items() if v is None])
        taken = [v for v in occupants.values() if v is not None]
        assert len(set(taken)) == len(taken), s["t"]
        assert sum(1 for v in occupants.values() if v is None) <= 4


def test_head_count_matches_the_roster_of_the_shift(day):
    plant, samples = day
    for s in samples:
        if not s["calm"]:
            continue
        shifts = s["snap"]["crew"]["shifts"]
        current = s["snap"]["crew"]["now"]
        for code, c in shifts.items():
            if code == current:
                assert c["on_site"] == c["roster"], (s["t"], code, c)
            else:
                # nobody of the other shifts is here, except a technician who finishes a repair he began
                rest = [p for p in s["snap"]["people"] if p["sh"] == code]
                assert all(p["r"] in ("MAI", "IT") and p["a"] in ("fix", "walk") for p in rest), (s["t"], code, rest)
    rosters = {code: c["roster"] for code, c in samples[0]["snap"]["crew"]["shifts"].items()}
    assert set(rosters) == {"A", "B", "C"}
    assert rosters["A"] > rosters["C"] > 30        # the night shift has fewer technicians and loaders


def test_everybody_has_a_half_hour_lunch_and_ten_minutes_of_coffee_in_turns(day):
    plant, samples = day
    cfg = plant.model.crew
    relief = cfg.shifts["A"].relief
    assert [(b.kind, b.length) for b in cfg.breaks] == [("coffee", 10), ("lunch", 30)]    # the coffee is on top of the lunch
    peak = 0
    for s in samples:
        on_break = Counter(p["sh"] for p in s["snap"]["people"] if p["a"] in ("break", "coffee") and p["r"] == "OPE")
        peak = max(peak, max(on_break.values(), default=0))
        code, start, end = s["shift"]
        for p in s["snap"]["people"]:
            if p["a"] in ("break", "coffee"):                           # not in the first two hours
                assert start + 120 * 60 <= s["t"] <= end, (p, s["t"] - start)
    assert 1 <= peak <= relief + 2        # a relief person for every seat on a break, plus a technician at times
    # nobody is away longer than 30 + 10 minutes, and most people really had them
    assert max(plant.peak_break.values()) <= 40 * 60 + 120     # (a minute more when the way back is busy)
    assert sum(1 for v in plant.peak_break.values() if v >= 39 * 60) > 100


def test_reliefs_wait_at_the_team_desks_and_nobody_idles_in_the_break_room(day):
    plant, samples = day
    layout = plant.layout
    staff = next(b for b in layout.data["buildings"] if b["id"] == "STAFF")
    room = next(r for r in layout.data["rooms"] if r["id"] == "break")
    seen = 0
    for s in samples:
        for p in s["snap"]["people"]:
            if "x" not in p:
                continue
            in_staff = staff["y"] <= p["y"] <= staff["y"] + staff["h"] and staff["x"] <= p["x"] <= staff["x"] + staff["w"]
            if p["r"] == "OPE" and p["a"] in ("idle", "post", "fix"):
                assert not in_staff, p                                   # operators wait at the lines
                seen += p["a"] == "idle"
            in_break = room["x"] <= p["x"] <= room["x"] + room["w"] and in_staff
            if in_break:
                assert p["a"] in ("break", "coffee", "walk"), p           # only people on their half hour sit there
    assert seen > 50                                                     # there were reliefs waiting


def test_a_shift_change_lets_the_new_people_in_at_the_door_and_the_old_ones_out(day):
    plant, samples = day
    changes = {}
    for s in samples:
        code, start, end = s["shift"]
        changes.setdefault(start, code)
    started = sorted(t for t in changes if t > 1800)
    assert len(started) >= 2
    t_change = started[0]

    def at(delta):
        return min(samples, key=lambda s: abs(s["t"] - (t_change + delta)))
    before, just, later = at(-900), at(300), at(3600 + 900)
    new_code = just["snap"]["crew"]["now"]
    old_code = before["snap"]["crew"]["now"]
    assert new_code != old_code
    # right after the change both shifts are on site, and many people are walking
    shifts = just["snap"]["crew"]["shifts"]
    assert shifts[new_code]["on_site"] > 0 and shifts[old_code]["on_site"] > 0
    around = [x for x in samples if abs(x["t"] - t_change) <= 900]
    assert max(sum(c["walking"] for c in x["snap"]["crew"]["shifts"].values()) for x in around) > 2
    # an hour later the old ones are gone and the new ones sit in their seats
    assert later["snap"]["crew"]["shifts"][old_code]["on_site"] == 0


def test_snapshot_of_people_is_consistent(day):
    plant, samples = day
    layout = plant.layout
    world = layout.world
    for s in samples:
        snap = s["snap"]
        people = snap["people"]
        ids = [p["i"] for p in people]
        assert len(ids) == len(set(ids))
        per_shift = Counter(p["sh"] for p in people)
        for code, c in snap["crew"]["shifts"].items():
            assert per_shift.get(code, 0) == c["on_site"]
            assert c["posts"] <= c["on_site"] and c["break"] <= c["on_site"] and c["walking"] <= c["on_site"]
        for p in people:
            assert p["r"] in {"OPE", "MAI", "IT", "FOR", "LOG", "MAN"}
            if "w" in p:
                pts = p["w"]["p"]
                assert len(pts) >= 2 and p["w"]["d"] > 0
                assert all(0 <= x <= world["w"] and 0 <= y <= world["h"] for x, y in pts)
                # a walk may start a little later: the person lets somebody pass first (traffic.py)
                assert p["w"]["t"] - 40 <= s["t"] <= p["w"]["t"] + p["w"]["d"] + 1, (p, s["t"])
            else:
                assert 0 <= p["x"] <= world["w"] and 0 <= p["y"] <= world["h"]
            if "seat" in p:
                assert p["seat"] in layout.data["seats"]
            if "s" in p:
                assert p["s"] in layout.stations or p["s"].startswith("dock:")
        by_id = set(ids)
        for c in snap["crew"]["calls"]:
            assert c["st"] in layout.stations
            assert c["ph"] in {"wait", "walk", "fix"}
            if c["ph"] in {"walk", "fix"}:
                assert c["p"] in by_id
            else:
                assert c["p"] is None


def test_roles_are_present_and_doing_their_jobs(day):
    plant, samples = day
    seen = Counter()
    for s in samples:
        for p in s["snap"]["people"]:
            seen[(p["r"], p["a"])] += 1
    for role in ("OPE", "MAI", "IT", "FOR", "LOG"):
        assert sum(v for (r, _), v in seen.items() if r == role) > 0, role
    assert seen[("MAI", "fix")] > 0                  # maintenance repairs machines
    assert seen[("OPE", "walk")] > 0 and seen[("OPE", "post")] > 0
    assert seen[("FOR", "walk")] > 0                 # the foreman walks his rounds


def test_everybody_passes_the_esd_gate_and_technicians_wear_a_coat(day):
    plant, samples = day
    crew = plant.crew
    cfg = plant.model.crew.esd
    role = {p.id: p.role for p in crew.persons.values()}
    assert crew.esd_log and crew.coat_log
    assert {role[pid] for pid, _ in crew.esd_log} == {"OPERATOR", "MAINTENANCE", "IT", "FOREMAN", "MANAGER"}   # logistics stays outside
    assert {role[pid] for pid, _ in crew.coat_log} <= {"MAINTENANCE", "IT", "FOREMAN", "MANAGER"}
    # everybody of the shifts that came in through the entrance went through the gate / put on a coat
    came = {p.id for p in crew.persons.values() if p.shift in ("B", "C")}
    passed = {pid for pid, _ in crew.esd_log}
    assert {pid for pid in came if role[pid] != "LOGISTICS"} <= passed
    coated = {pid for pid, _ in crew.coat_log}
    assert {pid for pid in came if role[pid] in ("MAINTENANCE", "IT", "FOREMAN", "MANAGER")} <= coated
    # one person at a time on a gate
    times = sorted(t for _, t in crew.esd_log)
    for i, t in enumerate(times):
        assert sum(1 for u in times[max(0, i - 10):i + 1] if u > t - cfg.check_s + 0.01) <= cfg.gates, t
    # what the plan shows: protected people carry the sign of it
    for s in samples:
        if not s["calm"]:
            continue
        for p in s["snap"]["people"]:
            if p["r"] == "OPE":
                assert p.get("e") == "g", p
            elif p["r"] in ("MAI", "IT", "FOR", "MAN"):
                assert p.get("e") == "c", p
            else:
                assert "e" not in p


def test_people_go_to_the_toilet_now_and_then_and_the_machine_waits_for_them(model, day):
    plant, samples = day
    crew = plant.crew
    cfg = plant.model.crew.wc
    assert len(crew.wc_log) > 100
    by_person: dict[int, list[float]] = {}
    for pid, t in crew.wc_log:
        by_person.setdefault(pid, []).append(t)
    for pid, times in by_person.items():                              # a few trips per shift, not more
        times.sort()
        for i in range(len(times) - cfg.trips):
            assert times[i + cfg.trips] - times[i] > 12 * 3600, (pid, times)
    acts = Counter(p["a"] for s in samples for p in s["snap"]["people"])
    assert acts["wc"] > 0

    fresh = _plant(model)
    fresh.env.run(until=2400)
    seat = next(s for s in fresh.crew.seats.values() if s.fixed and s.occupant is not None)
    person = seat.occupant
    station = next(st for st in fresh.all_stations() if st.code == seat.attends[0])
    t0 = fresh.env.now
    fresh.env.process(fresh.crew._go_to_wc(person))
    fresh.env.run(until=t0 + 1)
    assert seat.occupant is None and person.task == "wc"
    done = fresh.env.process(fresh.crew.attend(station, 20.0))
    fresh.env.run(until=done)
    assert fresh.env.now - t0 > cfg.minutes * 60 * 0.7                # the machine waited for the operator
    assert seat.occupant is person and person.task is None


def test_women_have_female_surnames_and_use_the_womens_toilet(day):
    plant, samples = day
    crew = plant.crew
    people = list(crew.persons.values())
    women = [p for p in people if p.sex == "w"]
    men = [p for p in people if p.sex == "m"]
    assert women and men
    assert all(p.name.split()[-1].endswith(("ová", "ská")) for p in women), [p.name for p in women]
    assert not any(p.name.split()[-1].endswith(("ová", "ská")) for p in men), [p.name for p in men]
    ops = [p for p in people if p.role == "OPERATOR"]
    assert 0.4 < sum(p.sex == "w" for p in ops) / len(ops) < 0.8            # assembly: mostly women
    maint = [p for p in people if p.role == "MAINTENANCE"]
    assert sum(p.sex == "w" for p in maint) <= len(maint) / 2               # maintenance: mostly men
    sex = {p.id: p.sex for p in people}
    assert crew.wc_used and all(cabin.startswith(f"wc{sex[pid]}:") for pid, cabin in crew.wc_used)
    snap_women = {p["i"] for s in samples for p in s["snap"]["people"] if p.get("g") == "w"}
    assert snap_women and snap_women <= {p.id for p in women}


def test_logistics_sit_down_at_their_table_cards_with_a_colleague_a_book_alone(day):
    plant, samples = day
    walk = plant.layout.walk
    lounge = {(round(p.x, 1), round(p.y, 1)) for k, p in walk.places.items() if k.startswith("lounge:")}
    seen = Counter()
    for s in samples:
        sitting = [p for p in s["snap"]["people"] if p["a"] in ("cards", "read")]
        for p in sitting:
            assert p["r"] == "LOG" and (p["x"], p["y"]) in lounge, p
            seen[p["a"]] += 1
        if sitting:
            assert {p["a"] for p in sitting} == ({"cards"} if len(sitting) > 1 else {"read"}), sitting
    assert seen["read"] + seen["cards"] > 0


def test_at_the_end_of_the_shift_technicians_hand_over_and_go_home_without_the_coat(model):
    """Nobody of the old shift sits at the desk long after the end (unless a repair is still in hand), the
    ESD coat stays in the locker, and nobody stands long at a free turnstile."""
    plant = _plant(model)
    crew = plant.crew
    _, start, end = crew.shift_window(0.0)
    plant.env.run(until=end - 30 * 60)
    old = [p for p in crew.persons.values() if p.shift == crew.current_shift and p.role in ("MAINTENANCE", "IT", "FOREMAN")]
    seen_uncoat = set()
    t = plant.env.now
    while t < end + 40 * 60:
        t += 5
        plant.env.run(until=t)
        for p in old:
            if p.act == "uncoat":
                seen_uncoat.add(p.id)
            if p.on_site and t > end + 20 * 60:
                assert not (p.act in ("idle", "handover") and not p.busy), (p.name, p.act, (t - end) / 60)
    gone = {pid: coat for pid, _, coat in crew.left_log}
    out = [p for p in old if p.id in gone]
    assert len(out) >= len(old) - 1                       # all of them went home (one may still be on a repair)
    assert not any(gone[p.id] for p in out)                # without the coat
    assert seen_uncoat                                     # taken off in the lockers
    assert crew.exit_wait and max(crew.exit_wait) < 40, sorted(crew.exit_wait)[-5:]


def test_the_gate_the_coat_and_the_coffee_machine_are_seen_in_use(model):
    plant = _plant(model)
    crew = plant.crew
    _, start, end = crew.shift_window(0.0)
    seen: Counter = Counter()
    at_gate = 0

    def watch(t0, t1, step):
        nonlocal at_gate
        t = t0
        while t < t1:
            plant.env.run(until=t)
            people = plant.floor_snapshot()["people"]
            acts = Counter(p["a"] for p in people)
            seen.update(acts.keys())
            at_gate = max(at_gate, acts["esd"])
            t += step

    watch(start + 180 * 60, start + 300 * 60, 20)               # the lunch of the morning shift
    assert {"coffee", "break"} <= set(seen), seen
    watch(end - 6 * 60, end + 6 * 60, 5)                        # the shift change
    assert {"esd", "coat"} <= set(seen), seen
    assert at_gate >= 1                                          # somebody is tested at a gate


# ---------------------------------------------------------------- what machines ask for

def _stand(model):
    plant = _plant(model)
    plant.env.run(until=2400)           # the morning shift is settled
    return plant


def test_a_breakdown_needs_a_technician_who_walks_to_the_machine(model):
    plant = _stand(model)
    crew = plant.crew
    st = next(s for s in plant.all_stations() if s.code.startswith("FA-03"))
    t0 = plant.env.now
    done = plant.env.process(crew.service(st, "SENSOR_FAULT", 600))
    plant.env.run(until=t0 + 1)
    call = st.call
    assert call is not None and call.role == "MAINTENANCE" and call.ph in ("walk", "fix")
    person = call.person
    assert person is not None and person.role == "MAINTENANCE"
    assert person.act in ("walk", "fix")
    plant.env.run(until=done)
    elapsed = plant.env.now - t0
    assert elapsed > 600 + 1.0                                      # the walk is part of the repair
    assert st.call is None and all(c.station != st.code for c in crew.calls)
    assert person.act != "fix"


def test_without_a_free_technician_the_machine_waits_its_turn(model):
    plant = _stand(model)
    crew = plant.crew
    pool = crew.pools["MAINTENANCE"]
    free = len(pool.free())
    assert free >= 2
    stations = [s for s in plant.all_stations() if s.code.startswith("SMT-0")][:free + 1]
    t0 = plant.env.now
    procs = [plant.env.process(crew.service(s, "PLC_FAULT", 900)) for s in stations]
    plant.env.run(until=t0 + 2)
    phases = Counter(s.call.ph for s in stations)
    assert phases["wait"] == 1 and phases["walk"] + phases["fix"] == free, phases
    waiting = next(s for s in stations if s.call.ph == "wait")
    assert waiting.call.person is None
    plant.env.run(until=plant.env.all_of(procs))
    assert plant.env.now - t0 > 900 * 2                              # the last one waited for a colleague


def test_network_and_printer_faults_go_to_the_it_technician(model):
    plant = _stand(model)
    crew = plant.crew
    st = next(s for s in plant.all_stations() if s.code.startswith("PACK-01"))
    t0 = plant.env.now
    done = plant.env.process(crew.service(st, "PRINTER_FAULT", 300))
    plant.env.run(until=t0 + 1)
    assert st.call.role == "IT" and st.call.person.role == "IT"
    plant.env.run(until=done)
    assert st.call is None


def test_the_foreman_comes_to_a_long_repair(model):
    plant = _stand(model)
    crew = plant.crew
    st = next(s for s in plant.all_stations() if s.code.startswith("SMT-02"))
    t0 = plant.env.now
    done = plant.env.process(crew.service(st, "PLC_FAULT", 50 * 60))
    visited = False
    while not done.triggered:
        plant.env.run(until=min(plant.env.now + 30, t0 + 80 * 60))
        if st.call is not None and st.call.visited:
            visited = True
        if plant.env.now >= t0 + 80 * 60:
            break
    assert visited, "the foreman never came to a repair that takes fifty minutes"


def test_the_andon_escalates_a_long_breakdown_to_the_foreman_and_the_manager(model):
    """Level 2 after foreman_min: the foreman walks to the machine; level 3 after manager_min: the production
    manager (morning shift) walks there too; the escalations are logged and the snapshot carries the level."""
    plant = _stand(model)                                            # the morning shift, the manager is in
    crew = plant.crew
    rule = model.crew.andon
    st = next(s for s in plant.all_stations() if s.code.startswith("FA-04"))
    t0 = plant.env.now
    done = plant.env.process(crew.service(st, "PLC_FAULT", 90 * 60))
    levels, at_machine = set(), set()
    while not done.triggered and plant.env.now < t0 + 100 * 60:
        plant.env.run(until=plant.env.now + 30)
        snap = plant.floor_snapshot()
        for c in snap["crew"]["calls"]:
            if c["st"] == st.code:
                levels.add(c["lv"])
                age = plant.env.now - t0
                expect = 3 if age >= rule.manager_min * 60 else 2 if age >= rule.foreman_min * 60 else 1
                assert c["lv"] == expect, (c, age)
        for p in snap["people"]:
            if p.get("s") == st.code and p["a"] == "supervise":
                at_machine.add(p["r"])
    assert levels == {1, 2, 3}
    assert {"FOR", "MAN"} <= at_machine
    lv = {(e["st"], e["lv"]) for e in crew.escalations}
    assert {(st.code, 2), (st.code, 3)} <= lv


def test_the_shift_meeting_brings_the_foreman_and_the_technicians_to_the_andon_board(model):
    plant = _plant(model)
    crew = plant.crew
    rule = model.crew.andon
    _, start, end = crew.shift_window(0.0)
    t_meet = end + rule.meeting_at_min * 60                          # the meeting of the next shift
    plant.env.run(until=t_meet + 60)
    best = Counter()
    while plant.env.now < t_meet + rule.meeting_min * 60:
        snap = plant.floor_snapshot()
        here = Counter(p["r"] for p in snap["people"] if p["a"] == "meeting")
        if sum(here.values()) > sum(best.values()):
            best = here
        assert snap["crew"]["meeting"] == bool(here)
        plant.env.run(until=plant.env.now + 20)
    assert best["FOR"] == 1 and best["MAI"] >= 1
    plant.env.run(until=t_meet + rule.meeting_min * 60 + 300)
    assert not any(p["a"] == "meeting" for p in plant.floor_snapshot()["people"])


def test_an_operator_walks_to_the_machine_for_a_minor_stop(model):
    plant = _stand(model)
    crew = plant.crew
    st = next(s for s in plant.all_stations()
              if (seat := crew.seat_of.get(s.code)) is not None and not seat.fixed
              and crew.walkways.duration(seat.home, f"spot:{s.code}") > 1.0)
    seat = crew.seat_of[st.code]
    t0 = plant.env.now
    done = plant.env.process(crew.attend(st, 40.0, "minor"))
    plant.env.run(until=done)
    elapsed = plant.env.now - t0
    assert elapsed >= 40.0 + crew.walkways.duration(seat.home, f"spot:{st.code}") * 0.9
    assert st.attendance is None
    assert seat.occupant is not None and seat.occupant.target is None


# ---------------------------------------------------------------- calls from the MES, the walk trail

def test_a_call_from_the_mes_brings_a_person_of_the_department_and_the_machine_goes_on(model):
    plant = _plant(model)
    plant.env.run(until=2 * 3600)
    crew = plant.crew
    code = next(c for c in plant.by_code if c.startswith("FA-01") and plant.by_code[c].type.code == "ICT")
    assert crew.request({"station": "NOPE", "role": "IT"}) is None
    assert crew.request({"station": code, "role": "PRESIDENT"}) is None
    for n, role in enumerate(("MAINTENANCE", "IT", "FOREMAN", "LOGISTICS")):
        call = crew.request({"id": n + 1, "station": code, "role": role, "minutes": 5, "meter": role == "MAINTENANCE", "by": "tester"})
        assert call is not None and call in crew.calls
        snap = plant.floor_snapshot()
        assert any(c.get("id") == n + 1 and c["kind"] == "request" for c in snap["crew"]["calls"])   # on the andon board
        t0 = plant.env.now
        seen_there = False
        while call in crew.calls and plant.env.now < t0 + 2 * 3600:
            plant.env.run(until=plant.env.now + 20)
            if call.ph == "fix" and call in crew.calls:
                seen_there = True
                person = call.person
                assert person.role == role and person.target == code
                # beside the operator's place: the technician on the left, the foreman on the right
                assert person.place == (f"hand:spot:{code}" if role == "FOREMAN" else f"tech:{code}")
                assert plant.by_code[code].call is not call                          # the machine is not stopped for it
        assert call not in crew.calls and seen_there, role
        done = crew.requests_done[-1]
        assert done["id"] == n + 1 and done["role"] == role[:3]
        assert done["stay_s"] >= 300 - 1 and done["walk_s"] > 0
        if role == "MAINTENANCE":
            assert call.person.carry in ("meter", None)                          # fetched the instruments


def test_the_live_view_gets_the_walks_just_before(model):
    """Between two snapshots a person may finish one walk and begin the next; the snapshot carries the
    walks of the last moments, so the page draws the real way instead of a straight line."""
    plant = _plant(model)
    found = 0
    for minute in range(30, 6 * 60, 5):
        plant.env.run(until=minute * 60)
        snap = plant.floor_snapshot()
        for rec in snap["people"]:
            legs = rec.get("wh", [])
            for a, b in zip(legs, legs[1:]):
                assert b["t"] >= a["t"] + a["d"] - 0.11                      # one after the other
            if legs and "w" in rec:
                last = legs[-1]
                assert math.dist(last["p"][-1], rec["w"]["p"][0]) < 0.5       # the next walk starts where the last ended
                found += 1
            elif legs:
                assert math.dist(legs[-1]["p"][-1], (rec["x"], rec["y"])) < 0.5
    assert found > 0


def test_quarantine_goes_by_robot_to_the_cage_and_back_when_released(model):
    from mes_lab.sim.trace import Decision
    plant = _plant(model)
    plant.env.run(until=3 * 3600)
    line = plant.lines["FA-02"]
    unit, cell = None, None
    for c in line.cells[1:]:
        unit = next((it for it in c.inbox.items if it.serial and it.kind == "UNIT"), None)
        if unit is not None:
            c.inbox.items.remove(unit)                                     # the station of this cell refuses it
            cell = c
            break
    assert unit is not None
    st = cell.stations[0]
    plant.quarantine(unit, st, Decision(False, "HOLD", "test"))
    assert any(it is unit for it, _ in plant.fleet.boxes[f"gap:{line.code}:{cell.index}"])   # into the NOK tote of the cell
    t0 = plant.env.now
    while unit not in plant.fleet.cage and plant.env.now < t0 + 3600:
        plant.env.run(until=plant.env.now + 10)
    assert unit in plant.fleet.cage                                        # a robot brought it to the cage
    plant.apply_commands([{"id": 1, "serial": unit.serial, "action": "RETURN"}])
    assert unit not in plant.fleet.cage
    assert any(it is unit for it, _ in plant.fleet.boxes["rpr:quar"])      # waits at the hatch for a robot
    t1 = plant.env.now
    stop = f"gap:{line.code}:{cell.index - 1}"                             # in the gap before the cell
    while plant.env.now < t1 + 3600 and not any(e["item"] == unit.serial and e["to"] == stop for e in plant.fleet.log):
        plant.env.run(until=plant.env.now + 10)
    back = [e for e in plant.fleet.log if e["item"] == unit.serial and e["to"] == stop]
    assert back, "the released unit did not come back to the cell that refused it"
    assert back[-1]["t"] - t1 > plant.model.amr.transfer_s * 2            # the trip took time
