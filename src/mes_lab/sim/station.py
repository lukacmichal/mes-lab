"""Stations: the machines and workplaces of the plant.

Every station is a SimPy process. It takes an item from its cell's inbox
(a conveyor buffer shared by all stations of the cell), works on it and puts the
result into the next cell's inbox. While doing so it reports its state to the
edge gateway: RUNNING, STARVED, BLOCKED, DOWN, MINOR_STOP, CHANGEOVER,
MAINTENANCE or NO_ORDER.

Interlocked stations check the item in with the MES before working on it; every
station reports the result of its work afterwards (see trace.py).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Generator

import simpy

from mes_lab.model.schema import StationKind, StationType
from mes_lab.model.schema import Station as StationSpec

from .core import TRANSFER_S, Item, Job, State, lognormal
from .trace import Decision, MesUnavailable

if TYPE_CHECKING:
    from .line import CellRuntime, LineRuntime
    from .plant import PlantSim

Proc = Generator  # SimPy process generator
Found = list[tuple[Item, list[str]]]  # pieces with the defect codes found on them

MES_RETRY_S = 60.0       # an interlocked station retries an unanswered check-in after this,
MES_RETRY_MAX_S = 900.0  # doubling the wait each time up to this, so a sick MES is not flooded


def retry_wait(attempt: int) -> float:
    return min(MES_RETRY_S * 2 ** attempt, MES_RETRY_MAX_S)


class Station:
    def __init__(self, plant: PlantSim, line: LineRuntime, cell: CellRuntime,
                 spec: StationSpec, stype: StationType):
        self.plant = plant
        self.env: simpy.Environment = plant.env
        self.line = line
        self.cell = cell
        self.spec = spec
        self.code = spec.code
        self.type = stype
        self.rng = plant.rng_for(spec.code)
        self.state = State.NO_ORDER if line.is_job else State.STARVED
        self.reason: str | None = None
        self.since = self.env.now
        self.setup: tuple[str, str] | None = None
        self.item: Item | None = None
        self.last_job: Job | None = None
        self.ttf = self._sample_ttf()
        self.op_since_pm = 0.0
        # what is physically going on, for the live view (see floor.py):
        #   idle - nothing inside      in   - piece at the entrance (check-in, setup, microstop)
        #   work - machine working     verify - operator checks the machine's calls
        #   pass - piece rides through without work    out - piece done, waiting to leave
        self.phase = "idle"
        self.out_items: list[Item] = []          # pieces done but not yet on the next conveyor
        self.work_total = 0.0                    # length of the current cycle (operating seconds)
        self.work_left = 0.0                     # what was left when `work_t` was written
        self.work_t = 0.0
        self.work_run = False                    # False while the machine is broken down
        self.container: Item | None = None       # packing: the box or pallet being filled
        self.last_result: str | None = None      # PASS / FAIL of the last piece it reported
        self.call = None                         # open call for a technician (see crew.py)
        self.attendance = None                   # an operator comes to the machine
        plant.edge.register(self)

    # ------------------------------------------------------------------ state
    def set_state(self, state: State, reason: str | None = None) -> None:
        if state is self.state and reason == self.reason:
            return
        self.plant.edge.on_state(self, state, reason)
        self.state = state
        self.reason = reason
        self.since = self.env.now

    def idle(self) -> None:
        self.set_state(State.NO_ORDER if self.line.no_order else State.STARVED)

    def current_job(self) -> Job | None:
        if self.item is not None and self.item.job is not None:
            return self.item.job
        return self.last_job

    def item_label(self) -> str | None:
        return self.item.label if self.item is not None else None

    # ------------------------------------------------------------------ random draws
    def _sample_ttf(self) -> float:
        return self.rng.expovariate(1 / (self.type.mtbf_h * 3600)) if self.type.mtbf_h else math.inf

    def _sample_repair(self) -> float:
        return lognormal(self.rng, (self.type.mttr_min or 10) * 60, self.type.mttr_cv)

    def cycle_time(self, job: Job | None) -> float:
        ideal = job.steps[self.type.code] if job is not None and self.line.is_job \
            else self.type.default_cycle_s
        return ideal * (1 + abs(self.rng.gauss(0, self.type.cycle_cv)))

    def measure(self, failed: bool) -> dict:
        """Process values the station logs (paste volume, peak temperature, torque, ...).
        A station that rejected the piece logs its first value out of the window."""
        values = {}
        for i, (name, spec) in enumerate(self.type.measurements.items()):
            value = self.rng.gauss(spec.mean, spec.sd)
            if failed and i == 0:
                value = spec.usl + spec.sd * self.rng.uniform(0.5, 3) if self.rng.random() < 0.5 \
                    else spec.lsl - spec.sd * self.rng.uniform(0.5, 3)
            values[name] = round(value, 3)
        return values

    # ------------------------------------------------------------------ building blocks
    def performs(self, item: Item) -> bool:
        if not self.line.is_job:
            return True
        job = item.job
        return self.type.code in job.steps and self.type.code not in item.done

    def gate(self, item: Item, job: Job | None, consume: Item | None = None) -> Proc:
        """Check the item in with the MES; wait while the MES does not answer."""
        attempt = 0
        while True:
            try:
                return self.plant.trace.check_in(self, item, job, consume)
            except MesUnavailable:
                self.set_state(State.DOWN, "MES_UNAVAILABLE")
                yield self.env.timeout(retry_wait(attempt))
                attempt += 1

    def changeover_reason(self, job: Job) -> str:
        """The same board's other side (bottom -> top) is a side change, anything
        else a product changeover. Both need new stencil, programs and parts."""
        return "SIDE_CHANGE" if self.setup and self.setup[0] == job.setup_key[0] else "CHANGEOVER"

    def prepare(self, job: Job | None) -> Proc:
        """Changeover, planned maintenance and minor stops before a cycle."""
        if job is not None and self.line.is_job:
            self.last_job = job
            if job.setup_key != self.setup:
                if self.setup is not None and self.type.changeover_min > 0:
                    self.set_state(State.CHANGEOVER, self.changeover_reason(job))
                    yield self.env.timeout(self.type.changeover_min * 60 * self.rng.uniform(0.8, 1.4))
                self.setup = job.setup_key
        pm = self.type.pm
        if pm and self.op_since_pm >= pm.interval_h * 3600:
            self.set_state(State.MAINTENANCE, "PLANNED_MAINTENANCE")
            yield from self.plant.crew.service(self, "PLANNED_MAINTENANCE",
                                               pm.duration_min * 60 * self.rng.uniform(0.9, 1.3), "pm")
            self.op_since_pm = 0.0
        ms = self.type.microstop
        if ms.p and self.rng.random() < ms.p:
            self.set_state(State.MINOR_STOP, "MICROSTOP")
            yield from self.plant.crew.attend(self, self.rng.expovariate(1 / ms.mean_s), "minor")

    def _progress(self, left: float, running: bool) -> None:
        self.work_left = left
        self.work_t = self.env.now
        self.work_run = running

    def work_state(self) -> tuple[float, float]:
        """(length of the cycle, seconds still to go) at this moment."""
        left = self.work_left
        if self.work_run:
            left -= self.env.now - self.work_t
        return self.work_total, max(0.0, left)

    def work(self, duration: float, phase: str = "work") -> Proc:
        """Run for `duration` operating seconds; the machine may break down meanwhile."""
        self.set_state(State.RUNNING)
        self.phase = phase
        self.work_total = duration
        remaining = duration
        while remaining > 1e-9:
            if self.ttf < remaining:
                if self.ttf > 0:
                    self._progress(remaining, True)
                    yield self.env.timeout(self.ttf)
                remaining -= self.ttf
                self.op_since_pm += self.ttf
                self._progress(remaining, False)
                yield from self.breakdown()
                self.set_state(State.RUNNING)
            else:
                self._progress(remaining, True)
                yield self.env.timeout(remaining)
                self.ttf -= remaining
                self.op_since_pm += remaining
                remaining = 0.0
        self._progress(0.0, False)

    def verify(self, duration: float) -> Proc:
        """The operator looks at what the inspection flagged. He has to be at the machine,
        so the time starts when he is there (the progress of the cycle stands still till then)."""
        self.set_state(State.RUNNING)
        self.phase = "verify"
        self.work_total = duration
        self._progress(duration, False)
        yield from self.plant.crew.attend(self, duration, "verify",
                                          on_start=lambda: self._progress(duration, True))
        self._progress(0.0, False)

    def breakdown(self) -> Proc:
        reason = self.rng.choice(self.type.failures) if self.type.failures else "PLC_FAULT"
        self.set_state(State.DOWN, reason)
        yield from self.plant.crew.service(self, reason, self._sample_repair())
        self.ttf = self._sample_ttf()

    def introduce_defects(self, item: Item) -> Found:
        """Possibly add defects to each piece; return what the station itself caught."""
        caught: Found = []
        if not self.type.defects:
            return caught
        rng = self.rng
        for piece in item.pieces():
            codes = []
            for code, spec in self.type.defects.items():
                if rng.random() < spec.p:
                    if spec.self_detect and rng.random() < spec.self_detect:
                        piece.found.append(code)
                        codes.append(code)
                    else:
                        piece.defects.append(code)
            if codes:
                caught.append((piece, codes))
        return caught

    def inspect(self, item: Item) -> tuple[Found, int]:
        """Find latent defects; return the defective pieces and the number of false calls."""
        rng = self.rng
        detects = self.type.detects
        found: Found = []
        false_calls = 0
        for piece in item.pieces():
            codes = [c for c in piece.defects if rng.random() < detects.get(c, 0.0)]
            if codes:
                for code in codes:
                    piece.defects.remove(code)
                piece.found.extend(codes)
                found.append((piece, codes))
            elif rng.random() < self.type.false_call_rate:
                false_calls += 1
        return found, false_calls

    def forward(self, outputs: list[Item], source: Item) -> Proc:
        """Pass outputs on: out of the line after its last step, else to the next cell."""
        self.item = None
        self.out_items = list(outputs)
        self.phase = "out" if outputs else "idle"
        for out in outputs:
            if self.line.exits_after(self.cell, out):
                self.line.exit(out, self)
            else:
                inbox = self.line.next_inbox(self.cell)
                if len(inbox.items) >= inbox.capacity:
                    self.set_state(State.BLOCKED)
                yield inbox.put(out)
            self.out_items.remove(out)
        self.phase = "idle"
        if self.line.exits_after(self.cell, source) or source not in outputs:
            self.line.leave(source)

    def trace(self, item: Item, job: Job | None, start: float, result: str,
              found: Found = (), action: str | None = None) -> None:
        self.last_result = result
        self.plant.trace.record(self, item, job, start, self.env.now, result, found,
                                self.measure(result == "FAIL" and self.type.kind != "inspection"),
                                action)

    # ------------------------------------------------------------------ main loop
    def run(self) -> Proc:
        inbox = self.cell.inbox
        while True:
            if not inbox.items:
                self.idle()
            item = yield inbox.get()
            self.item = item
            if self.performs(item):
                outputs = yield from self.handle(item)
            else:
                self.phase = "pass"
                self.idle()
                yield self.env.timeout(TRANSFER_S)
                outputs = [item]
            yield from self.forward(outputs, item)

    def handle(self, item: Item) -> Proc:
        job = item.job if self.line.is_job else None
        decision: Decision | None = None
        self.phase = "in"
        if self.type.interlock and (item.serial or self.type.code == "LASER_PCB"):
            decision = yield from self.gate(item, job)
            if not decision.ok:
                self.plant.quarantine(item, self, decision)
                return []
            self._take_serials(item, decision)
        if self.type.kind == StationKind.PACK and self.type.container == "klt" and decision is not None:
            box = decision.data.get("container")
            if box and box not in self.plant.open_containers:
                yield from self.plant.supply.take(self)              # a new box from the stack of empty ones
        yield from self.prepare(job)
        start = self.env.now
        yield from self.work(self.cycle_time(job))
        kind = self.type.kind
        job_id = job.id if job else None
        pieces = item.pieces()
        edge = self.plant.edge

        if kind == StationKind.INSPECTION:
            found, false_calls = self.inspect(item)
            flagged = len(found) + false_calls
            if flagged and self.type.verify_s:
                yield from self.verify(self.type.verify_s * flagged)         # operator verifies the calls
            edge.count(self, job_id, 1, len(pieces) - len(found), len(found), false_calls)
            if found:
                edge.defects_found(self, [c for _, codes in found for c in codes])
                outputs, result, action = self.reject(item, found)
                self.trace(item, job, start, result, found, action)
                return outputs
            item.done.add(self.type.code)
            self.trace(item, job, start, "PASS")
            return [item]

        caught = self.introduce_defects(item)
        if kind == StationKind.DEPANEL:
            item.done.add(self.type.code)
            edge.count(self, job_id, 1, len(pieces), 0)
            self.trace(item, job, start, "PASS")
            for board in item.children:
                board.job = item.job
            return item.children
        if kind == StationKind.PACK:
            container = self.plant.pack(self, item, decision)
            edge.count(self, None, 1, 1, 0)
            self.trace(item, job, start, "PASS")
            return [container] if container is not None else []
        edge.count(self, job_id, 1, len(pieces) - len(caught), len(caught))
        if caught:
            edge.defects_found(self, [c for _, codes in caught for c in codes])
            outputs, result, action = self.reject(item, caught)
            self.trace(item, job, start, result, caught, action)
            return outputs
        item.done.add(self.type.code)
        self.trace(item, job, start, "PASS")
        return [item]

    @staticmethod
    def _take_serials(item: Item, decision: Decision) -> None:
        data = decision.data
        if data.get("serial") and not item.serial:
            item.serial = data["serial"]
        for board, serial in zip(item.children, data.get("boards") or []):
            board.serial = serial

    def reject(self, item: Item, found: Found) -> tuple[list[Item], str, str]:
        """What happens to an item with defects: (outputs, record result, action)."""
        action = self.type.on_reject if self.type.kind == StationKind.INSPECTION else "repair"
        if item.kind == "PANEL":
            if action == "wash":
                self.plant.wash(item, self.line)
                return [], "FAIL", "WASH"
            for board, _ in found:            # bad mark, repaired after depaneling
                board.bad_mark = True
            item.done.add(self.type.code)
            return [item], "PASS", "BAD_MARK"
        item.return_cell = self.cell.index
        self.plant.send_to_repair(item, self.line, self)
        return [], "FAIL", "REPAIR"


class LoaderStation(Station):
    """First station of a job line: asks for the next job and releases its items."""

    def run(self) -> Proc:
        line = self.line
        while True:
            job = yield from line.wait_for_job(self)
            # A job is loaded completely only when its washed panels are back as well,
            # so a rework panel never meets a line already set up for the next job.
            while job.released < job.qty or job.washing or line.rework.items:
                if line.rework.items or job.released >= job.qty:
                    if not line.rework.items:
                        self.set_state(State.STARVED)
                    item = yield line.rework.get()
                    if item.washed:
                        item.washed = False
                        item.job.washing -= 1
                else:
                    item = yield from self.take(job)
                    if item is None:
                        continue
                    job.released += 1
                    self.plant.edge.job_changed(job)
                line.enter(item)
                self.item = item
                outputs = yield from self.handle(item)
                self.item = None
                yield from self.forward(outputs, item)
            line.loading_finished(job)

    def take(self, job: Job) -> Proc:
        plant = self.plant
        if job.area_kind == "SMT":
            if job.is_first:
                rack = plant.panel_rack[self.line.code]
                if not rack.level:
                    self.set_state(State.STARVED, "NO_MATERIAL")
                yield rack.get(1)                      # a bare panel from the magazine rack
                plant.panel_room[self.line.code].put(1)
                return plant.new_panel(job)
            store = plant.side_store(job.order_no)
            if not store.items:
                self.set_state(State.STARVED)
            panel = yield store.get()
            panel.job = job
            panel.done = set()
            return panel
        feed = plant.feed[self.line.code]
        if not feed.items:
            self.set_state(State.STARVED, "NO_MATERIAL")
        board = yield feed.get()
        plant.feed_room[self.line.code].put(1)
        # the MES checks the board and starts the product's life (serial number)
        self.item = board
        self.phase = "in"
        decision = yield from self.gate(board, job, consume=board)
        if not decision.ok:
            self.item = None
            self.phase = "idle"
            plant.quarantine(board, self, decision, return_to=("supermarket", board.material))
            return None
        yield from plant.supply.take(self)                 # the parts of the unit from the bin in the rack
        return plant.new_unit(job, board, decision.data.get("serial"))


class OvenStation(Station):
    """Conveyor oven: up to `capacity` items inside, each stays transit_s seconds.
    A product changeover waits until the oven is empty. While the oven is broken down
    or in service its belt stands still, so the pieces inside wait where they are."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inside = 0
        self.inside_items: list[tuple[Item, float]] = []  # (piece, belt time when it entered)
        self.belt_ran = 0.0                      # seconds the belt has run so far
        self.belt_since: float | None = self.env.now   # None while it stands still
        self.belt_halt = self.env.event()        # fires when the belt stops
        self.belt_go = self.env.event()          # fires when it runs again
        self.exit_waiting = 0
        self.override: tuple[State, str | None] | None = None
        self.last_entry = -math.inf
        self.slots = simpy.Resource(self.env, capacity=self.type.capacity)
        self.empty = self.env.event()

    def refresh(self) -> None:
        if self.override:
            self.set_state(*self.override)
        elif self.exit_waiting:
            self.set_state(State.BLOCKED)
        elif self.inside:
            self.set_state(State.RUNNING)
        else:
            self.idle()

    def item_label(self) -> str | None:
        return f"{self.inside} inside" if self.inside else None

    def belt_time(self) -> float:
        return self.belt_ran + (self.env.now - self.belt_since if self.belt_since is not None else 0.0)

    def _stop_belt(self) -> None:
        if self.belt_since is not None:
            self.belt_ran += self.env.now - self.belt_since
            self.belt_since = None
            self.belt_halt.succeed()
            self.belt_halt = self.env.event()

    def _start_belt(self) -> None:
        if self.belt_since is None:
            self.belt_since = self.env.now
            self.belt_go.succeed()
            self.belt_go = self.env.event()

    def _hold(self, state: State, reason: str | None, duration: float) -> Proc:
        self.override = (state, reason)
        stops = state in (State.DOWN, State.MAINTENANCE)
        if stops:
            self._stop_belt()
        self.refresh()
        if stops:
            yield from self.plant.crew.service(self, reason or "PLC_FAULT", duration)
        else:
            yield self.env.timeout(duration)
        self.override = None
        if stops:
            self._start_belt()
        self.refresh()

    def _ride_belt(self, duration: float) -> Proc:
        """Let `duration` seconds of belt time pass: the clock stops while the belt does."""
        remaining = duration
        while remaining > 1e-9:
            if self.belt_since is None:
                yield self.belt_go
                continue
            started = self.env.now
            yield self.env.timeout(remaining) | self.belt_halt
            remaining -= self.env.now - started

    def run(self) -> Proc:
        inbox = self.cell.inbox
        while True:
            if not inbox.items:
                self.refresh()
            item = yield inbox.get()
            self.item = item
            self.phase = "in"
            performs = self.performs(item)
            job = item.job if self.line.is_job else None
            if performs and job is not None:
                self.last_job = job
                if job.setup_key != self.setup:
                    if self.setup is not None and self.type.changeover_min > 0:
                        while self.inside:
                            yield self.empty
                        yield from self._hold(State.CHANGEOVER, self.changeover_reason(job),
                                              self.type.changeover_min * 60 * self.rng.uniform(0.8, 1.4))
                    self.setup = job.setup_key
            if performs and self.ttf <= 0:
                reason = self.rng.choice(self.type.failures) if self.type.failures else "PLC_FAULT"
                yield from self._hold(State.DOWN, reason, self._sample_repair())
                self.ttf = self._sample_ttf()
            pitch = self.cycle_time(job) if performs else TRANSFER_S
            wait = self.last_entry + pitch - self.env.now
            if wait > 0:
                yield self.env.timeout(wait)
            request = self.slots.request()
            yield request
            self.last_entry = self.env.now
            if performs:
                self.ttf -= pitch
            self.inside += 1
            self.inside_items.append((item, self.belt_time()))
            self.item = None
            self.phase = "idle"
            self.refresh()
            self.env.process(self.transit(item, request, performs, self.env.now))

    def transit(self, item: Item, request, performs: bool, entered: float) -> Proc:
        yield from self._ride_belt(self.type.transit_s)
        if performs:
            self.introduce_defects(item)
            item.done.add(self.type.code)
            job = item.job if self.line.is_job else None
            self.plant.edge.count(self, job.id if job else None, 1, len(item.pieces()), 0)
            self.trace(item, job, entered, "PASS")
        if self.line.exits_after(self.cell, item):
            self.line.exit(item, self)
            self.line.leave(item)
        else:
            inbox = self.line.next_inbox(self.cell)
            if len(inbox.items) >= inbox.capacity:
                self.exit_waiting += 1
                self.refresh()
                yield inbox.put(item)
                self.exit_waiting -= 1
            else:
                yield inbox.put(item)
        self.inside_items = [(i, t) for i, t in self.inside_items if i is not item]
        self.inside -= 1
        self.slots.release(request)
        if self.inside == 0:
            self.empty.succeed()
            self.empty = self.env.event()
        self.refresh()


