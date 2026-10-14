"""The whole plant: lines, stores between areas, repair loop, quarantine and packing."""

from __future__ import annotations

import logging
import random
from collections import Counter, deque
from datetime import datetime
from typing import Callable, Protocol

import simpy
import simpy.rt

from mes_lab.layout import Layout
from mes_lab.model.schema import AreaKind, PlantModel

from . import floor
from .amr import Fleet
from .core import Item, Job, JobSpec, PlantClock, State
from .crew import Crew
from .edge import EdgeGateway, NullSink, Sink
from .line import LineRuntime
from .logistics import Logistics
from .shipping import Shipping
from .station import Station
from .supply import Supply
from .trace import Decision, LocalTrace, TraceClient
from .traffic import Traffic

log = logging.getLogger("mes_lab.sim")


class Dispatcher(Protocol):
    def attach(self, plant: PlantSim) -> None: ...
    def next_job(self, line_code: str) -> JobSpec | None: ...


PANELS_PER_MAGAZINE = 10   # one delivery from the goods-in warehouse
PANEL_RACK = 40            # bare panels the rack at the loader of an SMT line holds
FEED_BOARDS = 8            # boards on the feed conveyor of a final assembly line


def initial_stock_serials(model: PlantModel, run_id: str) -> dict[str, list[str]]:
    """Serial numbers of the boards already lying in the supermarket when a run
    starts. The run id makes them unique across runs."""
    tag = run_id.replace("-", "")[:6].upper()
    return {pcba.code: [f"S{tag}{pcba.code[5:8]}{n + 1:05d}" for n in range(pcba.planning.initial_stock)]
            for pcba in model.pcbas()}


