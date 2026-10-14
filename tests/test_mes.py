"""MES API, and the simulation driving the MES end to end."""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_lab.mes import models as db
from mes_lab.sim.mes_client import MesClient
from mes_lab.sim.runner import start_live_plant


def _start(client, seed=5, stock=None):
    run_id = str(uuid.uuid4())
    mes = MesClient(client=client)
    reg = mes.register_run(run_id, seed, None, stock or {})
    return mes, run_id, datetime.fromisoformat(reg["plant_start"])


def _count(app, model, *where):
    with Session(app.state.engine) as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def test_master_data_is_seeded(app, client):
    with Session(app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(db.Station)) == 110
        assert s.scalar(select(func.count()).select_from(db.Line)) == 13
        bcm = s.scalar(select(db.Material).where(db.Material.code == "BCM-200"))
        assert [op.code for op in bcm.operations] == ["FA"]
    assert client.get("/healthz").json()["ok"]
    assert client.get("/api/v1/clock").json()["clock"]["mes_errors"]["count"] == 0


def test_migrations_match_models(app):
    """Every model change needs a migration (scripts/new_migration.py)."""
    from alembic import command

    from mes_lab.mes.db import alembic_config

    config = alembic_config()
    with app.state.engine.begin() as conn:
        config.attributes["connection"] = conn
        command.check(config)


def test_dispatch_plans_final_assembly_and_smt_replenishment(client):
    mes, run_id, start = _start(client)
    job = mes.next_job(run_id, "FA-01", start.isoformat())
    assert job["material"] == "BCM-200" and job["operation"] == "FA"
    orders = client.get("/api/v1/orders").json()["orders"]
    assert any(o["material"] == "BCM-200" for o in orders)
    for o in orders:
        if o["material"] == "PCBA-BCM-200":
            assert [j["operation"] for j in o["jobs"]] == ["SMT_BOT", "SMT_TOP"]
    assert mes.next_job(run_id, "PACK-01", start.isoformat()) is None


def test_floor_endpoints(client):
    assert client.get("/api/v1/floor").json() == {"age": None, "floor": None}      # nothing reported yet
    layout = client.get("/api/v1/floor/layout").json()
    assert len(layout["stations"]) == 110 and "sm:SMT-01" in layout["routes"]
    assert client.get("/").status_code == 200 and client.get("/overview").status_code == 200


def test_a_person_called_on_a_page_goes_to_the_plant_once(client):
    assert client.post("/api/v1/crew/calls", json={"station": "NOPE", "role": "IT"}).status_code == 404
    assert client.post("/api/v1/crew/calls", json={"station": "FA-01-ICT", "role": "BOSS"}).status_code == 422
    r = client.post("/api/v1/crew/calls", json={"station": "FA-01-ICT", "role": "MAINTENANCE", "minutes": 5, "meter": True})
    assert r.status_code == 200 and r.json()["queued"]
    assert client.get("/api/v1/crew/calls").json()["pending"][0]["station"] == "FA-01-ICT"
    mes, run_id, start = _start(client)
    payload = {"run_id": run_id, "plant_time": start.isoformat()}
    first = client.post("/api/v1/edge/ingest", json=payload).json()
    assert [c["role"] for c in first["calls"]] == ["MAINTENANCE"] and first["calls"][0]["meter"] is True
    assert client.post("/api/v1/edge/ingest", json=payload).json()["calls"] == []        # only once
    assert client.get("/api/v1/crew/calls").json()["sent"][0]["id"] == r.json()["id"]


