"""Running the simulation: offline (as fast as possible) or live against the MES."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime

from mes_lab import tuning
from mes_lab.model.schema import PlantModel

from .core import PlantClock, State
from .dispatch import LocalDispatcher, MesDispatcher
from .mes_client import MesClient, MesSink
from .plant import PlantSim, initial_stock_serials
from .trace import MesTrace

log = logging.getLogger("mes_lab.sim")


def run_offline(model: PlantModel, hours: float, seed: int = 1,
                start: datetime | None = None) -> PlantSim:
    start = start or datetime(2026, 1, 5, 6, 0, tzinfo=UTC)
    plant = PlantSim(model, PlantClock(start), LocalDispatcher(model, seed), seed=seed)
    plant.env.run(until=hours * 3600)
    plant.edge.checkpoint()
    return plant


FF_CHUNK_S = 120.0     # running ahead to a time: report to the MES after this much plant time


def live_step(plant: PlantSim) -> str:
    """One turn of the live loop: a wall-clock second of plant time, a chunk of a jump ahead, or
    (paused) nothing. Then the report to the MES, whose answer may pause, resume or jump."""
    if plant.paused:
        time.sleep(1.0)
        plant.edge.flush_and_apply()                       # still alive, still listening
        if not plant.paused:
            plant.resync()
        return "paused"
    if plant.goto_s is not None and plant.goto_s > plant.env.now:
        plant.resync(factor=1e-9)                           # no waiting for the wall clock
        plant.env.run(until=min(plant.env.now + FF_CHUNK_S, plant.goto_s))
        plant.edge.flush_and_apply()
        if plant.goto_s is None or plant.goto_s <= plant.env.now:
            plant.goto_s = None
            plant.resync()
        return "jump"
    plant.env.run(until=plant.env.now + plant.speed)       # one wall-clock second
    paused = plant.paused
    plant.edge.flush_and_apply()
    if plant.goto_s is None and paused != plant.paused and not plant.paused:
        plant.resync()
    return "run"


def run_live(model: PlantModel, mes_url: str, speed: float, seed: int = 1,
             token: str | None = None, stop: threading.Event | None = None,
             client: MesClient | None = None, config_dir=None) -> PlantSim:
    """Run in real time (speed x faster) until `stop` is set or Ctrl+C. The plant is the one the MES
    asks for: as configured (`model`), or a scenario of the tuning game; when the MES asks for another
    one, this run ends and a new one starts with it."""
    client = client or MesClient(mes_url, token)
    stop = stop or threading.Event()
    plant = None
    try:
        while not stop.is_set():
            wish = client.scenario()
            knobs = wish.get("knobs") or None
            built = model if not knobs else tuning.model_for(knobs, config_dir)
            plant = start_live_plant(built, client, seed, plant.speed if plant is not None else speed, knobs=knobs)
            plant.scenario_version = wish.get("version")
            while not stop.is_set() and not plant.rebuild:
                live_step(plant)
            if plant.rebuild:
                plant.edge.flush()
                log.info("the MES asks for another plant: new run")
    except KeyboardInterrupt:
        pass
    finally:
        if plant is not None:
            plant.edge.flush()
            log.info("simulation stopped at plant time %s", plant.clock.at(plant.env.now))
    return plant


def start_live_plant(model: PlantModel, client: MesClient, seed: int,
                     speed: float | None, knobs: dict | None = None, **kwargs) -> PlantSim:
    """Register a new run with the MES and build a plant that reports to it.
    speed=None gives a plant that runs as fast as possible (tests)."""
    run_id = str(uuid.uuid4())
    stock = initial_stock_serials(model, run_id)
    registration = client.register_run(run_id, seed, speed, stock, lines=sorted(model.lines), knobs=knobs)
    start = datetime.fromisoformat(registration["plant_start"])
    log.info("run %s registered, plant time starts at %s, speed %sx%s", run_id, start, speed,
             f", scenario {knobs}" if knobs else "")
    return PlantSim(model, PlantClock(start), MesDispatcher(client, run_id), seed=seed,
                    run_id=run_id, sink=MesSink(client), speed=speed,
                    trace=lambda plant: MesTrace(plant, client, run_id),
                    initial_serials=stock, **kwargs)


def summary(plant: PlantSim) -> str:
    """Plain-text report of an offline run."""
    hours = plant.env.now / 3600
    edge = plant.edge
    out = [f"Simulated {hours:.1f} h from {plant.clock.start:%Y-%m-%d %H:%M} UTC, seed {plant.seed}", ""]
    header = f"{'line':8} {'output':>7} {'jobs':>5}  {'bottleneck':12} " + " ".join(
        f"{s.value[:7]:>7}" for s in State)
    out.append(header)
    out.append("-" * len(header))
    for code, line in plant.lines.items():
        out_type = line.spec.output
        output = sum(edge.total_count[s.code][1] for s in line.stations if s.type.code == out_type)
        jobs = sum(1 for j in plant.jobs.values() if j.line_code == code and j.status == "COMPLETED")
        # bottleneck = station busiest with RUNNING (an oven is "running" whenever
        # something is inside, so it does not count)
        candidates = [s for s in line.stations if s.type.kind != "oven"] or line.stations
        busiest = max(candidates, key=lambda s: edge.total_state[(s.code, State.RUNNING.value)])
        total = sum(edge.total_state[(busiest.code, s.value)] for s in State) or 1
        shares = " ".join(f"{100 * edge.total_state[(busiest.code, s.value)] / total:6.1f}%"
                          for s in State)
        out.append(f"{code:8} {output:7d} {jobs:5d}  {busiest.code[-6:]:12} {shares}")
    out.append("")
    out.append("Supermarket (boards): " + ", ".join(
        f"{k}={len(v.items)}" for k, v in plant.supermarket.items()))
    out.append("Warehouse (units):    " + ", ".join(
        f"{k}={v['units']}" for k, v in plant.warehouse.items()))
    out.append(f"Repair queue: {len(plant.repair_queue.items)}, washing: {plant.washing}, "
               f"scrapped: {dict(plant.scrapped)}")
    out.append(f"Shipped with latent defect (escapes): {dict(plant.shipped_latent)}")
    found = {}
    for (station, code), n in edge.total_defect.items():
        found[code] = found.get(code, 0) + n
    top = sorted(found.items(), key=lambda kv: -kv[1])[:8]
    out.append("Top defects found: " + ", ".join(f"{c}={n}" for c, n in top))
    calls = sum(v[3] for v in edge.total_count.values())
    out.append(f"False calls: {calls}")
    return "\n".join(out)
