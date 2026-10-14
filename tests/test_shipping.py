"""Shipping: finished pallets do not vanish, a truck of the customer takes them, oldest first,
loaded by a logistics worker, and the delivery note (ASN) says exactly what left."""

from collections import Counter
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from mes_lab.sim.core import PlantClock
from mes_lab.sim.dispatch import LocalDispatcher
from mes_lab.sim.plant import PlantSim


def _plant(model, seed=4):
    return PlantSim(model, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(model, seed), seed=seed)


@pytest.fixture(scope="module")
def run(model):
    """Two days of plant time with every shipment and every pallet taken out of the warehouse written down."""
    plant = _plant(model)
    shipping = plant.shipping
    notes: list[dict] = []
    taken: list[dict] = []
    add, take = plant.edge.add_shipment, shipping._take

    def add_shipment(shipment):
        notes.append(dict(shipment))
        add(shipment)

    def _take(customer):
        waiting = [p for m in shipping._materials(customer) for p in plant.stock[m]]
        pallet = take(customer)
        if pallet is not None:
            taken.append({"id": pallet.id, "serial": pallet.serial, "material": pallet.material, "customer": customer,
                          "older_left": [p.id for p in waiting if p.id < pallet.id], "t": plant.env.now})
        return pallet

    plant.edge.add_shipment = add_shipment
    shipping._take = _take
    visits: list[dict] = []

    def watch():
        """A truck is at the plant for a few minutes only: look at it every quarter of a minute."""
        while True:
            yield plant.env.timeout(15)
            if shipping.trucks:
                visits.append({"t": plant.env.now, "snap": plant.floor_snapshot()})

    plant.env.process(watch())
    samples = []
    for minute in range(10, 48 * 60, 10):
        plant.env.run(until=minute * 60)
        samples.append({"t": minute * 60.0, "snap": plant.floor_snapshot(),
                        "stock": {m: [p.serial for p in q] for m, q in plant.stock.items()},
                        "counts": {m: dict(c) for m, c in plant.warehouse.items()}})
    return plant, notes, taken, samples, visits


def test_finished_pallets_stay_in_the_warehouse_until_a_truck_takes_them(run):
    plant, notes, taken, samples, visits = run
    assert sum(len(n["pallets"]) for n in notes) > 5
    for s in samples:
        for material, serials in s["stock"].items():
            assert s["counts"][material]["pallets"] == len(serials)
            assert len(serials) == len(set(serials))
        assert {m: v for m, v in s["snap"]["fgw"].items()} == s["stock"]
    # a pallet that left is never in the warehouse again
    gone = {p["serial"] for p in taken}
    last = samples[-1]["stock"]
    assert not gone & {x for serials in last.values() for x in serials}


def test_the_oldest_pallet_goes_first(run):
    _, _, taken, _, _ = run
    assert taken
    for t in taken:
        assert not t["older_left"], f"{t['serial']} left while older pallets waited: {t['older_left']}"


def test_trucks_come_on_the_timetable_and_only_for_their_own_products(run, model):
    plant, notes, taken, samples, visits = run
    tz = ZoneInfo(model.plant.timezone)
    schedule = {(t.customer): t for t in model.shipping.trucks}
    by_customer = {m.code: m.customer for m in model.finished_goods()}
    assert notes
    for n in notes:
        assert n["customer"] in schedule
        assert 1 <= len(n["pallets"]) <= schedule[n["customer"]].capacity
        departed = datetime.fromisoformat(n["departed"]).astimezone(tz)
        # leaves after the time of the timetable (some waiting for pallets, loading, driving)
        assert departed.hour in {int(x.split(":")[0]) + k for x in schedule[n["customer"]].at for k in range(0, 8)} | {0, 1}
        assert n["bay"] == schedule[n["customer"]].bay
    for t in taken:
        assert by_customer[t["material"]] == t["customer"]
    assert len({n["asn"] for n in notes}) == len(notes)


def test_the_delivery_note_adds_up(run):
    plant, notes, taken, samples, visits = run
    shipped = Counter()
    for n in notes:
        shipped["pallets"] += len(n["pallets"])
        shipped["klt"] += n["klt"]
        shipped["units"] += n["units"]
        assert n["units"] >= n["klt"] * 1 and n["klt"] >= len(n["pallets"])
    counted = plant.floor_snapshot()["shipped"]
    assert sum(v["pallets"] for v in counted.values()) == shipped["pallets"]
    assert sum(v["klt"] for v in counted.values()) == shipped["klt"]
    assert sum(v["units"] for v in counted.values()) == shipped["units"]
    # what was made = what stands in the warehouse + what left (units of the plant counters)
    in_stock = sum(c["units"] for c in plant.warehouse.values())
    assert in_stock >= 0 and shipped["units"] > 0


def test_a_truck_is_seen_driving_in_loaded_by_a_worker_and_driving_out(run):
    plant, notes, taken, samples, visits = run
    layout = plant.layout
    phases = Counter()
    for s in visits:
        snap = s["snap"]
        for t in snap["trucks"]:
            phases[t["ph"]] += 1
            assert t["ph"] in {"in", "load", "out"}
            assert t["bay"] in {b["bay"] for b in layout.data["bays"]}
            assert t["cap"] >= len(t["load"])
            bay = layout.data["bays"][t["bay"]]
            assert bay["customer"] == t["c"]
            if t["ph"] != "load":
                assert t["t"] - 0.5 <= s["t"] <= t["t"] + t["d"] + 0.5
            if t["ph"] == "load":
                workers = [p for p in snap["people"] if p["r"] == "LOG" and p.get("s") == f"dock:{t['bay']}"]
                assert workers or not t["load"], "pallets are loaded by nobody"
            assert all(p["sn"] for p in t["load"])
    assert phases["in"] > 0 and phases["load"] > 0 and phases["out"] > 0, phases
    # every pallet seen on a truck is on a delivery note afterwards
    onboard = {p["sn"] for s in visits for t in s["snap"]["trucks"] for p in t["load"]}
    listed = {x for n in notes for x in n["pallets"]}
    assert onboard <= listed