def test_a_tuning_scenario_goes_into_the_live_plant(app, client, model):
    """Use in the live plant: the MES keeps the wish, the plant learns it with the answer to its report and
    builds anew; the new run has fewer SMT lines, their work moves to the others, the planner gives SMT
    work only to the lines the plant has, the pages show the plan and say which lines are out of operation."""
    mes, run_id, start = _start(client)
    payload = {"run_id": run_id, "plant_time": start.isoformat()}
    before = client.post("/api/v1/edge/ingest", json=payload).json()["scenario"]
    mes.next_job(run_id, "FA-01", start.isoformat())                    # some SMT work is planned on all five lines
    state = client.post("/api/v1/plant/scenario", json={"knobs": {"smt_lines": 3, "robots": 6}}).json()
    assert state["pending"] and state["wanted"]["knobs"]["smt_lines"] == 3
    assert client.post("/api/v1/edge/ingest", json=payload).json()["scenario"] == before + 1     # build anew
    assert client.get("/api/v1/sim/scenario").json()["knobs"]["robots"] == 6

    from mes_lab import tuning
    built = tuning.model_for(state["wanted"]["knobs"])
    assert sorted(c for c in built.lines if c.startswith("SMT")) == ["SMT-01", "SMT-02", "SMT-03"]
    run2 = str(uuid.uuid4())
    reg = mes.register_run(run2, 5, None, {}, lines=sorted(built.lines), knobs=state["wanted"]["knobs"])
    t2 = reg["plant_start"]
    state = client.get("/api/v1/plant/scenario").json()
    assert not state["pending"] and len(state["live"]["lines"]) == 11
    with Session(app.state.engine) as s:
        busy = s.execute(select(db.Line.code).join(db.Job, db.Job.line_id == db.Line.id)
                         .where(db.Job.status == "QUEUED")).scalars().all()
    assert busy and not {"SMT-04", "SMT-05"} & set(busy)                # their work moved to the lines there are
    for line in ("FA-01", "FA-02", "FA-03"):
        mes.next_job(run2, line, t2)
    orders = client.get("/api/v1/orders?status=open").json()["orders"]
    assert not any(j["line"] in ("SMT-04", "SMT-05") for o in orders for j in o["jobs"])
    overview = client.get("/api/v1/plant/overview").json()
    status = {l["code"]: l["status"] for a in overview["areas"] for l in a["lines"]}
    assert status["SMT-04"] == status["SMT-05"] == "OFF" and status["SMT-01"] != "OFF"
    assert overview["clock"]["scenario"] == {"smt_lines": 3, "robots": 6}       # what differs, for the banner
    layout = client.get("/api/v1/floor/layout").json()
    assert layout["run"] == run2 and "SMT-04" not in layout["lines"] and "SMT-03" in layout["lines"]
    # and back: the plant as configured
    assert client.post("/api/v1/plant/scenario", json={"knobs": None}).json()["wanted"]["knobs"] is None


def test_the_plant_builds_anew_when_the_mes_asks_for_another_plant(model):
    from datetime import UTC

    from mes_lab.sim.core import PlantClock
    from mes_lab.sim.dispatch import LocalDispatcher
    from mes_lab.sim.plant import PlantSim

    plant = PlantSim(model, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(model, 1), seed=1)
    plant.scenario_version = 3
    plant.apply_control({"scenario": 3})
    assert not plant.rebuild
    plant.apply_control({"scenario": 4})
    assert plant.rebuild


def test_the_hour_by_hour_plan_follows_the_jobs_of_a_line(app, client, model):
    """The plan grows at the ideal rate of the job's constraint times the plan efficiency while a job owns
    the line; nothing is planned without a job; the boards and the line page carry it."""
    from mes_lab.mes.hourly import HourlyBoards, ideal_rates

    rates = ideal_rates(model)
    bcm = rates[("FA-01", "BCM-200")] * 3600
    assert 90 < bcm < 130                       # the slowest cell of FA-01 for BCM-200, a double cell counts twice
    assert rates[("SMT-01", "PCBA-BCM-200")] < rates[("SMT-01", "PCBA-LED-DRV-40")]     # two sides vs one
    mes, run_id, start = _start(client)
    job = mes.next_job(run_id, "FA-01", start.isoformat())
    boards = HourlyBoards(model)
    with Session(app.state.engine) as s:
        line = s.scalar(select(db.Line).where(db.Line.code == "FA-01"))
        row = s.get(db.Job, job["id"])
        hour = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        row.started_at = hour + timedelta(minutes=30)        # the job owns the second half of the hour
        s.commit()
        plan = boards.plan(s, line, [hour, hour + timedelta(hours=1)], hour + timedelta(hours=2))
    assert plan[hour] == pytest.approx(bcm * 0.5 * model.plant.plan_efficiency, rel=0.01)
    assert plan[hour + timedelta(hours=1)] == pytest.approx(bcm * model.plant.plan_efficiency, rel=0.01)
    data = client.get("/api/v1/boards").json()
    assert "FA-01" in data["lines"] and "PACK-01" not in data["lines"]
    assert {"hours", "shift", "last_shift"} <= set(data["lines"]["FA-01"])
    line_view = client.get("/api/v1/lines/FA-01").json()
    assert all("plan" in h for h in line_view["hourly"])


