"""A production line at runtime: cells of stations connected by conveyor buffers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import simpy

from mes_lab.model.schema import Line as LineSpec

from .core import Item, Job, State
from .station import STATION_CLASSES, Station

if TYPE_CHECKING:
    from .plant import PlantSim

JOB_POLL_S = 120.0  # how often an idle line asks the MES for work


class CellRuntime:
    def __init__(self, index: int, type_code: str, inbox: simpy.Store | None):
        self.index = index
        self.type = type_code
        self.inbox = inbox
        self.stations: list[Station] = []


class LineRuntime:
    def __init__(self, plant: PlantSim, spec: LineSpec, inbox: simpy.Store | None = None):
        self.plant = plant
        self.env = plant.env
        self.spec = spec
        self.code = spec.code
        self.is_job = spec.is_job_line
        self.wip: set[Item] = set()
        self.rework = simpy.Store(self.env)  # washed panels waiting at the loader
        self.pending_in: dict[int, list[Item]] = {}  # items waiting to be put on a full conveyor
        self.no_order = self.is_job
        self.waiting_for_job = False
        self.active_job: Job | None = None
        self.cells: list[CellRuntime] = []
        for cell_spec in spec.cells:
            if cell_spec.index == 0:
                cell_inbox = inbox
            else:
                cell_inbox = simpy.Store(self.env, capacity=max(1, spec.buffer))
            cell = CellRuntime(cell_spec.index, cell_spec.type, cell_inbox)
            stype = plant.model.station_types[cell_spec.type]
            for st_spec in cell_spec.stations:
                cls = STATION_CLASSES.get(stype.kind, Station)
                cell.stations.append(cls(plant, self, cell, st_spec, stype))
            self.cells.append(cell)
        self.loader = self.cells[0].stations[0] if self.is_job else None

    @property
    def stations(self) -> list[Station]:
        return [s for cell in self.cells for s in cell.stations]

    def start(self) -> None:
        for station in self.stations:
            self.env.process(station.run())

    # ------------------------------------------------------------------ flow
    def exit_cell(self, steps: dict[str, float]) -> int:
        return max(c.index for c in self.cells if c.type in steps)

    def next_inbox(self, cell: CellRuntime) -> simpy.Store:
        return self.cells[cell.index + 1].inbox

    def exits_after(self, cell: CellRuntime, item: Item) -> bool:
        if self.is_job and item.job is not None:
            return cell.index >= item.job.exit_cell
        return cell.index == len(self.cells) - 1

    def exit(self, item: Item, station: Station | None = None) -> None:
        if self.is_job:
            self.plant.job_exit(item, station)
        else:
            self.plant.flow_exit(self, item)

    def enter(self, item: Item) -> None:
        if self.is_job:
            self.wip.add(item)

    def leave(self, item: Item) -> None:
        if self.is_job:
            self.wip.discard(item)
            if self.waiting_for_job and not self.wip:
                self.set_no_order(True)

    def reinsert(self, item: Item, cell_index: int):
        """SimPy process: an operator puts an item back on the conveyor."""
        self.enter(item)
        waiting = self.pending_in.setdefault(cell_index, [])
        waiting.append(item)
        yield self.cells[cell_index].inbox.put(item)
        waiting.remove(item)

    # ------------------------------------------------------------------ jobs
    def wait_for_job(self, loader: Station):
        self.active_job = None
        while True:
            spec = self.plant.dispatcher.next_job(self.code)
            if spec is not None:
                job = self.plant.create_job(spec, self)
                self.active_job = job
                self.waiting_for_job = False
                self.set_no_order(False)
                return job
            self.waiting_for_job = True
            loader.set_state(State.NO_ORDER, "NO_ORDER")
            if not self.wip:
                self.set_no_order(True)
            yield self.env.timeout(JOB_POLL_S)

    def loading_finished(self, job: Job) -> None:
        self.active_job = None

    def set_no_order(self, value: bool) -> None:
        if value == self.no_order:
            return
        self.no_order = value
        for station in self.stations:
            if station is self.loader:
                continue
            if station.state in (State.STARVED, State.NO_ORDER) and station.reason is None:
                station.idle()

    # ------------------------------------------------------------------ snapshot
    def snapshot(self) -> dict:
        return {
            "wip": len(self.wip) if self.is_job else None,
            "buffers": [len(c.inbox.items) if c.inbox is not None else None for c in self.cells],
            "buffer_capacity": self.spec.buffer,
            "job_id": self.active_job.id if self.active_job else None,
            "no_order": self.no_order if self.is_job else False,
        }