class RepairStation(Station):
    """Offline repair bench. Its repair person takes the next piece out of the inbound rack (a robot
    put it there), repairs it, and carries it to the outbound rack, from where a robot takes it where
    the MES says, or into the scrap bin."""

    def run(self) -> Proc:
        inbox = self.cell.inbox
        spec = self.type.repair
        crew = self.plant.crew
        while True:
            if not inbox.items:
                self.idle()
            item = yield inbox.get()
            self.phase = "in"
            self.last_job = item.job
            yield from crew.bench_fetch(self, item)          # the walk to the rack and back
            self.item = item
            yield from self.prepare(None)
            yield from self.work(lognormal(self.rng, spec.mean_min * 60, spec.cv))
            item.repairs += 1
            success = self.rng.random() < spec.success
            attempt = 0
            while True:
                try:
                    decision = self.plant.trace.repair_done(self, item, success)
                    break
                except MesUnavailable:
                    self.set_state(State.DOWN, "MES_UNAVAILABLE")
                    yield self.env.timeout(retry_wait(attempt))
                    attempt += 1
            scrap = decision.data.get("disposition") == "SCRAP"
            self.plant.edge.count(self, item.job.id if item.job else None, 1,
                                  0 if scrap else 1, 1 if scrap else 0)
            if scrap:
                self.plant.scrap(item)
                dest = None
            else:
                item.found.clear()
                dest = self.plant.return_from_repair(item, decision.data.get("return_to"))
            self.phase = "out"
            self.idle()
            yield from crew.bench_put(self, item, dest)      # to the outbound rack or the scrap bin
            self.item = None
            self.phase = "idle"


STATION_CLASSES = {
    StationKind.LOADER: LoaderStation,
    StationKind.OVEN: OvenStation,
    StationKind.REPAIR: RepairStation,
}