def test_the_andon_page_lists_calls_and_escalated_breakdowns(client):
    data = client.get("/api/v1/andon").json()
    assert {"open", "escalations", "history", "counts", "rule"} <= set(data)
    assert data["rule"]["foreman_min"] == 15
    assert client.get("/andon").status_code == 200


def test_ingest_requires_known_run(client):
    r = client.post("/api/v1/edge/ingest", json={"run_id": "nope-nope", "plant_time": "2026-01-01T00:00:00Z"})
    assert r.status_code == 404


# ---------------------------------------------------------------- check-in rules

def _record(serial, station, step, t, result="PASS", defects=(), job_id=None):
    return {"serial": serial, "station": station, "step": step, "job_id": job_id,
            "start": t.isoformat(), "end": t.isoformat(), "result": result,
            "defects": [{"serial": serial, "code": c} for c in defects]}


def _check_in(client, run_id, station, t, serial=None, consume=None, job_id=None, records=()):
    return client.post("/api/v1/trace/check-in", json={
        "run_id": run_id, "station": station, "plant_time": t.isoformat(), "serial": serial,
        "consume": consume, "job_id": job_id, "records": list(records)}).json()


def test_route_holds_and_repair_are_enforced(app, client):
    mes, run_id, t = _start(client, stock={"PCBA-BCM-200": ["SB1", "SB2"]})
    job = mes.next_job(run_id, "FA-01", t.isoformat())

    # a board becomes a product unit at the loader, with a serial from the MES
    start = _check_in(client, run_id, "FA-01-LDR", t, consume="SB1", job_id=job["id"])
    assert start["ok"] and start["serial"].startswith("BCM")
    unit = start["serial"]
    assert not _check_in(client, run_id, "FA-01-LDR", t, consume="SB1", job_id=job["id"])["ok"]  # used

    # skipping press-fit, coating and its inspection is refused
    skip = _check_in(client, run_id, "FA-01-ICT", t, serial=unit, job_id=job["id"],
                     records=[_record(unit, "FA-01-LDR", "FA_LOADER", t, job_id=job["id"])])
    assert skip == {"ok": False, "reason": "ROUTE", "message": skip["message"]}
    assert "PRESS_FIT" in skip["message"]
    trace = client.get(f"/api/v1/trace/units/{unit}").json()
    assert trace["unit"]["status"] == "QUARANTINE"
    assert [n["serial"] for n in trace["up"]["children"]] == ["SB1"]

    # quality releases it; the plant gets the command with the next ingest
    assert client.post(f"/api/v1/quarantine/{unit}/disposition", json={"action": "RETURN"}).status_code == 200
    reply = client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": t.isoformat()}).json()
    assert reply["commands"][0]["serial"] == unit and reply["commands"][0]["action"] == "RETURN"
    reply = client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": t.isoformat(),
                                                    "acks": [reply["commands"][0]["id"]]}).json()
    assert reply["commands"] == []

    # done properly: all steps before ICT, then ICT fails -> open defect blocks the next check-in
    steps = [("FA-01-PRS", "PRESS_FIT"), ("FA-01-CTR", "COATER"), ("FA-01-UVC", "UV_CURE"),
             ("FA-01-CAO", "COATING_AOI")]
    recs = [_record(unit, s, st, t, job_id=job["id"]) for s, st in steps]
    assert _check_in(client, run_id, "FA-01-ICT", t, serial=unit, job_id=job["id"], records=recs)["ok"]
    fail = [_record(unit, "FA-01-ICT", "ICT", t, "FAIL", ["SOLDER_BRIDGE", "COMPONENT_FAULT"], job["id"])]
    blocked = _check_in(client, run_id, "FA-01-EOL1", t, serial=unit, job_id=job["id"], records=fail)
    assert blocked["reason"] == "NOT_REPAIRED"

    # repair: the MES closes the defect and sends the unit back to the station that found it
    client.post(f"/api/v1/quarantine/{unit}/disposition", json={"action": "RETURN"})
    rep = client.post("/api/v1/trace/repair", json={"run_id": run_id, "station": "RPR-01-RPB1",
                                                    "plant_time": t.isoformat(), "serial": unit,
                                                    "success": True}).json()
    assert rep == {"ok": True, "disposition": "RETURN", "return_to": "ICT"}
    history = client.get(f"/api/v1/trace/units/{unit}").json()["history"]
    assert history[-1]["action"] == "COMPONENT_FAULT, SOLDER_BRIDGE"  # longer than the old 20 characters
    assert _check_in(client, run_id, "FA-01-ICT", t, serial=unit, job_id=job["id"])["ok"]

    # a hold on the board stops the unit made from it
    hold = client.post("/api/v1/holds", json={"kind": "SERIAL", "target": "SB1",
                                             "reason": "suspect solder paste lot"}).json()
    assert hold["affected"] == 2  # the board and the unit built from it
    held = _check_in(client, run_id, "FA-01-ICT", t, serial=unit, job_id=job["id"])
    assert held["reason"] == "HOLD"
    assert client.post(f"/api/v1/quarantine/{unit}/disposition", json={"action": "RETURN"}).status_code == 409
    released = client.post(f"/api/v1/holds/{hold['id']}/release", json={"disposition": "SCRAP"}).json()
    assert released["disposed"] == 1
    assert client.get(f"/api/v1/trace/units/{unit}").json()["unit"]["status"] == "SCRAPPED"


