"""Pause and "go to a time": the MES keeps the wish, the plant carries it out in its live loop."""

from datetime import UTC, datetime, timedelta

import pytest

from mes_lab.mes.floor import SimControl
from mes_lab.sim.core import PlantClock
from mes_lab.sim.dispatch import LocalDispatcher
from mes_lab.sim.plant import PlantSim
from mes_lab.sim.runner import live_step

T0 = datetime(2026, 1, 5, 6, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_a_pause_stops_the_plant_and_ends_by_itself():
    wall = FakeClock()
    ctl = SimControl(clock=wall)
    assert ctl.for_plant() == {"paused": False, "goto": None}
    ctl.pause(True, "me")
    assert ctl.for_plant()["paused"] and ctl.state()["pause_left_s"] == SimControl.MAX_PAUSE_S
    wall.t = SimControl.MAX_PAUSE_S + 1
    assert ctl.for_plant()["paused"] is False                       # never stands still for long by mistake


def test_a_jump_goes_forward_only_and_stops_there_when_asked():
    ctl = SimControl()
    with pytest.raises(ValueError):
        ctl.jump(T0 - timedelta(hours=1), "pause", T0, "me")         # the plant cannot go back in time
    with pytest.raises(ValueError):
        ctl.jump(T0 + timedelta(days=8), "pause", T0, "me")
    ctl.jump(T0 + timedelta(hours=2), "pause", T0, "me")
    assert ctl.for_plant()["goto"].startswith("2026-01-05T08:00")
    ctl.reached(T0 + timedelta(hours=1))
    assert ctl.goto is not None
    ctl.reached(T0 + timedelta(hours=2))
    assert ctl.goto is None and ctl.paused                           # there, and paused as asked
    ctl.jump(T0 + timedelta(hours=3), "run", T0 + timedelta(hours=2), "me")
    assert not ctl.paused                                            # a jump starts the clock again
    ctl.reached(T0 + timedelta(hours=3))
    assert not ctl.paused and ctl.goto is None


class Sink:
    """Stands in for the MES: answers every report with the control state."""

    def __init__(self, ctl: SimControl, clock: PlantClock):
        self.ctl = ctl
        self.clock = clock
        self.reports = 0

    def send(self, payload):
        self.reports += 1
        self.ctl.reached(datetime.fromisoformat(payload["plant_time"]))
        return self.ctl.for_plant()


def test_the_live_loop_pauses_and_jumps(model, monkeypatch):
    clock = PlantClock(T0)
    ctl = SimControl()
    sink = Sink(ctl, clock)
    plant = PlantSim(model, clock, LocalDispatcher(model, 3), seed=3, sink=sink, speed=600.0)
    monkeypatch.setattr("mes_lab.sim.runner.time.sleep", lambda s: None)
    live_step(plant)                                                 # a wall second = 600 s of plant time
    assert plant.env.now == pytest.approx(600, abs=1)
    ctl.pause(True, "me")
    live_step(plant)                                                 # the answer to this report pauses it
    t = plant.env.now
    for _ in range(3):
        assert live_step(plant) == "paused"
    assert plant.env.now == t                                        # the clock stands, the reports go on
    assert plant.floor_snapshot()["ctl"]["paused"]
    target = clock.at(t) + timedelta(minutes=30)
    ctl.jump(target, "pause", clock.at(t), "me")
    steps = 0
    while plant.env.now < t + 1800 - 1 and steps < 60:
        live_step(plant)
        steps += 1
    assert plant.env.now == pytest.approx(t + 1800, abs=1)          # exactly there ...
    live_step(plant)
    assert plant.paused and live_step(plant) == "paused"             # ... and paused, as asked


def test_at_midnight_the_plant_goes_on_with_the_real_day():
    """The plant's calendar follows the real one: past its midnight, a plant off the real time (ahead after a
    jump or a faster speed, behind after a stop) starts the real day at 00:00; close to it, it keeps its day;
    while it runs ahead to a time it gets there first."""
    from zoneinfo import ZoneInfo

    from mes_lab.sim.runner import new_day

    tz = ZoneInfo("Europe/Bratislava")

    class Plant:
        def __init__(self, start):
            self.clock = PlantClock(start)
            self.env = type("Env", (), {"now": 0.0})()
            self.goto_s = None

    real = datetime(2026, 10, 19, 21, 30, tzinfo=tz)                 # the real clock: 19 Oct, 21:30
    ahead = Plant(datetime(2027, 3, 30, 23, 50, tzinfo=tz))           # months ahead
    assert new_day(ahead, tz, real) is None                           # the first look only notes the day
    ahead.env.now = 5 * 60
    assert new_day(ahead, tz, real) is None                           # 23:55: still the same day
    ahead.env.now = 15 * 60
    assert new_day(ahead, tz, real) == datetime(2026, 10, 19, 0, 0, tzinfo=tz)    # past midnight: the real day
    behind = Plant(datetime(2026, 10, 17, 23, 59, tzinfo=tz))
    new_day(behind, tz, real)
    behind.env.now = 120
    assert new_day(behind, tz, real) == datetime(2026, 10, 19, 0, 0, tzinfo=tz)
    in_step = Plant(datetime(2026, 10, 19, 23, 58, tzinfo=tz))        # a few minutes off the real midnight
    new_day(in_step, tz, real.replace(hour=23, minute=55))
    in_step.env.now = 180
    assert new_day(in_step, tz, real.replace(hour=23, minute=58)) is None
    late = Plant(datetime(2026, 10, 19, 23, 59, tzinfo=tz))           # hours behind, but the day is right
    new_day(late, tz, datetime(2026, 10, 20, 10, 0, tzinfo=tz))
    late.env.now = 120
    assert new_day(late, tz, datetime(2026, 10, 20, 10, 2, tzinfo=tz)) is None
    jumping = Plant(datetime(2026, 10, 19, 23, 50, tzinfo=tz))
    jumping.goto_s = 10 * 3600.0
    new_day(jumping, tz, real)
    jumping.env.now = 20 * 60
    assert new_day(jumping, tz, real) is None                         # on the way to its time: not now


def test_pause_and_jump_need_a_signed_in_user(pg_url):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text

    from mes_lab.mes.app import create_app

    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text("drop schema public cascade; create schema public"))
    engine.dispose()
    app = create_app(pg_url, edge_token="", login="actions")
    with TestClient(app) as client:
        assert client.post("/api/v1/sim/pause", json={"paused": True}).status_code == 401
        assert client.post("/api/v1/sim/goto", json={"until": "2030-01-01T10:00:00+00:00"}).status_code == 401
        assert client.get("/api/v1/clock").json()["clock"]["control"] == {"paused": False}
    app.state.engine.dispose()