class PlantSim:
    def __init__(self, model: PlantModel, clock: PlantClock, dispatcher: Dispatcher,
                 *, seed: int = 1, run_id: str = "offline", sink: Sink | None = None,
                 speed: float | None = None, sim_flush_s: float = 300.0,
                 trace: Callable[[PlantSim], TraceClient] | None = None,
                 initial_serials: dict[str, list[str]] | None = None):
        """speed=None runs as fast as possible (offline, tests); otherwise the
        simulation follows the wall clock, `speed` times faster."""
        self.model = model
        self.layout = Layout(model)
        self.clock = clock
        self.seed = seed
        self.run_id = run_id
        self.speed = speed
        if speed is None:
            self.env: simpy.Environment = simpy.Environment()
        else:
            self.env = simpy.rt.RealtimeEnvironment(factor=1 / speed, strict=False)
        self.sim_flush_s = sim_flush_s
        self.paused = False                 # the MES stopped the clock (see runner.run_live)
        self.scenario_version: int | None = None    # the plant the MES asked for when this run began ...
        self.rebuild = False                # ... and the MES now asks for another one: end this run
        self.goto_s: float | None = None    # the MES asked to run ahead to this simulation time
        self.trace: TraceClient = trace(self) if trace else LocalTrace(self)
        self.edge = EdgeGateway(self, clock, sink or NullSink(), run_id)
        self.dispatcher = dispatcher
        self.logistics = Logistics(self)
        self.tags = {code: t.tag for code, t in model.station_types.items()}

        # stores between areas
        self.supermarket: dict[str, simpy.Store] = {m.code: simpy.Store(self.env) for m in model.pcbas()}
        self.side_stores: dict[str, simpy.Store] = {}
        self.pack_inputs: dict[str, simpy.Store] = {}
        self.repair_queue = simpy.Store(self.env)
        self.inrack: list[Item] = []    # what physically lies in the inbound rack of the repair centre
        self.washing = 0
        self.open_containers: dict[str, Item] = {}  # container serial -> KLT box / pallet
        self.quarantined: dict[str, tuple[Item, tuple, Job | None]] = {}
        self.acks: list[int] = []  # MES commands carried out, reported with the next flush
        self.warehouse: dict[str, dict[str, int]] = {
            m.code: {"units": 0, "klt": 0, "pallets": 0} for m in model.finished_goods()}
        self.stock: dict[str, deque[Item]] = {m.code: deque() for m in model.finished_goods()}   # the pallets, oldest first
        self.scrapped: Counter[str] = Counter()
        self.shipped_latent: Counter[str] = Counter()
        self.jobs: dict[int, Job] = {}

        # conveyors that bring material to the lines: bare panels to SMT, boards to final assembly
        self.panel_rack: dict[str, simpy.Container] = {}   # panels in the rack at the loader
        self.panel_room: dict[str, simpy.Container] = {}   # free places in it, claimed by deliveries
        self.feed: dict[str, simpy.Store] = {}             # boards waiting at the end of the feed conveyor
        self.feed_room: dict[str, simpy.Container] = {}    # places on the feed conveyor not claimed yet

        self.lines: dict[str, LineRuntime] = {}
        for spec in model.lines.values():
            inbox = None
            if spec.area_kind == AreaKind.PACK:
                inbox = self.pack_inputs[spec.code] = simpy.Store(self.env)
            elif spec.area_kind == AreaKind.REPAIR:
                inbox = self.repair_queue
            self.lines[spec.code] = LineRuntime(self, spec, inbox)
        self.repair_line = next(l for l in self.lines.values() if l.spec.area_kind == AreaKind.REPAIR)

        serials = initial_serials or initial_stock_serials(model, run_id)
        for pcba, numbers in serials.items():
            for serial in numbers:
                self.supermarket[pcba].put(Item("BOARD", serial, pcba))

        for line in self.lines.values():
            if line.spec.area_kind == AreaKind.SMT:
                self.panel_rack[line.code] = simpy.Container(self.env, PANEL_RACK, init=PANEL_RACK)
                self.panel_room[line.code] = simpy.Container(self.env, PANEL_RACK, init=0)
                self.env.process(self._deliver_panels(line))
            elif line.spec.area_kind == AreaKind.FA:
                if len(line.spec.materials) != 1:
                    raise ValueError(f"{line.code}: the feed conveyor serves exactly one product")
                self.feed[line.code] = simpy.Store(self.env)
                self.feed_room[line.code] = simpy.Container(self.env, FEED_BOARDS, init=FEED_BOARDS)
                self.env.process(self._feed_boards(line))

        self.traffic = Traffic(self.env)
        self.crew = Crew(self)
        self.shipping = Shipping(self)
        self.fleet = Fleet(self)
        self.supply = Supply(self)
        self.env.process(self._prune_traffic())
        self.by_code = {s.code: s for s in self.all_stations()}

        dispatcher.attach(self)
        for line in self.lines.values():
            line.start()
        if speed is None:
            # live runs flush from the run loop once per wall-clock second
            self.env.process(self.edge.run(self._flush_interval))

    # ------------------------------------------------------------------ helpers
    def rng_for(self, name: str) -> random.Random:
        return random.Random(f"{self.seed}:{name}")

    @property
    def now(self) -> float:
        return self.env.now

    def _flush_interval(self) -> float:
        return self.sim_flush_s

    def all_stations(self) -> list[Station]:
        return [s for line in self.lines.values() for s in line.stations]

    def _prune_traffic(self):
        while True:
            yield self.env.timeout(60)
            self.traffic.prune()

    @staticmethod
    def nok_tote(station: Station) -> str:
        """The red NOK tote at the exit of the station's cell (a robot stop)."""
        return f"gap:{station.line.code}:{station.cell.index}"

    # ------------------------------------------------------------------ jobs
    def create_job(self, spec: JobSpec, line: LineRuntime) -> Job:
        material = self.model.materials[spec.material]
        operation = material.operation(spec.op)
        job = Job(spec, material, operation, line.code, line.spec.area_kind.value,
                  line.exit_cell(operation.steps), self.now)
        if job.area_kind == "SMT" and not job.is_last:
            self.side_stores.setdefault(job.order_no, simpy.Store(self.env))
        self.jobs[job.id] = job
        self.edge.job_changed(job)
        return job

    def check_job(self, job: Job) -> None:
        self.edge.job_changed(job)
        if job.status != "COMPLETED" and job.is_done():
            job.status = "COMPLETED"
            job.completed_at = self.now
            if job.area_kind == "SMT" and job.is_last:
                self.side_stores.pop(job.order_no, None)

    def job_progress(self, job: Job) -> dict:
        return {
            "id": job.id, "status": job.status, "released": job.released, "good": job.good,
            "scrap": job.scrap, "in_repair": job.in_repair, "held": job.held,
            "completed_at": self.clock.iso(job.completed_at),
        }

    def forget_completed_jobs(self, job_ids: set[int]) -> None:
        """Completed jobs that the MES already knows about are kept only for
        reporting in offline runs (no sink attached)."""
        if isinstance(self.edge.sink, NullSink):
            return
        for job_id in job_ids:
            job = self.jobs.get(job_id)
            if job is not None and job.status == "COMPLETED" and job_id not in self.edge.dirty_jobs:
                del self.jobs[job_id]

    def side_store(self, order_no: str) -> simpy.Store:
        return self.side_stores.setdefault(order_no, simpy.Store(self.env))

    @staticmethod
    def _pieces(item: Item, job: Job) -> int:
        """How many of the job's counted pieces an item stands for."""
        return len(item.children) if item.kind == "PANEL" and job.final_smt else 1

    # ------------------------------------------------------------------ items
    def new_panel(self, job: Job) -> Item:
        """A bare panel; it gets its serial number (and its boards theirs) at the laser."""
        panel = Item("PANEL", None, job.material.code, job)
        panel.children = [Item("BOARD", None, job.material.code, job)
                          for _ in range(job.material.boards_per_panel)]
        return panel

    def new_unit(self, job: Job, board: Item, serial: str | None) -> Item:
        unit = Item("UNIT", serial, job.material.code, job, children=[board])
        unit.defects, board.defects = board.defects, []
        board.job = job
        return unit

    def job_exit(self, item: Item, station: Station | None = None) -> None:
        """An item finished its last step on a job line; it rides a conveyor to where it goes next."""
        job = item.job
        send = self.logistics.send
        if job.area_kind == "SMT":
            if not job.is_last:
                store = self.side_store(job.order_no)
                send(f"ret:{job.line_code}", item, lambda: store.put(item))
                job.good += 1
            elif item.bad_mark:
                item.return_cell = None
                self._queue_repair(item, station)
            else:
                stock = self.supermarket[item.material]
                send(f"sm:{job.line_code}", item, lambda: stock.put(item))
                job.good += 1
        else:
            pack_line = self.model.pack_line_for(item.material)
            inbox = self.pack_inputs[pack_line.code]
            send(f"pack:{job.line_code}", item, lambda: inbox.put(item))
            job.good += 1
        self.check_job(job)

    def flow_exit(self, line: LineRuntime, item: Item) -> None:
        if line.spec.area_kind == AreaKind.PACK:
            self.logistics.send(f"wh:{line.code}", item, lambda: self._into_warehouse(item))

    def _into_warehouse(self, item: Item) -> None:
        self.stock[item.material].append(item)
        stock = self.warehouse[item.material]
        stock["pallets"] += 1
        stock["klt"] += len(item.children)
        for klt in item.children:
            stock["units"] += len(klt.children)
            for unit in klt.children:
                if unit.latent_count():
                    self.shipped_latent[item.material] += 1

    # ------------------------------------------------------------------ material for the lines
    def _deliver_panels(self, line: LineRuntime):
        """SimPy process: whenever a magazine's worth of room is free in the rack at the
        loader, the goods-in warehouse sends a magazine of bare panels down the conveyor."""
        rack, room = self.panel_rack[line.code], self.panel_room[line.code]
        route = f"supply:{line.code}"
        while True:
            yield room.get(PANELS_PER_MAGAZINE)
            magazine = Item("MAG", None, "BARE")
            magazine.qty = PANELS_PER_MAGAZINE
            self.logistics.send(route, magazine, lambda: rack.put(PANELS_PER_MAGAZINE))

    def _feed_boards(self, line: LineRuntime):
        """SimPy process: the feed conveyor of a final assembly line pulls boards out of
        the supermarket until it is full; the loader takes them from its end."""
        material = self.model.materials[line.spec.materials[0]]
        stock, feed, room = self.supermarket[material.pcba], self.feed[line.code], self.feed_room[line.code]
        route = f"feed:{line.code}"
        while True:
            yield room.get(1)
            board = yield stock.get()
            self.logistics.send(route, board, lambda b=board: feed.put(b))

    def pack(self, station: Station, item: Item, decision: Decision | None) -> Item | None:
        """Put an item into the container the MES assigned; hand the container on
        when the MES says it is full."""
        data = decision.data if decision else {}
        serial = data.get("container")
        if serial is None:  # station without interlock: no packing data, keep the item loose
            log.warning("%s packed %s without container data", station.code, item.label)
            return None
        container = self.open_containers.get(serial)
        if container is None:
            kind = "KLT" if station.type.container == "klt" else "PALLET"
            container = self.open_containers[serial] = Item(kind, serial, item.material)
        container.children.append(item)
        if data.get("full"):
            station.container = None
            return self.open_containers.pop(serial)
        station.container = container
        return None

    # ------------------------------------------------------------------ rework loops
    def wash(self, panel: Item, line: LineRuntime) -> None:
        """Misprinted panel: it rides to the washer, the paste is washed off, then it is
        carried back into the loader magazine."""
        self.washing += 1
        panel.job.washing += 1
        panel.washed = True

        def washed() -> None:
            for board in panel.children:
                board.defects = [c for c in board.defects
                                 if self.model.defect_codes[c].category != "PRINT"]
                board.found = [c for c in board.found
                               if self.model.defect_codes[c].category != "PRINT"]
            panel.done -= {"PASTE_PRINTER", "SPI"}
            self.washing -= 1
            line.rework.put(panel)
        self.logistics.send(f"wash:{line.code}", panel, washed,
                            dur=self.model.station_types["SPI"].wash_min * 60)

    def send_to_repair(self, item: Item, line: LineRuntime, station: Station | None = None) -> None:
        self._queue_repair(item, station)

    def _queue_repair(self, item: Item, station: Station | None = None) -> None:
        """Into the red NOK tote at the machine; a robot takes it to the repair centre."""
        if item.job is not None:
            item.job.in_repair += 1
            self.edge.job_changed(item.job)
        if station is None:                    # a board with a bad mark leaves SMT at the depaneler
            line = self.lines[item.job.line_code]
            station = line.cells[-1].stations[0]
        self.fleet.pickup(self.nok_tote(station), item, ("repair",))

    def into_repair_rack(self, item: Item) -> None:
        """A robot put a piece into the inbound rack; the next free bench takes it."""
        self.inrack.append(item)
        self.repair_queue.put(item)

    def return_from_repair(self, item: Item, return_to: str | None = None) -> tuple:
        """Where a repaired piece goes (the MES says: `return_to` = station type, by default the
        station that rejected it; repaired boards go to the supermarket). A robot takes it there
        from the outbound rack."""
        job = item.job
        job.in_repair -= 1
        if item.kind == "BOARD":
            item.bad_mark = False
            job.good += 1
            self.check_job(job)
            return ("sm", item.material)
        line = self.lines[job.line_code]
        cell = item.return_cell
        if return_to:
            cell = next((c.index for c in line.cells if c.type == return_to), cell)
        item.done -= {c.type for c in line.cells if c.index >= cell}  # test again from there on
        self.edge.job_changed(job)
        return ("cell", line.code, cell)

    def back_to_line(self, item: Item, line_code: str, cell: int) -> None:
        """A robot brought a piece back: onto the conveyor in front of the cell (the first cell of a job
        line: into the loader magazine)."""
        line = self.lines[line_code]
        if cell == 0 and line.is_job:
            line.enter(item)
            line.rework.put(item)
        else:
            self.env.process(line.reinsert(item, cell))

    def scrap(self, item: Item) -> None:
        """Repair failed."""
        self.scrapped[item.material] += 1
        job = item.job
        if job is not None:
            job.in_repair -= 1
            job.scrap += self._pieces(item, job)
            self.check_job(job)

    # ------------------------------------------------------------------ quarantine
    def quarantine(self, item: Item, station: Station, decision: Decision,
                   return_to: tuple | None = None) -> None:
        """The MES refused the item (hold, route, quality): it goes to the
        quarantine shelf until the MES decides to release or scrap it."""
        line = station.line
        counted = None
        if return_to is None:
            return_to = ("cell", line.code, station.cell.index)
            if line.is_job and item.job is not None and item.job.line_code == line.code:
                counted = item.job
                counted.held += 1
                self.edge.job_changed(counted)
        self.quarantined[item.label] = (item, return_to, counted)
        self.fleet.pickup(self.nok_tote(station), item, ("quar",))   # a robot takes it to the quarantine cage
        log.info("%s refused %s: %s %s", station.code, item.label, decision.reason,
                 decision.message or "")

    def apply_commands(self, commands: list[dict]) -> None:
        """Dispositions from the MES for items in quarantine: a robot takes a released piece from the
        cage back where it belongs; a scrapped one goes into the scrap bin next to the cage."""
        for command in commands:
            self.acks.append(command["id"])
            entry = self.quarantined.pop(command["serial"], None)
            if entry is None:
                continue
            item, (where, *target), job = entry
            if job is not None:
                job.held -= 1
            if command["action"] == "SCRAP":
                self.scrapped[item.material] += 1
                self.fleet.scrap(item)
                if job is not None:
                    job.scrap += self._pieces(item, job)
                    self.check_job(job)
                continue
            if job is not None:
                self.edge.job_changed(job)
            dest = ("sm", target[0]) if where == "supermarket" else ("cell", target[0], target[1])
            self.fleet.release(item, dest)

    # ------------------------------------------------------------------ control and snapshots
    def apply_control(self, control: dict) -> None:
        if control.get("commands"):
            self.apply_commands(control["commands"])
        for call in control.get("calls") or []:
            self.crew.request(call)
        speed = control.get("speed")
        if speed and self.speed is not None and float(speed) != self.speed:
            self.set_speed(float(speed))
        if "paused" in control:
            self.paused = bool(control["paused"])
        version = control.get("scenario")
        if version is not None and self.scenario_version is not None and version != self.scenario_version:
            self.rebuild = True
        if "goto" in control:
            until = control.get("goto")
            self.goto_s = None
            if until:
                t = (datetime.fromisoformat(until) - self.clock.start).total_seconds()
                self.goto_s = t if t > self.env.now else None

    def set_speed(self, speed: float) -> None:
        log.info("simulation speed %sx -> %sx", self.speed, speed)
        self.speed = speed
        self.resync()

    def resync(self, factor: float | None = None) -> None:
        """Tie the simulation clock to the wall clock again (after a pause, a jump, a new speed)."""
        # simpy exposes factor read-only, and sync() only moves the wall-clock
        # anchor; both anchors must move or the next event waits for ages
        if not isinstance(self.env, simpy.rt.RealtimeEnvironment):
            return
        self.env._factor = factor if factor is not None else 1 / self.speed
        self.env.env_start = self.env.now
        self.env.sync()

    def control_state(self) -> dict:
        """For the live view: stopped, or running ahead to a time."""
        out: dict = {"paused": self.paused}
        if self.goto_s is not None:
            out["ff"] = round(self.goto_s, 1)
        return out

    def floor_snapshot(self) -> dict:
        return floor.snapshot(self)

    def station_snapshot(self, station: Station) -> dict:
        job = station.current_job()
        return {
            "code": station.code, "state": station.state.value, "reason": station.reason,
            "since": self.clock.iso(station.since), "job_id": job.id if job else None,
            "item": station.item_label(),
        }

    def live_snapshot(self) -> dict:
        repair_busy = sum(1 for s in self.repair_line.stations if s.state == State.RUNNING)
        open_klt: Counter[str] = Counter()
        for box in self.open_containers.values():
            if box.kind == "KLT":
                open_klt[box.material] += len(box.children)
        return {
            "lines": {code: line.snapshot() for code, line in self.lines.items()},
            "supermarket": {code: len(store.items) for code, store in self.supermarket.items()},
            "side_stores": sum(len(s.items) for s in self.side_stores.values()),
            "repair": {"queue": len(self.repair_queue.items), "in_work": repair_busy,
                       "on_the_way": sum(1 for box in self.fleet.boxes.values() for _, d in box if d[0] == "repair")
                       + sum(1 for r in self.fleet.robots for _, d in r.load if d[0] == "repair")},
            "washing": self.washing,
            "fleet": {"n": len(self.fleet.robots), "busy_s": round(sum(r.busy_s for r in self.fleet.robots), 1),
                      "transports": self.fleet.done, "by_kind": dict(self.fleet.by_kind), "elapsed_s": round(self.env.now, 1)},
            "quarantine": len(self.quarantined),
            "packing": {code: len(store.items) for code, store in self.pack_inputs.items()},
            "open_klt": dict(open_klt),
            "warehouse": self.warehouse,
            "scrap": dict(self.scrapped),
        }
