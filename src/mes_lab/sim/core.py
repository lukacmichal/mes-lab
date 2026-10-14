"""Basic building blocks of the simulation: station states, the plant clock,
items (panels, boards, units, boxes) and jobs."""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from mes_lab.model.schema import Material, Operation


class State(StrEnum):
    RUNNING = "RUNNING"
    STARVED = "STARVED"          # waiting for input while the line has work
    BLOCKED = "BLOCKED"          # cannot pass the item on, next buffer is full
    DOWN = "DOWN"                # breakdown, waiting for repair
    MINOR_STOP = "MINOR_STOP"    # short stop (pick-up error, jam), cleared by the operator
    CHANGEOVER = "CHANGEOVER"
    MAINTENANCE = "MAINTENANCE"  # planned maintenance
    NO_ORDER = "NO_ORDER"        # the line has nothing scheduled


# States that are recorded as individual events (start/end/reason), not only as time.
EVENT_STATES = frozenset({State.DOWN, State.CHANGEOVER, State.MAINTENANCE})

TRANSFER_S = 4.0  # conveyor transfer through a station that does not work on the item


class PlantClock:
    """Maps simulation seconds to plant time (timezone-aware UTC datetimes)."""

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            raise ValueError("plant start must be timezone-aware")
        self.start = start.astimezone(UTC)
        self.epoch = self.start.timestamp()

    def at(self, t: float) -> datetime:
        return datetime.fromtimestamp(self.epoch + t, UTC)

    def iso(self, t: float | None) -> str | None:
        return None if t is None else self.at(t).isoformat(timespec="milliseconds")

    def hour_of(self, t: float) -> int:
        """Start of the hour containing t, as epoch seconds."""
        return int((self.epoch + t) // 3600) * 3600

    def hour_end(self, hour: int) -> float:
        """Simulation time at which the hour (epoch seconds) ends."""
        return hour + 3600 - self.epoch

    @staticmethod
    def hour_iso(hour: int) -> str:
        return datetime.fromtimestamp(hour, UTC).isoformat()


def lognormal(rng: random.Random, mean: float, cv: float) -> float:
    if cv <= 0:
        return mean
    sigma2 = math.log(1 + cv * cv)
    return rng.lognormvariate(math.log(mean) - sigma2 / 2, math.sqrt(sigma2))


@dataclass(frozen=True)
class JobSpec:
    """A job as handed out by the dispatcher (the MES or the offline planner)."""
    id: int
    order_no: str
    material: str
    op: str
    qty: int  # handling units to release: panels on SMT, units in final assembly


class Job:
    """Execution of one routing operation of a work order on one line."""

    def __init__(self, spec: JobSpec, material: Material, operation: Operation,
                 line_code: str, area_kind: str, exit_cell: int, now: float):
        self.id = spec.id
        self.order_no = spec.order_no
        self.material = material
        self.operation = operation
        self.steps = operation.steps
        self.qty = spec.qty
        self.line_code = line_code
        self.area_kind = area_kind
        index = material.op_index(operation.op)
        self.is_first = index == 0
        self.is_last = index == len(material.routing) - 1
        self.exit_cell = exit_cell
        # pieces counted at the end: boards for the last SMT pass, panels for an
        # earlier SMT pass, units in final assembly
        self.final_smt = area_kind == "SMT" and self.is_last
        self.expected = spec.qty * (material.boards_per_panel if self.final_smt else 1)
        self.setup_key = (material.code, operation.op)
        self.status = "ACTIVE"
        self.dispatched_at = now
        self.completed_at: float | None = None
        self.released = 0
        self.good = 0
        self.scrap = 0
        self.in_repair = 0
        self.washing = 0
        self.held = 0  # in quarantine, waiting for a decision

    def is_done(self) -> bool:
        return self.released >= self.qty and self.good + self.scrap >= self.expected

    def __repr__(self) -> str:
        return f"<Job {self.id} {self.order_no} {self.material.code}/{self.operation.op}>"


_item_ids = itertools.count(1)


class Item:
    """Anything that moves through the plant: panel, board, unit, KLT box, pallet
    (or MAG, a magazine of bare panels from the goods-in warehouse)."""

    __slots__ = ("id", "kind", "serial", "material", "job", "children", "defects", "found",
                 "bad_mark", "done", "return_cell", "repairs", "washed", "qty", "at")

    def __init__(self, kind: str, serial: str | None, material: str, job: Job | None = None,
                 children: list[Item] | None = None):
        self.id = next(_item_ids)  # the live view follows a piece by this number
        self.qty = 1               # magazines: panels inside
        self.kind = kind
        self.serial = serial
        self.material = material
        self.job = job
        self.children = children or []
        self.defects: list[str] = []   # latent, not detected yet
        self.found: list[str] = []     # detected, waiting for repair
        self.bad_mark = False          # board marked bad inside its panel
        self.done: set[str] = set()    # station types done in the current pass
        self.return_cell: int | None = None
        self.repairs = 0
        self.washed = False
        self.at = 0.0              # when it got where it is (robots, racks, totes: the live view shows it then)

    @property
    def label(self) -> str:
        if self.serial:
            return self.serial
        if self.kind == "PANEL":
            return "new panel"
        if self.children:
            return self.children[0].label
        return "?"

    def pieces(self) -> list[Item]:
        """Pieces a station works on: good boards of a panel, otherwise the item."""
        if self.kind == "PANEL":
            return [b for b in self.children if not b.bad_mark]
        return [self]

    def latent_count(self) -> int:
        return len(self.defects) + sum(c.latent_count() for c in self.children)

    def __repr__(self) -> str:
        return f"<{self.kind} {self.label}>"


class SerialGenerator:
    """Serial numbers created by the plant itself (laser markers, label printers).
    In phase 2 the MES takes over issuing them."""

    def __init__(self, prefix: str = ""):
        self.prefix = prefix
        self.counters: dict[str, int] = {}

    def next(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def panel(self) -> str:
        return f"{self.prefix}P{self.next('panel'):09d}"

    def unit(self, material: str) -> str:
        short = material.split("-")[0][:3].upper()
        return f"{self.prefix}{short}{self.next('unit:' + short):09d}"

    def klt(self) -> str:
        return f"{self.prefix}K{self.next('klt'):09d}"

    def pallet(self) -> str:
        return f"{self.prefix}PAL{self.next('pallet'):08d}"
