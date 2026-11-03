"""SPC and the master sample check of the testers: the plant's processes drift with use, the MES watches
them on control charts and reacts the way a control plan says; a tester tests production only after its
master sample check of the shift."""

import math
import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_lab.mes import models as db
from mes_lab.mes.spc import SAMPLE_MIN, SUBGROUP, violations
from mes_lab.mes.trace import Refusal
from mes_lab.sim.core import PlantClock, State
from mes_lab.sim.dispatch import LocalDispatcher
from mes_lab.sim.mes_client import MesClient
from mes_lab.sim.plant import PlantSim
from mes_lab.sim.trace import LocalTrace


# ---------------------------------------------------------------- the rules

def test_the_rules_of_the_chart():
    s = 1 / math.sqrt(SUBGROUP)                      # sigma of an average of five, sd 1
    assert violations([0.1, -0.2, 0.3, 0.0, -0.1], 0.0, 1.0) == []
    assert ("R1", 2, 2) in violations([0.0, 0.1, 3.5 * s], 0.0, 1.0)
    assert ("R2", 1, 3) in violations([0.0, 2.3 * s, 0.5 * s, 2.4 * s], 0.0, 1.0)
    assert not [v for v in violations([0.0, 2.3 * s, -2.4 * s], 0.0, 1.0) if v[0] == "R2"]       # opposite sides
    trend = [i * 0.2 * s for i in range(7)]
    assert ("R5", 0, 6) in violations(trend, 0.0, 1.0)
    rng = random.Random(3)
    calm = [rng.gauss(0, s) for _ in range(400)]
    assert len(violations(calm, 0.0, 1.0)) < 6                                          # few false alarms


# ---------------------------------------------------------------- the plant

def _plant(model, trace=None, seed=4):
    return PlantSim(model, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(model, seed), seed=seed,
                    **({"trace": trace} if trace else {}))


def test_a_process_drifts_with_use(model):
    plant = _plant(model)
    st = plant.by_code["FA-01-SCR"]
    spec = model.station_types["SCREWDRIVER"].measurements["torque_nm"]
    assert spec.spc and spec.drift_per_h > 0
    fresh = sum(st.measure(False)["torque_nm"] for _ in range(300)) / 300
    st.drift_s = 40 * 3600
    worn = sum(st.measure(False)["torque_nm"] for _ in range(300)) / 300
    assert worn - fresh == pytest.approx(spec.drift_per_h * 40, abs=4 * spec.sd / math.sqrt(300))
    before = st.drift_s
    plant.env.run(until=3600)
    assert before < st.drift_s <= before + 3600      # it only grows while the machine works


def test_an_adjust_command_stops_the_station_for_a_technician_and_is_confirmed(model):
    plant = _plant(model)
    st = plant.by_code["FA-02-SCR"]
    plant.env.run(until=1800)
    st.drift_s = 20 * 3600
    plant.apply_control({"commands": [{"id": 77, "serial": "FA-02-SCR", "action": "ADJUST"}]})
    plant.apply_control({"commands": [{"id": 77, "serial": "FA-02-SCR", "action": "ADJUST"}]})     # repeated: once
    assert st.adjust == 77
    states = set()
    t = plant.env.now
    while 77 not in plant.acks and plant.env.now < t + 3 * 3600:
        plant.env.run(until=plant.env.now + 20)
        states.add((st.state, st.reason))
    assert 77 in plant.acks and plant.acks.count(77) == 1
    assert (State.MAINTENANCE, "SPC_ADJUST") in states
    assert st.adjust is None and st.drift_s < 3600


class RecordingTrace(LocalTrace):
    def __init__(self, plant):
        super().__init__(plant)
        self.checks: list[tuple[str, str, float, str | None]] = []
        self.first: dict[tuple[str, float], float] = {}

    def verification(self, station, result, start, person=None):
        self.checks.append((station.code, result, self.plant.env.now, person))

    def check_in(self, station, item, job, consume=None):
        if station.type.verification is not None:
            _, shift_start, _ = self.plant.crew.shift_window(self.plant.env.now)
            self.first.setdefault((station.code, shift_start), self.plant.env.now)
        return super().check_in(station, item, job, consume)


def test_a_tester_checks_its_master_sample_before_the_first_unit_of_every_shift(model):
    plant = _plant(model, trace=lambda p: RecordingTrace(p))
    plant.env.run(until=20 * 3600)
    trace = plant.trace
    testers = [s.code for s in plant.all_stations() if s.type.verification is not None]
    assert testers and trace.checks
    passed = {}
    for code, result, t, person in trace.checks:
        if result == "PASS":
            _, start, _ = plant.crew.shift_window(t)
            passed.setdefault((code, start), t)
            assert person, "an operator does the check"
    for (code, start), first_unit in trace.first.items():
        assert (code, start) in passed, f"{code} tested units in a shift without a check"
        assert passed[(code, start)] <= first_unit
    assert len({start for _, start in passed}) >= 2                              # every shift again


# ---------------------------------------------------------------- the MES

def _units_with_torque(app, run_id, station_code, values, start):
    """Units with a screwdriver record each, one per minute, as the plant would report them."""
    with Session(app.state.engine) as s:
        station = s.scalar(select(db.Station).where(db.Station.code == station_code))
        mat = s.scalar(select(db.Material).where(db.Material.code == "BCM-200"))
        for i, v in enumerate(values):
            t = start + timedelta(minutes=i)
            unit = db.Unit(serial=f"T{uuid.uuid4().hex[:12]}", kind="UNIT", material_id=mat.id, run_id=run_id,
                           status="IN_PROCESS", operation="FA", completed=[], created_at=t, updated_at=t)
            s.add(unit)
            s.flush()
            s.add(db.OperationRecord(unit_id=unit.id, station_id=station.id, run_id=run_id, operation="FA",
                                     step="SCREWDRIVER", started_at=t, ended_at=t, result="PASS",
                                     measurements={"torque_nm": v}))
        s.commit()


