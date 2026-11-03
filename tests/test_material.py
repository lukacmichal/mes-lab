"""SMT material: reels on the feeders and their splices, the solder paste of the printers, the floor life of
moisture-sensitive parts, on the shop floor (offline) and in the MES (scans, the paste check at the
printer, the genealogy of the components, a hold on a supplier lot, the alarm for a reel out of floor life)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_lab.mes import models as db
from mes_lab.sim.core import PlantClock, State
from mes_lab.sim.dispatch import LocalDispatcher
from mes_lab.sim.material import Reel
from mes_lab.sim.mes_client import MesClient
from mes_lab.sim.plant import PlantSim


def _plant(model, seed=4):
    return PlantSim(model, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(model, seed), seed=seed)


@pytest.fixture(scope="module")
def shift(model):
    """Ten hours of the plant, looked at every half minute for the first two."""
    plant = _plant(model)
    carried = set()
    for t in range(30, 2 * 3600, 30):
        plant.env.run(until=t)
        for p in plant.floor_snapshot()["people"]:
            if p.get("c") in ("reel", "paste"):
                carried.add(p["c"])
    plant.env.run(until=10 * 3600)
    plant.carried = carried
    return plant


def _placers(plant):
    return [st for st in plant.all_stations() if st.type.code in ("PNP_CHIP", "PNP_IC")]


def test_every_placer_has_a_reel_of_every_part_its_job_places(shift):
    plant = shift
    material = plant.material
    for st in _placers(plant):
        job = st.last_job
        if job is None or st.state is State.CHANGEOVER:          # the reels are being changed right now
            continue
        need = material.needs(st, job)
        bank = material.banks[st.code]
        assert set(bank) == set(need), (st.code, set(bank) ^ set(need))
        for part, feeder in bank.items():
            assert feeder.per_panel == need[part]
            for reel in (feeder.reel, feeder.next):
                if reel is not None:
                    assert reel.part == part and 0 <= reel.left <= reel.qty
    entry = plant.floor_snapshot()["stations"][_placers(plant)[0].code]
    assert entry["fd"] and all(0 <= f[1] <= 1 for f in entry["fd"])


def test_reels_are_spliced_in_time_and_the_operators_carry_them(shift):
    plant = shift
    stats = plant.material.stats
    assert stats["splices"] > 40 and stats["reels used up"] > 20
    assert "reel" in plant.carried                           # somebody walked with a reel to a machine
    snap = plant.floor_snapshot()
    assert set(snap["mat"]["racks"]) == {l for l in plant.lines if l.startswith("SMT")}


def test_the_printers_get_a_new_jar_when_the_old_one_is_open_too_long(shift):
    plant = shift
    material = plant.material
    paste = plant.model.smt_material.paste
    assert material.stats["paste changes"] >= 1
    for code, jar in material.jars.items():
        assert jar.opened_at - jar.out_at >= paste.thaw_h * 3600 - 1           # it thawed before it was opened
    assert paste.cabinet <= len(material.cabinet) <= paste.cabinet + 1        # (a refused jar goes back in)
    printer = next(st for st in plant.all_stations() if st.type.code == "PASTE_PRINTER")
    jar = material.jars[printer.code]
    jar.opened_at = plant.env.now - (paste.open_h + 0.5) * 3600                # open too long: no print with it
    assert plant.trace.check_in(printer, None, None).reason == "PASTE_EXPIRED"


def test_a_reel_that_runs_out_before_the_splice_stops_the_machine(model):
    plant = _plant(model, seed=7)
    plant.env.run(until=1800)
    material = plant.material
    st = next(s for s in _placers(plant) if material.banks.get(s.code))
    feeder = next(iter(material.banks[st.code].values()))
    feeder.reel.left = 0
    feeder.next = None
    seen = []
    original = st.set_state
    st.set_state = lambda state, reason=None: (seen.append(reason), original(state, reason))[1]
    t0 = plant.env.now
    done = plant.env.process(material.ready(st, st.last_job))
    plant.env.run(until=done)
    assert "REEL_EMPTY" in seen
    assert plant.env.now > t0 + 30                                            # the operator fetched and spliced a reel
    assert feeder.pieces() >= feeder.per_panel


def test_a_reel_out_of_floor_life_is_refused_and_baked(model):
    plant = _plant(model, seed=7)
    plant.env.run(until=60)
    material = plant.material
    st = next(s for s in _placers(plant) if s.type.code == "PNP_IC")
    old = Reel("RLTEST000001", "IC-US-QFN24", "L17-0001", 3000, "5a")
    old.used_s, old.out_since = 20 * 3600, plant.env.now - 6 * 3600          # 26 h of the 24 out of the dry bag
    ok = plant.env.process(material._scan(st, "IC-US-QFN24", old, "LOAD"))
    plant.env.run(until=ok)
    assert ok.value is False and material.stats["refused: MSL_EXPIRED"] == 1
    assert any(r is old for _, r, _ in material.baking)
    plant.env.run(until=plant.env.now + model.smt_material.msl.bake_h * 3600 + 60)
    assert old.used_s == 0 and old.out_since is None                          # dry again: a new floor life
    assert old in material.spares[st.line.code]                                # (dry and full again: a spare)
    wrong = Reel("RLTEST000002", "RES-0402-10K", "L01-0001", 10000, None)
    ok = plant.env.process(material._scan(st, "MOS-DPAK", wrong, "SPLICE"))
    plant.env.run(until=ok)
    assert ok.value is False


# ---------------------------------------------------------------- the MES

def _record(serial, station, step, t, job_id):
    return {"serial": serial, "station": station, "step": step, "job_id": job_id,
            "start": t.isoformat(), "end": t.isoformat(), "result": "PASS", "defects": []}


def test_the_mes_checks_every_scan_the_paste_and_knows_the_reels_of_every_board(app, client, model):
    run_id = str(uuid.uuid4())
    mes = MesClient(client=client)
    t = datetime.fromisoformat(mes.register_run(run_id, 5, None, {})["plant_start"])
    mes.next_job(run_id, "FA-01", t.isoformat())                  # final assembly needs boards: SMT orders follow
    line, job = next((code, j) for code in ("SMT-01", "SMT-02", "SMT-03", "SMT-04", "SMT-05")
                     for j in [mes.next_job(run_id, code, t.isoformat())] if j)
    assert job["material"].startswith("PCBA")
    op = model.materials[job["material"]].operation(job["operation"])
    parts = model.smt_material.parts
    chip = sorted(p for p in op.components if parts[p].machine == "PNP_CHIP")
    other = next(p for p in parts if parts[p].machine == "PNP_CHIP" and p not in op.components)

    def scan(station, kind, item, at, **kw):
        body = {"run_id": run_id, "station": station, "plant_time": at.isoformat(), "kind": kind, "item": item,
                "job_id": job["id"], **kw}
        return client.post("/api/v1/trace/material", json=body).json()

    reel = {"part": chip[0], "lot": "LTEST-01", "qty": 10000, "left": 10000}
    assert scan(f"{line}-PP1", "LOAD", "RLT1", t, slot=chip[0], **reel)["ok"]
    assert scan(f"{line}-PP1", "LOAD", "RLT2", t, slot=chip[0], **{**reel, "part": other})["reason"] == "WRONG_PART"
    assert scan(f"{line}-PP1", "LOAD", "RLT3", t, slot=other, **{**reel, "part": other})["reason"] == "NOT_IN_PROGRAM"
    wet = scan(f"{line}-PP2", "SPLICE", "RLT4", t, slot="IC-US-QFN24", part="IC-US-QFN24", lot="L17-0001", qty=3000,
               left=3000, msl="5a", floor_used_s=20 * 3600, out_since=(t - timedelta(hours=5)).isoformat(), job_id=None)
    assert wet["reason"] == "MSL_EXPIRED"
    assert scan(f"{line}-PRN", "PASTE", "PJT1", t, out_at=(t - timedelta(hours=1)).isoformat(), lot="SP-T1",
                prints=700)["reason"] == "PASTE_NOT_THAWED"
    assert scan(f"{line}-PRN", "PASTE", "PJT2", t, out_at=(t - timedelta(hours=5)).isoformat(), lot="SP-T2", prints=700)["ok"]
    assert _scalar(app, select(func.count()).select_from(db.MaterialScan)) == 6

    # the reel starts feeding (the plant reports it with the next flush); a panel goes through the placer
    client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": t.isoformat(), "material": {
        "events": [{"kind": "START", "item": "RLT1", "station": f"{line}-PP1", "slot": chip[0], "at": t.isoformat(), "left": 10000}],
        "levels": [{"id": "RLT1", "left": 9800, "eta_s": 3600}]}})
    def new_panel(at):
        return client.post("/api/v1/trace/check-in", json={"run_id": run_id, "station": f"{line}-LSR", "plant_time": at.isoformat(),
                                                          "job_id": job["id"], "records": []}).json()
    born = new_panel(t)
    panel = born["serial"]
    t1 = t + timedelta(hours=1)
    records = [_record(panel, f"{line}-LSR", "LASER_PCB", t1, job["id"])]
    assert client.post("/api/v1/trace/check-in", json={"run_id": run_id, "station": f"{line}-PRN", "serial": panel,
                                                      "plant_time": t1.isoformat(), "job_id": job["id"],
                                                      "records": records}).json()["ok"]
    records = [_record(panel, f"{line}-PRN", "PASTE_PRINTER", t1, job["id"]), _record(panel, f"{line}-SPI", "SPI", t1, job["id"]),
               _record(panel, f"{line}-PP1", "PNP_CHIP", t1, job["id"])]
    client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": t1.isoformat(), "records": records})
    trace = client.get(f"/api/v1/trace/units/{panel}").json()
    got = {(c["part"], c["lot"]) for c in trace["components"]}
    assert (chip[0], "LTEST-01") in got and ("PASTE", "SP-T2") in got
    board = born["boards"][0]
    assert {c["lot"] for c in client.get(f"/api/v1/trace/units/{board}").json()["components"]} >= {"LTEST-01"}

    lot = client.get("/api/v1/material/lot/LTEST-01").json()
    assert lot["panels"] == 1 and lot["affected"] > 1                         # the panel and its boards
    preview = client.post("/api/v1/holds/preview", json={"kind": "MATERIAL_LOT", "target": "LTEST-01", "reason": "check"}).json()
    assert preview["affected"] == lot["affected"]

    # thirteen hours later the jar is open too long: the printer may not print (the panel is not to blame)
    t13 = t + timedelta(hours=13)
    second = new_panel(t13)["serial"]
    late = client.post("/api/v1/trace/check-in", json={"run_id": run_id, "station": f"{line}-PRN", "serial": second,
                                                      "plant_time": t13.isoformat(), "job_id": job["id"],
                                                      "records": [_record(second, f"{line}-LSR", "LASER_PCB", t13, job["id"])]}).json()
    assert late["reason"] == "PASTE_EXPIRED"
    assert client.get(f"/api/v1/trace/units/{second}").json()["unit"]["status"] != "QUARANTINE"

    # a moisture-sensitive reel on a machine runs out of floor life: the MES has it taken off
    assert scan(f"{line}-PP2", "LOAD", "RLT5", t, slot="IC-US-QFN24", part="IC-US-QFN24", lot="L17-0002", qty=3000,
                left=3000, msl="5a", floor_used_s=0, out_since=t.isoformat(), job_id=None)["ok"]
    reply = client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": (t + timedelta(hours=25)).isoformat()}).json()
    assert {"serial": "RLT5", "action": "UNLOAD_REEL"} in [{k: c[k] for k in ("serial", "action")} for c in reply["commands"]]

    page = client.get("/api/v1/material").json()
    row = next(l for l in page["lines"] if l["line"] == line)
    feeders = {f["slot"]: f for p in row["placers"] for f in p["feeders"]}
    assert feeders[chip[0]]["reel"]["serial"] == "RLT1" and feeders[chip[0]]["reel"]["left"] == 9800
    assert row["printer"]["jar"]["serial"] == "PJT2"
    assert [s["result"] for s in page["scans"]].count("WRONG_PART") == 1
    assert client.get("/material").status_code == 200

    # a new run: the reels of the last one are gone from the machines
    MesClient(client=client).register_run(str(uuid.uuid4()), 6, None, {})
    assert _scalar(app, select(func.count()).select_from(db.Reel).where(db.Reel.status.in_(("FEEDER", "NEXT")))) == 0


def _scalar(app, q):
    with Session(app.state.engine) as s:
        return s.scalar(q)


# ---------------------------------------------------------------- goods-in and the supply of the lines

def test_trucks_bring_the_orders_and_the_material_handler_supplies_the_lines(model):
    sm = model.smt_material
    lean = sm.model_copy(update={"receiving": sm.receiving.model_copy(update={"start_days": 0, "safety_days": 0.3,
                                                                              "order_days": 0.5})})
    plant = _plant(model.model_copy(update={"smt_material": lean}), seed=5)       # a lean store: the trucks come soon
    plant.env.run(until=40 * 3600)
    r = plant.receiving
    assert r.stats["trucks"] >= 2 and r.stats["reels received"] > 20
    assert r.stats["milk runs"] > 40 and r.stats["reels to lines"] > 150
    assert plant.material.stats["waits for a reel"] < 40          # (the lean store runs short now and then: stockouts)
    for part, queue in r.store.items():                       # the store hands out its oldest reel first
        times = [t for _, t in queue]
        assert times == sorted(times), part
    snap = plant.floor_snapshot()
    assert snap["recv"]["store"] == sum(len(q) for q in r.store.values())
    assert any(p["r"] == "MAT" for p in snap["people"])


def test_goods_in_refuses_what_was_not_ordered_and_a_newer_reel_than_the_oldest(model):
    plant = _plant(model)
    plant.env.run(until=10)
    tr = plant.trace
    note = {"kind": "ARRIVAL", "item": "DN1", "note": "DN1", "supplier": "OHMWERK", "lines": [{"part": "RES-0402-10K", "reels": 1}]}
    assert tr.goods(note).ok
    assert tr.goods({"kind": "RECEIPT", "item": "R1", "note": "DN1", "part": "RES-0402-10K"}).ok
    assert tr.goods({"kind": "RECEIPT", "item": "R2", "note": "DN1", "part": "RES-0402-10K"}).reason == "NOT_ORDERED"
    queue = plant.receiving.store["CAP-0402-100N"]
    assert tr.goods({"kind": "ISSUE", "item": queue[1][0].id, "part": "CAP-0402-100N"}).reason == "FIFO"
    assert tr.goods({"kind": "ISSUE", "item": queue[0][0].id, "part": "CAP-0402-100N"}).ok


def test_the_mes_receives_reels_against_the_delivery_note_and_issues_the_oldest_first(app, client):
    run_id = str(uuid.uuid4())
    mes = MesClient(client=client)
    t = datetime.fromisoformat(mes.register_run(run_id, 5, None, {})["plant_start"])
    stock = [{"serial": f"RLS{i}", "part": "RES-0402-10K", "lot": "L01-0001", "qty": 10000, "msl": None,
              "supplier": "OHMWERK", "received": (t - timedelta(hours=3 - i)).isoformat()} for i in (1, 2)]
    assert mes.register_reels(run_id, stock)["reels"] == 2

    def goods(kind, item, **kw):
        body = {"run_id": run_id, "place": "GI", "plant_time": t.isoformat(), "kind": kind, "item": item, **kw}
        return client.post("/api/v1/trace/material", json=body).json()

    cap = {"supplier": "OHMWERK", "note": "DN-T1", "part": "CAP-0402-100N", "qty": 10000, "left": 10000}
    assert goods("ARRIVAL", "DN-T1", note="DN-T1", supplier="OHMWERK", lines=[{"part": "CAP-0402-100N", "reels": 2}])["ok"]
    assert goods("RECEIPT", "RLR1", lot="L04-0009", **cap)["ok"]
    assert goods("RECEIPT", "RLR2", lot="L08-0001", **{**cap, "part": "TRA-SOT23"})["reason"] == "NOT_ORDERED"
    assert client.post("/api/v1/holds", json={"kind": "MATERIAL_LOT", "target": "L04-0666",
                                             "reason": "the supplier recalls the lot"}).status_code == 200
    held = goods("RECEIPT", "RLR3", lot="L04-0666", **cap)
    assert held["reason"] == "LOT_ON_HOLD" and "recalls" in held["message"]

    assert goods("ISSUE", "RLS2", part="RES-0402-10K", line="SMT-01")["reason"] == "FIFO"     # RLS1 came first
    assert goods("ISSUE", "RLS1", part="RES-0402-10K", line="SMT-01")["ok"]
    client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": t.isoformat(), "material": {
        "events": [{"kind": "AT_LINE", "item": "RLS1", "line": "SMT-01", "at": t.isoformat()}]}})

    page = client.get("/api/v1/material").json()
    inv = {x["part"]: x for x in page["inventory"]}
    assert inv["RES-0402-10K"]["store"] == 1 and inv["RES-0402-10K"]["line"] == 1
    assert inv["CAP-0402-100N"]["store"] == 1
    assert page["deliveries"][0]["received"] == 1 and page["deliveries"][0]["refused"] == 2
    assert page["day"]["received"] == 1 and page["day"]["issued"] == 1
