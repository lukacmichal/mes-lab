"""Autonomous mobile robots (AMR) and the fleet manager that gives them their transports.

Nothing flies across the plant. A cell that rejects a piece drops it into the red NOK tote at its
exit; a piece the MES refuses goes there too. The fleet manager sends the nearest free robot: it
drives along its own lane in the aisle (see mes_lab.layout_crew), shifts sideways into the gap
beside the tote, takes what is in it (up to its capacity) and delivers every piece where it belongs:

  NOK tote of a cell        -> inbound rack of the repair centre       (repair)
                            -> quarantine cage of the repair centre    (refused by the MES)
  outbound rack of repair   -> back to the line, in the gap before the cell the MES names, or
                               repaired boards to the SMT supermarket
  quarantine cage           -> back to the line or the supermarket when the MES releases a piece
  goods-in warehouse        -> the rack at the loader of final assembly: a full bin of parts; the
                               stack of empty boxes at packing (two-bin kanban, see supply.py)
  rack at the loader        -> goods-in warehouse: the empty bins

A robot that stops somewhere also takes along what waits there for a robot, if it has room (the
empty bins when it brings a full one). The racks of the repair centre are pass-through racks in its
wall: the robot docks in the aisle, the repair people take the pieces out inside (see crew.py).
Robots are omnidirectional: they shift sideways to dock. Before a drive the robot checks the way
(traffic.py): where it crosses a walkway it waits until the people have passed.

The battery drains while driving (more with a load) and a little while standing (computer, laser
scanners, radio); an idle robot waits on its charger, and one below the low level charges before it
takes the next transport.

A transport takes the time it takes: the drive at the robot's speed, docking and moving the tote at
every stop, waiting for a robot when all are busy, waiting for people at crossings, and the charging.
"""

from __future__ import annotations

import itertools
from collections import deque
from typing import TYPE_CHECKING

import simpy

from mes_lab.layout_crew import path_length

from .core import Item
from .traffic import ROBOT

if TYPE_CHECKING:
    from .plant import PlantSim

Dest = tuple    # ("repair",) | ("quar",) | ("cell", line, cell) | ("sm", material) | ("kan", line) | ("gi",)


class Robot:
    __slots__ = ("id", "name", "home", "place", "walk", "trail", "state", "load", "battery", "_bt", "_rate",
                 "target", "busy", "wake", "orders", "km", "busy_s", "drive_s", "charge_s", "holds")

    def __init__(self, rid: int, home: str, now: float):
        self.id = rid
        self.name = f"AMR-{rid:02d}"
        self.home = home
        self.place = home
        self.walk: tuple[list, float, float] | None = None       # (points, started, duration)
        self.trail: deque = deque(maxlen=16)                       # the last drives, for the live view
        self.state = "charging"
        self.load: list[tuple[Item, Dest]] = []
        self.battery = 100.0
        self._bt = now                                             # battery level was `battery` at this time
        self._rate = 0.0                                           # % per second (+ charging, - driving)
        self.target: str | None = None                             # the stop it drives to
        self.busy = False
        self.wake: simpy.Event | None = None
        self.orders = 0
        self.km = 0.0
        self.busy_s = 0.0          # time on transports (driving, docking, waiting at crossings)
        self.holds: tuple[str, simpy.Resource] | None = None     # the stop it stands at, reserved until it leaves
        self.drive_s = 0.0
        self.charge_s = 0.0


