"""Phase 6: training scenarios. The faults in the plant, the containment at the truck, and the grade of the MES."""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_lab import training as catalog
from mes_lab.mes import models as db
from mes_lab.sim.mes_client import MesClient
from mes_lab.sim.runner import run_offline

T = datetime.fromisoformat("2026-01-05T08:00:00+01:00")


def _found(plant, code: str, station: str | None = None) -> int:
    return sum(n for (s, _, c), n in plant.edge.acc_defect.items() if c == code and (station is None or s == station))


def test_an_aoi_storm_floods_the_operators_until_somebody_fixes_it(model):
    plant = run_offline(model, 1.0, 3)
    aoi = "SMT-01-AOI"
    before = plant.edge.total_count[aoi][3]
    plant.start_fault({"kind": "AOI_STORM", "target": aoi, **catalog.FAULTS["AOI_STORM"]})
    plant.env.run(until=plant.env.now + 3600)
    calls = plant.edge.total_count[aoi][3] - before
    assert calls > 20                                    # an hour of a normal AOI makes a few
    assert plant.fault_on("AOI_STORM", aoi)
    plant.crew.request({"station": aoi, "role": "MAINTENANCE", "minutes": 10})
    plant.env.run(until=plant.env.now + 1800)
    assert not plant.fault_on("AOI_STORM", aoi)
    assert plant.fault_log[-1]["how"] == "fixed by maintenance"
    assert plant.live_snapshot()["faults"]["ended"][-1]["target"] == aoi


def test_an_oven_fails_with_panels_inside_and_they_carry_cold_joints(model):
    plant = run_offline(model, 1.0, 3)
    oven = plant.by_code["SMT-01-OVN"]
    plant.start_fault({"kind": "OVEN_FAILURE", "target": oven.code, **catalog.FAULTS["OVEN_FAILURE"]})
    while plant.fault_on("OVEN_FAILURE", oven.code):
        plant.env.run(until=plant.env.now + 5)
    inside = [it for it, _ in oven.inside_items]                   # the belt stands: they are still in there
    assert oven.state.value == "DOWN" and oven.reason == "HEATER_ZONE_FAULT"
    assert inside and sum("COLD_JOINT" in p.defects for it in inside for p in it.pieces()) > 0
    assert plant.fault_log[-1]["how"] == "the oven broke down"


def test_a_bad_lot_spoils_boards_and_a_hold_takes_its_reels_away(model):
    plant = run_offline(model, 1.0, 3)
    mat = plant.material
    lot = next(f.reel.lot for bank in mat.banks.values() for f in bank.values()
               if f.reel is not None and f.part in catalog.BAD_LOT_PARTS)
    plant.start_fault({"kind": "BAD_LOT", "target": lot, **catalog.FAULTS["BAD_LOT"]})
    plant.env.run(until=plant.env.now + 2 * 3600)
    spoiled = sum("COMPONENT_FAULT" in b.defects for store in plant.supermarket.values() for b in store.items)
    assert spoiled > 10                                           # latent in the boards waiting for final assembly
    mat.hold_lot(lot)
    on = [f for bank in mat.banks.values() for f in bank.values() for r in (f.reel, f.next) if r is not None and r.lot == lot]
    assert not on and lot in mat.held_lots
    assert not any(r.lot == lot for q in plant.receiving.store.values() for r, _ in q)
    plant.env.run(until=plant.env.now + 1800)                     # the lines go on with other reels
    assert not any(r.lot == lot for bank in mat.banks.values() for f in bank.values() for r in (f.reel, f.next) if r)


# ---------------------------------------------------------------- the MES side

def _pack_and_ship(client, app, n_units=24):
    """BCM-200 units through FA-01 (screwdriver at known times), into a KLT, on a pallet, onto a truck."""
    run_id = str(uuid.uuid4())
    mes = MesClient(client=client)
    stock = {"PCBA-BCM-200": [f"SC{i:02d}" for i in range(n_units)]}
    mes.register_run(run_id, 5, None, stock, plant_start=T.isoformat())
    job = mes.next_job(run_id, "FA-01", T.isoformat())
    steps = list(app.state.model.materials["BCM-200"].routing[0].steps)
    station_of = {c.type: c.stations[0].code for c in app.state.model.lines["FA-01"].cells}

    def call(path, body):
        return client.post(f"/api/v1/trace/{path}", json={"run_id": run_id, **body}).json()

    def rec(serial, station, step, t):
        return {"serial": serial, "station": station, "step": step, "job_id": job["id"], "start": t.isoformat(),
                "end": t.isoformat(), "result": "PASS", "defects": []}
    units, klt = [], None
    for i in range(n_units):
        t = T + timedelta(minutes=10 * i)
        unit = call("check-in", {"station": "FA-01-LDR", "plant_time": t.isoformat(), "consume": f"SC{i:02d}", "job_id": job["id"]})["serial"]
        packed = call("check-in", {"station": "PACK-01-PCK", "plant_time": t.isoformat(), "serial": unit, "job_id": None,
                                   "records": [rec(unit, station_of[s], s, t) for s in steps]})
        assert packed.get("ok"), packed
        units.append((unit, t))
        klt = packed["container"]
    t_end = T + timedelta(minutes=10 * n_units)
    pallet = call("check-in", {"station": "PACK-01-PAL", "plant_time": t_end.isoformat(), "serial": klt})["container"]
    return run_id, units, klt, pallet, t_end


