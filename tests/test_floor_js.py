"""The browser code of the live plant view, run in Node against what the simulation
really publishes: every piece is placed where the plant says, conveyors carry pieces
along their line, and drawing never fails in any state."""

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mes_lab.sim.core import PlantClock
from mes_lab.sim.dispatch import LocalDispatcher
from mes_lab.sim.plant import PlantSim

SCRIPT = Path(__file__).parent / "js" / "floor_check.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def test_page_places_and_draws_what_the_plant_reports(model, tmp_path):
    plant = PlantSim(model, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(model, 4), seed=4)
    messages = []
    wanted = {40, 95, 180, 300, 420, 421}            # 420 = a shift change: many people on their way
    wanted |= {421 + i for i in range(1, 12)}        # one snapshot a minute while they walk: walks begin and end between two
    seen_phase = set()
    for half in range(2, 3000):                      # every half minute (a truck drives in within one)
        plant.env.run(until=half * 30)
        minute = half / 2
        plant.speed = 60.0 if 420 <= minute <= 432 else None     # the live view at 60x keeps the walks of 4 minutes
        phases = {t.phase for t in plant.shipping.trucks.values()}
        new = phases - seen_phase
        seen_phase |= phases
        if minute in wanted or new:                  # and the first time a truck drives in, is loaded, drives out
            messages.append({"age": 0.5, "floor": plant.floor_snapshot(), "minute_series": 421 <= minute <= 432})
    assert {"in", "load", "out"} <= seen_phase
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"layout": plant.layout.data, "messages": messages}), encoding="utf-8")
    done = subprocess.run(["node", str(SCRIPT), str(path)], capture_output=True, text=True, timeout=240)
    assert done.returncode == 0, done.stderr[-2000:]
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["problems"] == []
    assert result["frames"] == len(messages) * 10 and result["sprites"] > 500
    assert result["playback"]["onWay"] > 50 and result["playback"]["offWay"] == 0
    assert any(m["floor"]["trucks"] for m in messages) and any(len(m["floor"]["people"]) > 40 for m in messages)
    assert any(p.get("wh") and p.get("w") for m in messages for p in m["floor"]["people"])   # a walk began while the last one is still in the picture