def test_packing_issues_boxes_and_sscc(app, client):
    mes, run_id, t = _start(client, stock={"PCBA-BMS-12S": [f"SX{i:02d}" for i in range(13)]})
    job = mes.next_job(run_id, "FA-04", t.isoformat())
    steps = list(app.state.model.materials["BMS-12S"].routing[0].steps)
    line = app.state.model.lines["FA-04"]
    station_of = {c.type: c.stations[0].code for c in line.cells}
    boxes = set()
    for i in range(13):
        unit = _check_in(client, run_id, "FA-04-LDR", t, consume=f"SX{i:02d}", job_id=job["id"])["serial"]
        recs = [_record(unit, station_of[s], s, t, job_id=job["id"]) for s in steps]
        packed = _check_in(client, run_id, "PACK-02-PCK", t, serial=unit, records=recs)
        assert packed["ok"], packed
        boxes.add(packed["container"])
        if i == 11:
            assert packed["full"]  # BMS-12S: 12 per KLT
    assert len(boxes) == 2
    first = sorted(boxes)[0]
    pallet = _check_in(client, run_id, "PACK-02-PAL", t, serial=first)
    assert pallet["ok"] and len(pallet["container"]) == 18  # SSCC
    view = client.get(f"/api/v1/trace/units/{first}").json()
    assert view["unit"]["qty"] == 12 and view["down"]["children"][0]["kind"] == "PALLET"

    # a truck takes the pallet away: the pallet, its boxes and the units in them are shipped
    sscc = pallet["container"]
    note = {"asn": "ASNTEST0001", "customer": "VOLTARO", "plate": "1AB CD1234", "pallets": [sscc], "klt": 1, "units": 12,
            "departed": (t + timedelta(minutes=5)).isoformat()}
    payload = {"run_id": run_id, "plant_time": t.isoformat(), "shipments": [note]}
    assert client.post("/api/v1/edge/ingest", json=payload).status_code == 200
    assert client.post("/api/v1/edge/ingest", json=payload).status_code == 200      # sent again: nothing doubles
    shipped = client.get(f"/api/v1/trace/units/{sscc}").json()
    assert shipped["unit"]["status"] == "SHIPPED" and "ASNTEST0001" in shipped["unit"]["location"]
    box = shipped["up"]["children"][0]
    assert box["kind"] == "KLT" and box["status"] == "SHIPPED"
    one = client.get(f"/api/v1/trace/units/{box['serial']}").json()["up"]["children"][0]["serial"]
    assert client.get(f"/api/v1/trace/units/{one}").json()["unit"]["status"] == "SHIPPED"
    listing = client.get("/api/v1/shipments").json()["shipments"]
    assert [x["asn"] for x in listing] == ["ASNTEST0001"] and listing[0]["pallets"] == 1
    assert client.get("/api/v1/shipments/ASNTEST0001").json()["pallet_serials"] == [sscc]
    assert client.get("/api/v1/shipments/NOPE").status_code == 404


# ---------------------------------------------------------------- the whole plant