def test_a_pallet_on_hold_does_not_leave_on_the_truck(app, client):
    run_id, units, klt, pallet, t = _pack_and_ship(client, app)
    ship = lambda: client.post("/api/v1/trace/ship", json={"run_id": run_id, "plant_time": t.isoformat(), "serial": pallet}).json()  # noqa: E731
    assert ship()["ok"]
    hold = client.post("/api/v1/holds", json={"kind": "SERIAL", "target": units[0][0], "reason": "suspect unit"}).json()
    refused = ship()
    assert refused["ok"] is False and refused["reason"] == "HOLD"
    assert client.get(f"/api/v1/trace/units/{units[1][0]}").json()["unit"]["status"] == "QUARANTINE"   # all on it
    client.post(f"/api/v1/holds/{hold['id']}/release", json={"disposition": "SCRAP"})
    assert ship()["reason"] == "SCRAPPED"


def test_a_complaint_is_graded_on_what_the_trainee_contained_and_reported(app, client):
    run_id, units, klt, pallet, t = _pack_and_ship(client, app)
    note = {"asn": "ASNTRAIN0001", "customer": "KRAFTWAGEN", "plate": "M-KW 1234", "pallets": [pallet], "klt": 1,
            "units": 24, "departed": (t + timedelta(minutes=5)).isoformat()}
    client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": (t + timedelta(minutes=10)).isoformat(), "shipments": [note]})
    view = client.get("/api/v1/training").json()
    assert set(view["scenarios"]) == {"BAD_LOT", "AOI_STORM", "OVEN_FAILURE", "COMPLAINT"} and view["running"] is None
    started = client.post("/api/v1/training/start", json={"scenario": "COMPLAINT"})
    assert started.status_code == 200, started.text
    p = started.json()["params"]
    assert p["serial"] in {u for u, _ in units} and p["asn"] == "ASNTRAIN0001" and p["station"] == "FA-01-SCR"
    assert client.post("/api/v1/training/start", json={"scenario": "BAD_LOT"}).status_code == 409    # one at a time
    with Session(app.state.engine) as s:                        # a complaint needs no fault in the plant
        assert not s.scalars(select(db.SimCommand).where(db.SimCommand.action == "FAULT_START")).all()
    done = client.post("/api/v1/training/finish", json={"asns": ["asntrain0001", "ASNNOPE"]}).json()
    r = done["result"]
    assert done["status"] == "DONE" and r["suspects"] >= 1
    assert r["counts"]["before"] == r["suspects"]                # all left before the complaint came
    assert r["report"] == {"right": ["ASNTRAIN0001"], "missed": [], "wrong": ["ASNNOPE"]}
    assert client.get("/api/v1/training").json()["past"][0]["result"]["score"] is None
    assert client.get("/training").status_code == 200


@pytest.mark.parametrize("scenario", ["AOI_STORM", "OVEN_FAILURE"])
def test_a_fault_goes_to_the_plant_and_its_end_comes_back(app, client, scenario):
    run_id = str(uuid.uuid4())
    MesClient(client=client).register_run(run_id, 5, None, {}, plant_start=T.isoformat())
    assert client.post("/api/v1/training/start", json={"scenario": scenario}).status_code == 409   # no line runs
    client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": T.isoformat(),
                                             "stations": [{"code": "SMT-02-PP1", "state": "RUNNING", "since": T.isoformat()}]})
    started = client.post("/api/v1/training/start", json={"scenario": scenario}).json()
    target = started["params"]["target"]
    assert target.startswith("SMT-02-")
    answer = client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": T.isoformat()}).json()
    command = next(c for c in answer["commands"] if c["action"] == "FAULT_START")
    assert command["data"]["kind"] == scenario and command["data"]["target"] == target
    later = T + timedelta(minutes=20)
    live = {"faults": {"active": [], "ended": [{"kind": scenario, "target": target, "since": T.isoformat(),
                                               "end": later.isoformat(), "how": "fixed by it"}]}}
    client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": later.isoformat(), "acks": [command["id"]], "live": live})
    running = client.get("/api/v1/training").json()["running"]
    assert running["fault_end_how"] == "fixed by it"
    assert client.post("/api/v1/training/finish", json={"abort": True}).json()["status"] == "ABORTED"


def test_the_holds_of_a_graded_run_go_back_to_production(app, client):
    run_id, units, klt, pallet, t = _pack_and_ship(client, app)
    note = {"asn": "ASNTRAIN0002", "customer": "KRAFTWAGEN", "plate": "M-KW 1", "pallets": [pallet], "klt": 1,
            "units": 24, "departed": (t + timedelta(minutes=5)).isoformat()}
    client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": (t + timedelta(minutes=10)).isoformat(), "shipments": [note]})
    entry = client.post("/api/v1/training/start", json={"scenario": "COMPLAINT"}).json()
    assert client.post(f"/api/v1/training/{entry['id']}/release-holds").status_code == 409        # grade first
    p = entry["params"]
    client.post("/api/v1/holds", json={"kind": "STATION_WINDOW", "target": p["station"], "window_from": p["window_from"],
                                       "window_to": p["window_to"], "reason": "complaint: torque"})
    client.post("/api/v1/training/finish", json={"asns": [p["asn"]]})
    assert client.get("/api/v1/training").json()["past"][0]["active_holds"] == 1
    assert client.post(f"/api/v1/training/{entry['id']}/release-holds").json() == {"released": 1}
    assert client.get("/api/v1/training").json()["past"][0]["active_holds"] == 0