class Fleet:
    """The fleet manager: the NOK totes, the racks, the robots and who drives where."""

    def __init__(self, plant: PlantSim):
        self.plant = plant
        self.env = plant.env
        self.cfg = plant.model.amr
        self.lanes = plant.layout.amr
        # what waits where for a robot: the NOK totes at the machines, the outbound rack of the repair
        # centre ("rpr:out") and the released pieces at the hatch of the quarantine cage ("rpr:quar")
        self.boxes: dict[str, list[tuple[Item, Dest]]] = {}
        self.since: dict[str, float] = {}                         # the oldest piece of a tote waits since
        self.claimed: set[str] = set()                            # totes a robot is on its way to
        self.cage: list[Item] = []                                # in quarantine, waiting for the MES
        self.redirect: dict[int, Dest] = {}                       # released while still on the way to the cage
        self.discard: set[int] = set()                            # scrapped while still on the way to the cage
        self.docks = {key: simpy.Resource(self.env, capacity=1) for key in plant.layout.docks}
        self.traffic = plant.traffic
        self._ids = itertools.count(1)
        self.robots = [Robot(next(self._ids), f"charge:{i}", self.env.now) for i in range(self.cfg.robots)]
        self.done = 0                                             # transports finished
        self.by_kind: dict[str, int] = {}                         # deliveries by what was carried
        self.log: deque = deque(maxlen=40)                        # the last transports, for the MES view
        self.drain = 100.0 / (self.cfg.battery_h * 3600)          # % per second of driving empty
        self.idle_drain = self.drain * self.cfg.idle_drain
        self.charge = 100.0 / (self.cfg.charge_h * 3600)
        for robot in self.robots:
            self._set_rate(robot, self.charge)
            p = self.lanes.places[robot.home]
            self.traffic.stand(("R", robot.id), ROBOT, p.x, p.y, p.face or 0.0)
            self.env.process(self._run(robot))

    # ------------------------------------------------------------------ battery
    def level(self, robot: Robot) -> float:
        return max(0.0, min(100.0, robot.battery + robot._rate * (self.env.now - robot._bt)))

    def _set_rate(self, robot: Robot, rate: float) -> None:
        robot.battery = self.level(robot)
        robot._bt = self.env.now
        robot._rate = rate

    # ------------------------------------------------------------------ what stations and repair hand over
    def pickup(self, place: str, item: Item, dest: Dest) -> None:
        """A piece waits at `place` (a NOK tote "nok:<station>", "rpr:out", "rpr:quar") for a robot."""
        if place not in self.lanes.places:
            raise KeyError(f"no robot stop {place}")
        box = self.boxes.setdefault(place, [])
        if not box:
            self.since[place] = self.env.now
        item.at = self.env.now
        box.append((item, dest))
        self._wake(place)

    def to_cage(self, item: Item) -> None:
        item.at = self.env.now
        self.cage.append(item)

    def release(self, item: Item, dest: Dest) -> bool:
        """The MES released a piece of the quarantine: out of the cage to its destination. A piece not yet
        in the cage goes there straight away (the robot that carries it, or will, gets the new address)."""
        if item in self.cage:
            self.cage.remove(item)
            self.pickup("rpr:quar", item, dest)
            return True
        for entries in [*self.boxes.values(), *(r.load for r in self.robots)]:
            for i, (it, d) in enumerate(entries):
                if it is item:
                    entries[i] = (it, dest)
                    return True
        self.redirect[item.id] = dest
        return False

    def scrap(self, item: Item) -> None:
        """The MES scrapped a piece of the quarantine: from the cage into the scrap bin next to it."""
        if item in self.cage:
            self.cage.remove(item)
        else:
            self.discard.add(item.id)

    def where_to(self, dest: Dest) -> str:
        kind = dest[0]
        if kind == "repair":
            return "rpr:in"
        if kind == "quar":
            return "rpr:quar"
        if kind == "sm":
            return "drop:SM"
        if kind == "kan":
            return f"drop:{dest[1]}:0"
        if kind == "gi":
            return "gi"
        line, cell = dest[1], dest[2]
        return f"drop:{line}:0" if cell == 0 else f"gap:{line}:{cell - 1}"     # into the gap before the cell

    # ------------------------------------------------------------------ dispatching
    def _waiting(self) -> list[str]:
        return sorted((p for p, box in self.boxes.items() if box and p not in self.claimed), key=lambda p: self.since.get(p, 0))

    def _wake(self, place: str) -> None:
        """Wake the nearest idle robot that has enough battery."""
        idle = [r for r in self.robots if not r.busy and r.wake is not None and self.level(r) >= self.cfg.low]
        if not idle:
            return
        robot = min(idle, key=lambda r: self.lanes.duration(r.place, place))
        event, robot.wake = robot.wake, None
        event.succeed()

    def _next(self, robot: Robot) -> str | None:
        waiting = self._waiting()
        if not waiting:
            return None
        # the oldest tote first, but a robot does not cross the plant for it when another one waits nearby
        oldest = waiting[0]
        near = min(waiting, key=lambda p: self.lanes.duration(robot.place, p))
        if self.env.now - self.since.get(oldest, 0) > 600 or near == oldest:
            return oldest
        return near

    # ------------------------------------------------------------------ the robot
    def driving_drain(self, robot: Robot) -> float:
        return self.drain * (1 + self.cfg.load_drain * len(robot.load) / self.cfg.capacity)

    def drive(self, robot: Robot, dest: str):
        """SimPy generator: drive along the AMR lanes to a stop. The way is checked first; where it
        crosses a walkway with people on it, the robot waits at its stop until they have passed."""
        if robot.place == dest:
            return
        pts = self.lanes.path(robot.place, dest)
        length = path_length(pts)
        who = ("R", robot.id)
        state = robot.state
        faces = (self.lanes.places[robot.place].face, self.lanes.places[dest].face)
        for _ in range(60):
            wait, _plan = self.traffic.plan(who, ROBOT, pts, self.cfg.speed, faces=faces)
            if wait is not None:
                break
            robot.state = "yield"                                  # a robot stands in the way
            yield self.env.timeout(2.0)
        else:                                                      # two minutes: go (and count it)
            self.traffic.go(who, _plan)
            wait = 0.0
        if wait > 0:
            robot.state = "yield"
            self._set_rate(robot, -self.idle_drain)
            yield self.env.timeout(wait)
        robot.state = state
        self._leave_stop(robot)
        d = max(0.5, length / self.cfg.speed)
        robot.target = dest
        walk = (pts, self.env.now, d)
        robot.walk = walk
        self._set_rate(robot, -self.driving_drain(robot))
        yield self.env.timeout(d)
        robot.trail.append(walk)
        robot.walk = None
        robot.place = dest
        robot.km += length
        robot.drive_s += d
        self._set_rate(robot, -self.idle_drain)

    def _leave_stop(self, robot: Robot) -> None:
        """The robot sets off: the stop it stood at is free for the next robot."""
        if robot.holds is not None:
            place, req = robot.holds
            self.docks[place].release(req)
            robot.holds = None

    def _go(self, robot: Robot, stop: str, state: str):
        """SimPy generator: reserve the stop, then drive there; it stays reserved until the robot leaves
        it again. While another robot stands there, this one waits where it is; when that takes long
        (the other one may wait for the very place this one stands at), it gives way: it drives to its
        charger and tries again from there."""
        while True:
            req = self.docks[stop].request()
            if not req.triggered:
                robot.state = "waiting"
                got = yield req | self.env.timeout(45)
                if req not in got:
                    req.cancel()
                    robot.state = "to_charger"
                    yield from self.drive(robot, robot.home)
                    continue
            break
        robot.state = state
        yield from self.drive(robot, stop)
        robot.holds = (stop, req)

    def _run(self, robot: Robot):
        cfg = self.cfg
        while True:
            src = None
            if self.level(robot) >= cfg.low:
                src = self._next(robot)
            if src is None:
                robot.busy = False
                if robot.place != robot.home:
                    robot.state = "to_charger"
                    yield from self.drive(robot, robot.home)
                    continue                                          # work may have come meanwhile
                robot.state = "charging" if self.level(robot) < 99.5 else "idle"
                self._set_rate(robot, self.charge)
                t_home = self.env.now
                if self.level(robot) < cfg.low:
                    # charge up to a working level first
                    yield self.env.timeout((cfg.low + 35 - self.level(robot)) / self.charge)
                    robot.charge_s += self.env.now - t_home
                    continue
                robot.wake = self.env.event()
                yield robot.wake | self.env.timeout(600)
                robot.wake = None
                robot.charge_s += min(self.env.now - t_home, max(0.0, (100.0 - robot.battery) / self.charge))
                continue
            robot.busy = True
            robot.orders += 1
            t_order = self.env.now
            self.claimed.add(src)
            yield from self._go(robot, src, "to_pick")                  # the stop is reserved, then the drive
            robot.state = "loading"
            yield self.env.timeout(cfg.transfer_s)
            self._take(robot, src)
            self.claimed.discard(src)
            started = self.env.now
            # deliver: the nearest stop first, until the robot is empty
            while robot.load:
                stops = {self.where_to(d) for _, d in robot.load}
                stop = min(stops, key=lambda p: self.lanes.duration(robot.place, p))
                yield from self._go(robot, stop, "to_drop")
                robot.state = "unloading"
                yield self.env.timeout(cfg.transfer_s)
                here = [(it, d) for it, d in robot.load if self.where_to(d) == stop]
                robot.load = [(it, d) for it, d in robot.load if self.where_to(d) != stop]
                for item, dest in here:
                    self._deliver(item, dest)
                    self.by_kind[dest[0]] = self.by_kind.get(dest[0], 0) + 1
                    self.log.append({"t": round(self.env.now, 1), "robot": robot.name, "from": src, "to": stop,
                                     "item": item.label, "kind": item.kind, "s": round(self.env.now - started, 1)})
                # what waits here for a robot goes along (the empty bins when a full one comes)
                if self.boxes.get(stop) and stop not in self.claimed and len(robot.load) < cfg.capacity:
                    robot.state = "loading"
                    yield self.env.timeout(cfg.transfer_s / 2)
                    self._take(robot, stop)
            robot.busy_s += self.env.now - t_order
            self.done += 1

    def _take(self, robot: Robot, place: str) -> None:
        """Load what waits at a stop, as much as fits; the rest waits for the next robot."""
        box = self.boxes.get(place, [])
        room = self.cfg.capacity - len(robot.load)
        taken, box[:] = box[:room], box[room:]
        for item, _ in taken:
            item.at = self.env.now                                    # on the robot from now
        robot.load += taken
        if box:                                                       # more than fits: another robot comes
            self.since[place] = self.env.now
            self._wake(place)
        else:
            self.since.pop(place, None)

    def _deliver(self, item: Item, dest: Dest) -> None:
        plant = self.plant
        kind = dest[0]
        item.at = self.env.now
        if kind == "kan":
            plant.supply.arrive(dest[1], item)
        elif kind == "gi":
            pass                                        # the empty bin is back in the warehouse
        elif kind == "repair":
            plant.into_repair_rack(item)
        elif kind == "quar":
            if item.id in self.discard:
                self.discard.discard(item.id)               # scrapped meanwhile: into the scrap bin
            elif item.id in self.redirect:
                self.pickup("rpr:quar", item, self.redirect.pop(item.id))
            else:
                self.cage.append(item)
        elif kind == "sm":
            plant.supermarket[dest[1]].put(item)
        else:
            plant.back_to_line(item, dest[1], dest[2])

    # ------------------------------------------------------------------ snapshot
    def snapshot(self, describe, keep_s: float) -> dict:
        now = self.env.now
        robots = []
        for r in self.robots:
            rec: dict = {"i": r.id, "n": r.name, "st": r.state, "b": round(self.level(r), 1), "o": r.orders}
            if r.walk is not None:
                pts, t0, d = r.walk
                rec["w"] = {"p": [[round(x, 1), round(y, 1)] for x, y in pts], "t": round(t0, 1), "d": round(d, 1)}
            else:
                p = self.lanes.places[r.place]
                rec["x"], rec["y"] = round(p.x, 1), round(p.y, 1)
                if p.face is not None:
                    rec["f"] = p.face
            trail = [{"p": [[round(x, 1), round(y, 1)] for x, y in pts], "t": round(t0, 1), "d": round(d, 1)}
                     for pts, t0, d in r.trail if t0 + d >= now - keep_s]
            if trail:
                rec["wh"] = trail
            if r.load:
                rec["ld"] = [{**describe(it), "at": round(it.at, 1)} for it, _ in r.load]
                rec["to"] = sorted({self.where_to(d) for _, d in r.load})
            if r.target and r.walk is not None:
                rec["tg"] = r.target
            robots.append(rec)
        boxes = {p: [{**describe(it), "at": round(it.at, 1)} for it, _ in box] for p, box in self.boxes.items() if box}
        busy = sum(1 for r in self.robots if r.busy)
        return {"amr": robots, "boxes": boxes, "cage": [{**describe(it), "at": round(it.at, 1)} for it in self.cage[:24]], "cage_n": len(self.cage),
                "fleet": {"done": self.done, "waiting": len(self._waiting()) + len(self.claimed), "busy": busy,
                          "kinds": dict(self.by_kind), "log": list(self.log)[-8:]}}