def test_a_drifting_process_raises_an_alarm_the_station_is_adjusted_and_the_hold_is_released_by_data(app, client):
    from mes_lab.mes.spc import Spc
    from mes_lab.mes.trace import Trace

    mes = MesClient(client=client)
    run_id = str(uuid.uuid4())
    reg = mes.register_run(run_id, 1, None, {})
    start = datetime.fromisoformat(reg["plant_start"]).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    model = app.state.model
    spec = model.station_types["SCREWDRIVER"].measurements["torque_nm"]
    s5 = spec.sd / math.sqrt(SUBGROUP)
    # nine hours: in control, then a jump of 3.5 sigma of the average in the last hour (R1)
    values = []
    for h in range(9):
        shift = 3.5 * s5 if h == 8 else 0.0
        values += [spec.mean + shift + (0.2 * spec.sd if i % 2 else -0.2 * spec.sd) for i in range(SAMPLE_MIN)]
    _units_with_torque(app, run_id, "FA-01-SCR", values, start)
    now = start + timedelta(hours=9)
    trace = Trace(model)
    spc = Spc(model, trace)
    with Session(app.state.engine) as s:
        raised = spc.evaluate(s, now)
        assert [(a.station_code, a.characteristic, a.rule) for a in raised] == [("FA-01-SCR", "torque_nm", "R1")]
        alarm_id, hold_id = raised[0].id, raised[0].hold_id
        assert hold_id is not None                                   # a point beyond 3 sigma: contain
        command = s.scalar(select(db.SimCommand).where(db.SimCommand.action == "ADJUST"))
        assert command.serial == "FA-01-SCR"
        assert spc.evaluate(s, now) == []                            # one open alarm per station
    # the plant confirms the adjustment with its next report
    payload = {"run_id": run_id, "plant_time": (now + timedelta(minutes=20)).isoformat(), "acks": [command.id]}
    assert client.post("/api/v1/edge/ingest", json=payload).status_code == 200
    with Session(app.state.engine) as s:
        alarm = s.get(db.SpcAlarm, alarm_id)
        assert alarm.status == "CLOSED" and (alarm.note or "").startswith("RELEASED")
        assert s.get(db.Hold, hold_id).status == "RELEASED"          # all inside the specification
    page = client.get("/api/v1/spc").json()
    assert any(c["station"] == "FA-01-SCR" and c["name"] == "torque_nm" for c in page["chars"])
    chart = client.get("/api/v1/spc/chart", params={"station": "FA-01-SCR", "name": "torque_nm"}).json()
    assert chart["points"] and chart["x"]["ucl"] == pytest.approx(spec.mean + 3 * s5)
    assert client.get("/spc").status_code == 200


def test_a_trend_inside_the_specification_is_adjusted_without_a_hold(app, client):
    from mes_lab.mes.spc import Spc
    from mes_lab.mes.trace import Trace

    mes = MesClient(client=client)
    run_id = str(uuid.uuid4())
    reg = mes.register_run(run_id, 1, None, {})
    start = datetime.fromisoformat(reg["plant_start"]).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    model = app.state.model
    spec = model.station_types["SCREWDRIVER"].measurements["torque_nm"]
    values = []
    for h in range(8):                                               # rising a little every hour: a trend
        values += [spec.mean + h * 0.1 * spec.sd for _ in range(SAMPLE_MIN)]
    _units_with_torque(app, run_id, "FA-02-SCR", values, start)
    with Session(app.state.engine) as s:
        raised = Spc(model, Trace(model)).evaluate(s, start + timedelta(hours=8))
        assert [(a.station_code, a.rule, a.hold_id) for a in raised] == [("FA-02-SCR", "R5", None)]


def test_the_mes_lets_a_tester_work_only_after_its_master_sample_check(app, client):
    from mes_lab.mes.trace import Trace

    model = app.state.model
    mes = MesClient(client=client)
    run_id = str(uuid.uuid4())
    reg = mes.register_run(run_id, 1, None, {})
    when = datetime.fromisoformat(reg["plant_start"])
    trace = Trace(model)
    with Session(app.state.engine) as s:
        station_id, step = trace.station(s, "FA-01-EOL1")
        stype = model.station_types[step]
        with pytest.raises(Refusal) as refused:
            trace.station_ready(s, stype, station_id, when)
        assert refused.value.reason == "VERIFICATION_DUE" and refused.value.unit is None     # no quarantine
    r = client.post("/api/v1/trace/verification", json={"run_id": run_id, "station": "FA-01-EOL1",
                                                        "plant_time": when.isoformat(), "result": "PASS", "person": "Jana Nováková"})
    assert r.status_code == 200
    with Session(app.state.engine) as s:
        trace.station_ready(s, stype, station_id, when)                                                # now it may
        assert not trace.verified(s, station_id, when + timedelta(hours=8))                            # the next shift: again
    data = client.get("/api/v1/verifications").json()
    row = next(t for t in data["testers"] if t["station"] == "FA-01-EOL1")
    assert row["last"]["result"] == "PASS" and row["last"]["person"] == "Jana Nováková"