def test_simulation_drives_the_mes(app, client, model):
    mes = MesClient(client=client)
    plant = start_live_plant(model, mes, seed=7, speed=None, sim_flush_s=180)
    plant.env.run(until=4 * 3600)
    plant.edge.flush()

    with Session(app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(db.StationStatus)) == 110
        assert s.scalar(select(func.count()).select_from(db.Job).where(db.Job.status == "COMPLETED")) > 0
        good_fin = s.scalar(select(func.sum(db.ProductionCount.good))
                            .join(db.Station, db.Station.id == db.ProductionCount.station_id)
                            .where(db.Station.code == "FA-01-FIN"))
        assert good_fin == plant.edge.total_count["FA-01-FIN"][1]
        seconds = s.scalar(select(func.sum(db.StationStateTime.seconds))
                           .join(db.Station, db.Station.id == db.StationStateTime.station_id)
                           .where(db.Station.code == "SMT-01-PP1"))
        assert abs(seconds - 4 * 3600) < 1
        assert s.scalar(select(func.count()).select_from(db.DowntimeEvent)) > 0
        kinds = dict(s.execute(select(db.Unit.kind, func.count()).group_by(db.Unit.kind)).all())
        assert kinds.keys() >= {"PANEL", "BOARD", "UNIT", "KLT"}
        assert s.scalar(select(func.count()).select_from(db.OperationRecord)) > 10_000
        # a normal day: the MES never had to stop anything
        assert s.scalar(select(func.count()).select_from(db.Unit)
                        .where(db.Unit.quarantine_reason.is_not(None))) == 0
        packed = s.scalar(select(db.Unit.serial).where(db.Unit.kind == "UNIT", db.Unit.status == "PACKED")
                          .order_by(db.Unit.id.desc()).limit(1))
        repaired = s.scalar(select(func.count()).select_from(db.Defect).where(db.Defect.status == "REPAIRED"))
        assert repaired > 0
    assert not plant.quarantined

    # the live picture of the floor travels with the flush, in memory only
    live = client.get("/api/v1/floor").json()
    assert live["age"] is not None and live["age"] < 30
    assert live["floor"]["run"] == plant.run_id and len(live["floor"]["stations"]) == 110
    assert live["floor"]["t"] == pytest.approx(plant.env.now, abs=1)

    view = client.get(f"/api/v1/trace/units/{packed}").json()
    assert view["unit"]["status"] == "PACKED"
    assert any(r["step"] == "EOL_TESTER" for r in view["history"])
    for path in ("/", "/lines/FA-03", "/orders", "/trace", "/quality", "/holds", "/guide"):
        assert client.get(path).status_code == 200, path
    assert client.get("/api/v1/quality").json()["pareto"]

    # containment: hold everything that passed FA-01's press-fit in the last hour
    now = datetime.fromisoformat(client.get("/api/v1/plant/overview").json()["clock"]["plant_time"])
    preview = client.post("/api/v1/holds/preview", json={
        "kind": "STATION_WINDOW", "target": "FA-01-PRS", "window_from": (now - timedelta(hours=1)).isoformat(),
        "window_to": now.isoformat(), "reason": "press force sensor drift"}).json()
    hold = client.post("/api/v1/holds", json={
        "kind": "STATION_WINDOW", "target": "FA-01-PRS", "window_from": (now - timedelta(hours=1)).isoformat(),
        "window_to": now.isoformat(), "reason": "press force sensor drift"}).json()
    assert hold["affected"] == preview["affected"] > 0
    plant.env.run(until=plant.env.now + 3600)
    plant.edge.flush()
    assert plant.quarantined, "held units must be stopped at their next check-in"
    holds = client.get("/api/v1/holds").json()["holds"]
    assert holds[0]["in_quarantine"] > 0
    client.post(f"/api/v1/holds/{hold['id']}/release", json={"disposition": "SCRAP"})
    plant.edge.flush()                      # delivers the scrap commands
    plant.env.run(until=plant.env.now + 600)
    plant.edge.flush()                      # acknowledges them
    assert not plant.quarantined
    assert _count(app, db.SimCommand, db.SimCommand.acked_at.is_(None)) == 0

    # a restart of the simulation: unfinished orders back in the queue, WIP is lost
    mes2, run2, start2 = _start(client, seed=8)
    with Session(app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(db.Job).where(db.Job.status == "ACTIVE")) == 0
        assert s.scalar(select(func.count()).select_from(db.Unit).where(db.Unit.status == "IN_PROCESS")) == 0
