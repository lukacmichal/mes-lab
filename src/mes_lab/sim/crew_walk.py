"""Where the people are and how they walk: places, routes along the walkways, turnstiles and doors.

A part of the Crew (crew.py), which holds the state these methods work on."""

from __future__ import annotations

from mes_lab.layout_crew import path_length

from .crew_base import (
    LOGISTICS,
    MATERIAL,
    OPERATOR,
    Person,
    _passes,
)
from .traffic import PERSON, Plan


class WalkMixin:
    # ------------------------------------------------------------------ places and walking
    def xy(self, place: str) -> tuple[float, float]:
        p = self.walkways.places[place]
        return p.x, p.y

    def position(self, person: Person, now: float) -> tuple[float, float]:
        if person.walk is not None:
            pts, t0, d = person.walk
            f = min(1.0, max(0.0, (now - t0) / d))
            target = f * path_length(pts)
            run = 0.0
            for a, b in zip(pts, pts[1:]):
                seg = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
                if run + seg >= target and seg > 0:
                    g = (target - run) / seg
                    return a[0] + (b[0] - a[0]) * g, a[1] + (b[1] - a[1]) * g
                run += seg
            return pts[-1]
        return self.xy(person.place)

    def walk(self, person: Person, dest: str, queue: bool = False):
        """SimPy generator: walk along the walkways to a place. The walk is checked against everybody
        else's first (traffic.py): the person waits where they are until the way is free. `queue`: a step
        up a queue (the gate, the turnstile): the queue itself keeps the order and the distance, the
        step goes at once (the others still see it)."""
        if person.place == dest:
            return
        who = ("P", person.id)
        if queue:
            pts = self._way(person, dest)
            self.traffic.commit(who, Plan.make(PERSON, pts, self.env.now, self.speed))
            wait = 0.0
        else:
            for _ in range(45):
                pts = self._way(person, dest)
                wait, plan = self.traffic.plan(who, PERSON, pts, self.speed)
                if wait is not None:
                    break
                yield self.env.timeout(2.0)          # a robot stands in the way, or a crowd: look again in a moment
            else:
                self.traffic.go(who, plan)           # waited long enough
                wait = 0.0
        d = max(0.5, path_length(pts) / self.speed)
        walk = (pts, self.env.now + wait, d)
        person.walk = walk
        yield self.env.timeout(wait + d)
        person.trail.append(walk)               # (person.walk may have been cleared meanwhile: a new shift)
        person.walk = None
        person.place = dest

    def _way(self, person: Person, dest: str) -> list[tuple[float, float]]:
        """The way to a place; inside a room it goes around the people who stand there."""
        walkways = self.walkways
        pts = walkways.path(person.place, dest)
        rooms = walkways.rooms_of(person.place, dest)
        if rooms:
            people = []
            for rid in rooms:
                r = walkways.rooms[rid]
                people += self.traffic.standing_people((r.x, r.y, r.x + r.w, r.y + r.h), ("P", person.id))
            if people and _passes(pts, people):
                return walkways.path_around(person.place, dest, people)
        return pts

    def _settle_at(self, person: Person, place: str) -> None:
        """Put a person at a place without a walk (the start of the plant)."""
        person.place = place
        x, y = self.xy(place)
        self.traffic.stand(("P", person.id), PERSON, x, y)

    def _steps_aside(self, person: Person, other: Person, home: str):
        """SimPy generator: wait until `other` has left the place `home` (has set off), so that the
        person can step in from beside it."""
        for _ in range(240):
            if other.place != home or other.walk is not None or not other.on_site:
                return
            yield self.env.timeout(0.5)

    def _grab(self, prefix: str, count: int, person: Person, ordered: bool = False) -> str:
        """A free chair / coffee machine / cabin (one person at a time; all taken: share one).
        `ordered`: the lowest free one (a queue); otherwise one that depends on the person."""
        for i in range(count):
            key = f"{prefix}:{i if ordered else (i + person.id) % count}"
            if key not in self.held:
                self.held[key] = person.id
                return key
        return f"{prefix}:{person.id % count}"

    def _free(self, prefix: str, count: int) -> bool:
        return any(f"{prefix}:{i}" not in self.held for i in range(count))

    def _drop(self, key: str, person: Person) -> None:
        if self.held.get(key) == person.id:
            del self.held[key]

    def _rest(self, person: Person, length_s: float, back_to: str, kind: str = "lunch"):
        """SimPy generator: a break. Lunch: a chair in the break room. Coffee: at the coffee
        machine. The walks there and back are part of it."""
        t0 = self.env.now
        person.on_break = True
        person.act = "walk"
        if kind == "coffee":
            if self._free("coffee", self.n_coffee):
                spot, act = self._grab("coffee", self.n_coffee, person), "coffee"
            else:                                        # the coffee corner is full: a cup at a table
                spot, act = self._grab("break", self.n_chairs, person), "coffee"
        else:
            spot, act = self._grab("break", self.n_chairs, person), "break"
        yield from self.walk(person, spot)
        person.act = act
        rest = length_s - (self.env.now - t0) - self.walkways.duration(spot, back_to)
        if rest > 0:
            yield self.env.timeout(rest)
        self._drop(spot, person)
        person.act = "walk"
        yield from self.walk(person, back_to)
        person.on_break = False
        person.break_s += self.env.now - t0

    def _enter(self, person: Person):
        """SimPy generator: into the production. Everybody passes an ESD gate: into the queue of the
        shorter one, up the queue as the people in front go through, the test, through the gate.
        Technicians, IT and the foreman then put on an ESD coat; logistics does not come in here."""
        cfg = self.config.esd
        if person.role == LOGISTICS:
            return
        gate = min(range(self.n_gates), key=lambda g: len(self.gate_q[g]) + self.gate_busy[g])
        queue = self.gate_q[gate]
        queue.append(person)
        while True:
            idx = queue.index(person)
            spot = f"esdq{gate}:{min(idx, self.n_queue - 1)}"
            if person.place != spot and (idx < self.n_queue or person.place == "door"):
                yield from self.walk(person, spot, queue=person.place.startswith("esdq"))   # into the queue, or one place up
                continue
            if idx == 0 and not self.gate_busy[gate]:
                break
            moved = self.env.event()
            self.gate_moved[gate].append(moved)
            yield moved
        self.gate_busy[gate] = True
        queue.pop(0)
        self._queue_moved(gate)
        yield from self.walk(person, f"esd:{gate}", queue=True)
        person.act = "esd"
        yield self.env.timeout(cfg.check_s)
        person.act = "walk"
        yield from self.walk(person, f"esdx:{gate}", queue=True)   # the gate opens, through it
        self.gate_busy[gate] = False
        self._queue_moved(gate)
        self.esd_log.append((person.id, self.env.now))
        if person.role in (OPERATOR, MATERIAL):                 # ESD clothes of their own, no coat
            person.esd_ok = True
        else:
            for _ in range(60):                                 # one person at a locker: wait for a free one
                if self._free("locker", 4):
                    break
                yield self.env.timeout(3)
            slot = self._grab("locker", 4, person)
            yield from self.walk(person, slot)
            person.act = "coat"
            yield self.env.timeout(cfg.coat_s)
            self._drop(slot, person)
            person.coat = True
            self.coat_log.append((person.id, self.env.now))

    def _turnstile(self, person: Person):
        """SimPy generator: out through the turnstile, one at a time; the others wait in a queue behind it.
        Everybody joins the queue when they get to it (not when they set off from their machine): nobody
        waits at a free turnstile for a colleague who is still on the way from the far end of the hall."""
        queue = self.exit_q
        for _ in range(8):                                    # to the tail of the queue as it is when you get there
            tail = f"exitq:{min(len(queue), self.n_exitq - 1)}"
            if person.place == tail:
                break
            yield from self.walk(person, tail, queue=person.place.startswith("exitq:"))
        queue.append(person)
        joined = self.env.now
        while True:
            idx = queue.index(person)
            spot = f"exitq:{min(idx, self.n_exitq - 1)}"
            if person.place != spot and (idx < self.n_exitq or not person.place.startswith("exitq:")):
                yield from self.walk(person, spot, queue=True)
                continue
            if idx == 0 and not self.exit_busy:
                break
            moved = self.env.event()
            self.exit_moved.append(moved)
            yield moved
        self.exit_busy = True
        queue.pop(0)
        self.exit_wait.append(self.env.now - joined)
        self.left_log.append((person.id, self.env.now, person.coat))
        self._exit_moved()
        yield from self.walk(person, "exitn", queue=True)
        yield from self.walk(person, "exits", queue=True)
        away = self.env.process(self.walk(person, "door"))          # on, away from the turnstile ...
        for _ in range(60):
            if person.walk is not None and person.walk[1] <= self.env.now:
                break
            yield self.env.timeout(0.5)
        yield self.env.timeout(0.6)
        self.exit_busy = False                                      # ... then the next one comes through
        self._exit_moved()
        yield away

    def _exit_moved(self) -> None:
        events, self.exit_moved = self.exit_moved, []
        for event in events:
            event.succeed()

    def _queue_moved(self, gate: int) -> None:
        events, self.gate_moved[gate] = self.gate_moved[gate], []
        for event in events:
            event.succeed()
