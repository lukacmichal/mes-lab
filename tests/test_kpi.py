"""The robots' line-side material (kanban), the KPIs and the tuning game."""

from datetime import UTC, datetime

import pytest

from mes_lab import kpi, tuning
from mes_lab.sim.core import PlantClock
from mes_lab.sim.dispatch import LocalDispatcher
from mes_lab.sim.plant import PlantSim


@pytest.fixture(scope="module")
def run8(model):
    plant = PlantSim(model, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(model, 5), seed=5)
    plant.env.run(until=8 * 3600)
    return plant


def test_robots_bring_bins_of_parts_and_take_the_empty_ones_back(run8):
    plant = run8
    fleet = plant.fleet
    racks = plant.supply.racks
    assert {line for line, r in racks.items() if r.kind == "kit"} == {l.code for l in plant.model.lines.values() if l.area_kind.value == "FA"}
    assert all(r.used > 0 for r in racks.values() if r.kind == "kit")              # bins ran empty ...
    assert fleet.by_kind.get("kan", 0) >= sum(r.used for r in racks.values()) - 8   # ... and full ones came by robot
    assert fleet.by_kind.get("gi", 0) > 0                                           # empty bins went back
    assert all(r.starved_s < 600 for r in racks.values())                           # four robots keep up
    # the robots share the work: none of them is idle, none does it all
    orders = sorted(r.orders for r in fleet.robots)
    assert orders[0] > 0.2 * orders[-1]
    busy = [r.busy_s / plant.env.now for r in fleet.robots]
    assert sum(busy) / len(busy) > 0.25
    # the battery drains while they work (and is shown to a tenth of a percent)
    snap = plant.floor_snapshot()
    assert all(isinstance(r["b"], float) for r in snap["amr"])
    assert min(fleet.level(r) for r in fleet.robots) < 99.95 or any(r.state == "charging" for r in fleet.robots)
    assert snap["ls"]["FA-01"][0] == "kit"


def test_a_loader_without_parts_stops_and_says_so(model):
    m = tuning.build({**tuning.defaults(), "robots": 1, "kit_bins": 2})
    plant = PlantSim(m, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(m, 5), seed=5)
    reasons = set()
    for minute in range(10, 6 * 60, 2):
        plant.env.run(until=minute * 60)
        reasons |= {st.reason for st in plant.all_stations() if st.type.code == "FA_LOADER"}
    assert sum(r.starved_s for r in plant.supply.racks.values()) > 0     # one robot and two bins are not enough
    assert "NO_MATERIAL" in reasons


def test_kpis_of_a_run_are_sane(run8, model):
    result = kpi.compute(model, kpi.facts_from_plant(run8))
    p = result["plant"]
    assert 0.5 < p["oee"] < 1.0 and 0.2 < p["oee_smt"] < 1.0
    assert 0.9 < p["fpy_fa"] <= 1.0 and 0.5 < p["rty"] <= 1.0
    assert 0.6 < p["automation"] < 1.0
    assert p["good_units"] > 1000 and p["units_per_person_h"] > 2
    assert 0.1 < p["robot_utilization"] < 1.0
    for line in result["lines"]:
        if line["oee"] is not None:
            assert 0 <= line["oee"] <= 1
            assert line["oee"] == pytest.approx(line["availability"] * line["performance"] * line["quality"])
        assert abs(sum(line["losses"].values())) <= 1.0001
    assert result["areas"]["SMT"]["work_lines"] < result["areas"]["SMT"]["lines"]
    c = result["costs"]
    assert c["revenue"] > c["material"] > 0 and c["conversion"] > 0
    assert c["cost_per_unit"] == pytest.approx((c["conversion"] + c["material"]) / p["good_units"])
    assert c["margin"] == pytest.approx(c["revenue"] - c["material"] - c["conversion"])


def test_the_knobs_build_the_plant_they_describe():
    base = tuning.defaults()
    assert base["smt_lines"] == 5 and base["robots"] == 4
    knobs = tuning.clean({"smt_lines": 3, "robots": 99, "nonsense": 1}, base)
    assert knobs["smt_lines"] == 3 and knobs["robots"] == 8 and "nonsense" not in knobs
    m = tuning.build(knobs)
    assert len([l for l in m.lines.values() if l.area_kind.value == "SMT"]) == 3
    assert m.amr.robots == 8


def test_a_scenario_runs_and_is_scored():
    result = tuning.run({**tuning.defaults(), "smt_lines": 4}, hours=2)
    assert result["knobs"]["smt_lines"] == 4 and result["hours"] == 2
    assert result["costs"]["margin_per_h"] is not None
    assert result["extra"]["wall_s"] > 0
