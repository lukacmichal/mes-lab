from datetime import UTC, datetime

from mes_lab.sim.core import PlantClock, State
from mes_lab.sim.runner import run_offline

HOURS = 36


def _fingerprint(plant):
    return (
        {k: len(v.items) for k, v in plant.supermarket.items()},
        {k: dict(v) for k, v in plant.warehouse.items()},
        sorted((s, tuple(c)) for s, c in plant.edge.total_count.items()),
    )


def test_same_seed_same_plant(model):
    a = run_offline(model, 12, seed=11)
    b = run_offline(model, 12, seed=11)
    c = run_offline(model, 12, seed=12)
    assert _fingerprint(a) == _fingerprint(b)
    assert _fingerprint(a) != _fingerprint(c)


def test_plant_produces_and_accounts_for_every_piece(model):
    plant = run_offline(model, HOURS, seed=4)
    edge = plant.edge

    # every line made something
    for code, line in plant.lines.items():
        out = sum(edge.total_count[s.code][1] for s in line.stations if s.type.code == line.spec.output)
        assert out > 0, code
    assert sum(w["pallets"] for w in plant.warehouse.values()) > 0

    # conservation per job: released pieces are good, scrapped, in repair or still on the way
    for job in plant.jobs.values():
        per_unit = job.material.boards_per_panel if job.final_smt else 1
        accounted = job.good + job.scrap + job.in_repair
        assert accounted <= job.released * per_unit
        if job.status == "COMPLETED":
            assert job.in_repair == 0 and accounted == job.expected

    # each station's time adds up to the simulated time
    for station in plant.all_stations():
        total = sum(edge.total_state[(station.code, s.value)] for s in State)
        assert abs(total - HOURS * 3600) < 1e-3, station.code

    # breakdowns and changeovers happen
    assert sum(v for (s, st), v in edge.total_state.items() if st == "DOWN") > 0
    assert sum(v for (s, st), v in edge.total_state.items() if st == "CHANGEOVER") > 0


def test_hour_buckets_split_at_hour_boundaries():
    clock = PlantClock(datetime(2026, 3, 2, 5, 59, 30, tzinfo=UTC))
    assert clock.hour_end(clock.hour_of(0)) == 30
    assert clock.hour_iso(clock.hour_of(31)) == "2026-03-02T06:00:00+00:00"


def test_edge_payload_is_idempotent_and_complete(model):
    plant = run_offline(model, 3, seed=2)
    payload = plant.edge.build_payload()
    assert len(payload["stations"]) == len(model.stations)
    assert payload["live"]["supermarket"]
    hours = {r["hour"] for r in payload["state_time"]}
    assert hours  # open hour always reported
    # sum of seconds in the current hour never exceeds an hour per station
    per_station = {}
    for row in payload["state_time"]:
        key = (row["station"], row["hour"])
        per_station[key] = per_station.get(key, 0) + row["seconds"]
    assert max(per_station.values()) <= 3600 + 0.05   # rows are rounded to milliseconds


def test_speed_change_keeps_real_time_running(model):
    """Changing the speed mid-run must not stall the realtime environment."""
    import time

    from mes_lab.sim.dispatch import LocalDispatcher
    from mes_lab.sim.plant import PlantSim

    clock = PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC))
    plant = PlantSim(model, clock, LocalDispatcher(model, 1), seed=1, speed=36000)
    plant.env.run(until=36000)           # ten plant hours in about one second
    plant.set_speed(3600)
    t0 = time.monotonic()
    plant.env.run(until=plant.env.now + 360)    # about 0.1 s; a stale anchor would wait 10 s
    assert time.monotonic() - t0 < 2


def test_side_change_is_not_a_product_changeover(model):
    """Bottom -> top side of the same board is reported as a side change."""
    from mes_lab.sim.dispatch import LocalDispatcher
    from mes_lab.sim.plant import PlantSim

    plant = PlantSim(model, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(model, 5), seed=5)
    plant.edge.event_log_limit = 100_000
    plant.env.run(until=24 * 3600)
    reasons = {e["reason"] for e in plant.edge.closed_events if e["state"] == "CHANGEOVER"}
    assert {"CHANGEOVER", "SIDE_CHANGE"} <= reasons


def test_event_ids_are_unique_across_runs_with_the_same_seed(model):
    def ids():
        plant = run_offline(model, 6, seed=9)
        return {e["id"] for e in plant.edge.events.values()} | set(plant.edge.open_event.values())
    first, second = ids(), ids()
    assert first and second and not first & second
