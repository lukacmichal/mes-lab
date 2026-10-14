"""The people of the plant.

Everything a person does here takes the time it would take: walking along the walkways of
the floor plan (see mes_lab.layout_crew), waiting for a free colleague, working on the machine.

* Operators sit in **seats** (config/crew.yaml): at a machine (loader, final inspection,
  packing, repair bench) or at the team desk of the line. A seat's person looks after the
  machines of that seat: he walks there for a minor stop and for the operator check of an
  inspection, one at a time (`attend`).
* **Maintenance** and **IT** technicians come for breakdowns and planned maintenance of a
  machine (`service`); the nearest free one is sent, and a call waits while all are busy,
  on a break, or not on site. The IT technician gets the faults of network, printers and
  programming adapters. A call is open from the breakdown to the end of the repair.
* The **foreman** sits in his office, walks along the lines every hour and goes to every
  repair that takes more than twenty minutes.
* **Logistics** workers load the trucks (see shipping.py).
* Three shifts. People of the next shift arrive at the entrance a minute before the shift
  starts, walk to their seats and take over; the people they replace walk to the exit.
* A half hour lunch and ten minutes at the coffee machine per person and shift (the walks are
  part of them): every seat gets a relief person for the time, so a machine with a fixed
  operator is never left alone. Reliefs wait at the team desk of their lines, not in the break
  room. Technicians take their breaks when idle. Now and then somebody walks to the toilet;
  the machine waits for the operator to come back.
* Everybody who works at a computer has a desk of their own (foreman, IT, maintenance), faces
  the screen and types while there. IT takes the laptop with them to a call; maintenance fetches
  the measuring instruments from the cabinet in the workshop when the fault asks for them.
* The production is an ESD protected area: everybody who goes in passes an ESD gate (the wrist
  strap and the shoes are tested, one person at a time; the queue moves up as the gate frees);
  technicians, IT and the foreman put on an ESD coat behind it. The way out is a free turnstile in
  the same railing. Logistics stays in the warehouse and at the dock and has its own gate.
* Nobody walks through anybody (see traffic.py): every walk is checked against the others before
  it starts and waits until the way is free. People stand off the walkways: the next person of a
  seat waits beside it until the seat is handed over, a technician stands beside the operator.
* The repair people fetch the next piece themselves from the inbound rack of the repair centre
  (a robot brought it there, see amr.py), and carry it after the repair to the outbound rack or
  into the scrap bin.
* A call from the MES (`request`): somebody of a department (maintenance, IT, the foreman,
  logistics) is asked to come to a machine for a while. The nearest free one walks there, works
  and walks back; the machine goes on producing. It is on the andon board like any other call.
* Every walk is kept for a while after it ended (`trail`), so the live view can draw a person
  along the real way even when a snapshot comes only after the next walk has begun.
"""

from __future__ import annotations

import itertools
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import simpy

from mes_lab.layout_crew import path_length

from .traffic import PERSON, Plan, keep_seconds

if TYPE_CHECKING:
    from .plant import PlantSim
    from .station import Station

OPERATOR, MAINTENANCE, IT, FOREMAN, LOGISTICS = "OPERATOR", "MAINTENANCE", "IT", "FOREMAN", "LOGISTICS"
MANAGER = "MANAGER"           # the production manager (morning shift): the third level of the andon
ARRIVE_LEAD_S = 600.0         # the first of the next shift is at the door this long before it starts
ARRIVE_SPREAD_S = 540.0       # and the others come one by one during that time (the ESD gates take 6 s each)
HANDOVER_EARLY_S = 360.0      # a seat is handed over between this long before the start of the shift ...
HANDOVER_LATE_S = 240.0       # ... and this long after it
GEMBA_EVERY_S = 3600.0
GEMBA_STOP_S = 90.0
SUPERVISE_S = 240.0           # how long the foreman stays at a long repair
MANAGER_STAY_S = 360.0        # how long the production manager stays at a breakdown of the third level
ATTEND_WAIT_S = 20 * 60.0     # a machine waits this long for an operator who is away, then goes on without
FETCH_S = 15.0                # taking the measuring instruments out of the cabinet
PUT_BACK_S = 10.0
TAKE_S = 6.0                  # repair: taking a piece out of the inbound rack, scanning it
PUT_S = 5.0                   # putting it into the outbound rack / the scrap bin
REQUEST_ROLES = ("MAINTENANCE", "IT", "FOREMAN", "LOGISTICS")
HANDOVER_MAX_S = 15 * 60.0    # a technician waits at most this long after the end of the shift for the successor
UNCOAT_S = 15.0               # taking the ESD coat off in the lockers on the way home
TRAIL = 16                    # walks kept for the live view (it shows the plant a moment late, see floor-core.js)

WOMEN = ["Jana", "Eva", "Mária", "Zuzana", "Katarína", "Lucia", "Petra", "Monika", "Veronika", "Martina",
         "Alena", "Ivana", "Silvia", "Tatiana", "Denisa", "Nikola", "Andrea", "Lenka", "Barbora", "Simona",
         "Dominika", "Kristína", "Michaela", "Gabriela", "Natália"]
MEN = ["Peter", "Ján", "Martin", "Tomáš", "Michal", "Marek", "Juraj", "Pavol", "Miroslav", "Roman",
       "Milan", "Jozef", "Štefan", "Dušan", "Ľubomír", "Branislav", "Róbert", "Marián", "Igor", "Patrik",
       "Lukáš", "Matúš", "Filip", "Radoslav", "Vladimír"]
LAST = ["Novák", "Horváth", "Kováč", "Varga", "Tóth", "Nagy", "Baláž", "Szabó", "Molnár", "Lukáč",
        "Polák", "Kráľ", "Kollár", "Hudák", "Mikuš", "Urban", "Bartoš", "Švec", "Oravec", "Sedlák",
        "Gajdoš", "Beňo", "Ďurica", "Pavlík", "Mazúr", "Rusnák", "Blaho", "Jurík", "Kubík", "Hrušovský"]
# the female form of a surname where it is not simply + "ová"
FEMALE = {"Oravec": "Oravcová", "Švec": "Švecová", "Beňo": "Beňová", "Ďurica": "Ďuricová", "Blaho": "Blahová",
          "Varga": "Vargová"}


def female(surname: str) -> str:
    """Slovak female surname: Novák -> Nováková, Hrušovský -> Hrušovská, Oravec -> Oravcová."""
    if surname in FEMALE:
        return FEMALE[surname]
    if surname.endswith("ský"):
        return surname[:-1] + "á"
    if surname[-1] in "aoe":
        return surname[:-1] + "ová"
    return surname + "ová"


def _passes(pts: list[tuple[float, float]], people: list[tuple[float, float]], gap: float = 9.5) -> bool:
    """Does a way come closer than `gap` to anybody of `people` (apart from where it starts and ends)?"""
    for px, py in people:
        if math.dist(pts[0], (px, py)) < gap or math.dist(pts[-1], (px, py)) < gap:
            continue
        for a, b in zip(pts, pts[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            ll = dx * dx + dy * dy
            t = 0.0 if ll < 1e-12 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / ll))
            if math.hypot(a[0] + dx * t - px, a[1] + dy * t - py) < gap:
                return True
    return False


class Person:
    __slots__ = ("id", "name", "role", "shift", "place", "walk", "act", "target", "on_site", "seat",
                 "home_seat", "available", "on_break", "busy", "base", "task", "esd_ok", "coat", "break_s", "carry",
                 "trail", "hold", "sex", "idle_since", "arrived", "hold_at")

    def __init__(self, pid: int, name: str, role: str, shift: str, sex: str = "m"):
        self.id = pid
        self.name = name
        self.role = role
        self.shift = shift
        self.sex = sex                                          # "m" | "w": the toilet they use, how they look
        self.place = "door"
        self.walk: tuple[list, float, float] | None = None     # (points, started, duration)
        self.act = "off"
        self.target: str | None = None                          # the machine he is working at
        self.on_site = False
        self.seat: Seat | None = None                           # the seat he sits in right now
        self.home_seat: Seat | None = None                      # the seat that is his own (operators)
        self.available = False                                  # technicians: idle and at their base
        self.on_break = False
        self.busy = False
        self.base = "door"
        self.task: str | None = None
        self.esd_ok = False                                     # operators: passed the ESD gate
        self.coat = False                                       # technicians, IT, foreman: wear an ESD coat
        self.break_s = 0.0                                      # time spent on the breaks of this shift
        self.carry: str | None = None                           # "laptop" | "meter" | "tools" while on a call
        self.trail: deque = deque(maxlen=TRAIL)                 # the last walks, for the live view
        self.hold = None                                        # a piece in the hands (repair)
        self.hold_at = 0.0                                      # ... since
        self.idle_since = 0.0                                   # free and at the base since (logistics: the lounge)
        self.arrived = False                                    # this shift: through the entrance and at the base


class Seat:
    def __init__(self, env: simpy.Environment, key: str, spec: dict):
        self.key = key
        self.line = spec["line"]
        self.code = spec["code"]
        self.area = spec["area"]
        self.home = spec["home"]
        self.handover = spec.get("handover") or spec["home"]     # where the next person waits for the seat
        self.fixed = spec["fixed"]
        self.attends: list[str] = spec["attends"]
        self.occupant: Person | None = None
        self.busy = simpy.Resource(env, capacity=1)


@dataclass
class Call:
    """A machine that waits for a technician (breakdown, planned maintenance)."""
    station: str
    reason: str
    role: str
    kind: str                       # "repair", "pm" or "request" (called from the MES)
    since: float
    ph: str = "wait"                # wait -> walk -> fix
    person: Person | None = None
    visited: bool = False
    rid: int | None = None          # the id the MES gave a request
    by: str | None = None           # who asked for it
    managed: bool = False           # the third level of the andon reached the production manager


@dataclass
class Attendance:
    """An operator comes to a machine (minor stop, inspection check)."""
    kind: str
    ph: str = "wait"
    person: Person | None = None


class Pool:
    """Technicians of one role: the nearest free one takes the next call."""

    def __init__(self, crew: Crew, role: str):
        self.crew = crew
        self.role = role
        self.persons: list[Person] = []
        self.waiting: deque[tuple[str, simpy.Event]] = deque()   # (place of the machine, event)

    def free(self) -> list[Person]:
        return [p for p in self.persons if p.on_site and p.available]

    def acquire(self, place: str):
        """SimPy generator: returns a person (now busy) once one is free."""
        person = self._nearest(place)
        if person is None:
            event = self.crew.env.event()
            self.waiting.append((place, event))
            person = yield event
        return person

    def _nearest(self, place: str) -> Person | None:
        free = self.free()
        if not free:
            return None
        walk = self.crew.walkways
        best = min(free, key=lambda p: walk.duration(p.place, place))
        best.available = False
        best.busy = True
        return best

    def offer(self, person: Person) -> bool:
        """A person became free: give him the oldest waiting call. False when none waits."""
        if not self.waiting:
            return False
        place, event = self.waiting.popleft()
        person.available = False
        person.busy = True
        event.succeed(person)
        return True


class Crew:
    def __init__(self, plant: PlantSim):
        self.plant = plant
        self.env = plant.env
        self.model = plant.model
        self.layout = plant.layout
        self.walkways = plant.layout.walk
        self.config = plant.model.crew
        self.rng = plant.rng_for("crew")
        self.names = plant.rng_for("names")            # who is who: names and sex, apart from what people do
        self.tz = ZoneInfo(plant.model.plant.timezone)
        self.speed = self.config.walk_speed
        self._ids = itertools.count(1)
        self.persons: dict[int, Person] = {}
        self.roster: dict[str, dict] = {}
        self.seats: dict[str, Seat] = {k: Seat(self.env, k, spec) for k, spec in plant.layout.seats.items()}
        self.seat_of: dict[str, Seat] = {}
        for seat in self.seats.values():
            for code in seat.attends:
                self.seat_of[code] = seat
        self.pools = {role: Pool(self, role) for role in (MAINTENANCE, IT, LOGISTICS)}
        self.calls: list[Call] = []
        self.foreman_calls: deque[tuple[Call, float]] = deque()
        self.requests_done: deque[dict] = deque(maxlen=20)   # finished calls from the MES, with their times
        self.escalations: deque[dict] = deque(maxlen=40)     # the andon: who was brought to which breakdown
        places = self.walkways.places
        self.n_coffee = sum(1 for k in places if k.startswith("coffee:"))
        self.n_wc = {sex: sum(1 for k in places if k.startswith(f"wc{sex}:")) for sex in ("m", "w")}
        self.n_sink = {sex: sum(1 for k in places if k.startswith(f"sink{sex}:")) for sex in ("m", "w")}
        self.n_lounge = sum(1 for k in places if k.startswith("lounge:"))
        self.n_meet = sum(1 for k in places if k.startswith("meet:"))
        self.n_chairs = sum(1 for k in places if k.startswith("break:"))
        self.held: dict[str, int] = {}                  # chairs, coffee machines, cabins that hold one person
        self.traffic = plant.traffic
        self.n_gates = self.config.esd.gates
        self.gate_q: list[list[Person]] = [[] for _ in range(self.n_gates)]     # the queue in front of a gate, in order
        self.gate_busy = [False] * self.n_gates
        self.gate_moved: list[list[simpy.Event]] = [[] for _ in range(self.n_gates)]
        self.n_queue = sum(1 for k in self.walkways.places if k.startswith("esdq0:"))
        self.exit_q: list[Person] = []                    # the queue to the turnstile on the way out
        self.exit_busy = False
        self.exit_moved: list[simpy.Event] = []
        self.n_exitq = sum(1 for k in self.walkways.places if k.startswith("exitq:"))
        self.esd_log: list[tuple[int, float]] = []      # (person, time) of every pass of an ESD gate
        self.coat_log: list[tuple[int, float]] = []
        self.wc_log: list[tuple[int, float]] = []
        self.wc_used: list[tuple[int, str]] = []        # (person, cabin) of every visit: the right toilet
        self.left_log: list[tuple[int, float, bool]] = []   # (person, time out of the turnstile, still in a coat)
        self.exit_wait: list[float] = []                # how long each one stood in the queue to the turnstile
        self.current_shift = ""
        self.window = (0.0, 0.0)
        self._names: set[str] = set()
        self.shift_times = {s.code: (s.start.strftime("%H:%M"), s.end.strftime("%H:%M")) for s in self.model.shifts}
        self.seat_order = sorted(self.seats, key=self._seat_sort)
        self._build_roster()
        self._start_shift_zero()
        self.env.process(self._phone_escalations())

    # ------------------------------------------------------------------ time
    def _boundaries(self, t: float) -> list[tuple[float, str]]:
        """Shift starts around the time t, as (simulation seconds, shift code)."""
        local = self.plant.clock.at(t).astimezone(self.tz)
        out = []
        for day in (-1, 0, 1, 2):
            date = (local + timedelta(days=day)).date()
            for shift in self.model.shifts:
                at = datetime.combine(date, shift.start, tzinfo=self.tz)
                out.append(((at - self.plant.clock.start).total_seconds(), shift.code))
        return sorted(out)

    def shift_window(self, t: float) -> tuple[str, float, float]:
        """(shift code, start, end) of the shift that is on at time t."""
        bounds = self._boundaries(t)
        current = [b for b in bounds if b[0] <= t][-1]
        following = [b for b in bounds if b[0] > t][0]
        return current[1], current[0], following[0]

    # ------------------------------------------------------------------ roster
    def _seat_sort(self, key: str) -> tuple:
        seat = self.seats[key]
        order = {"SMT": 0, "FA": 1, "PACK": 2, "REPAIR": 3}
        return (order.get(seat.area, 9), seat.line, key)

    def _name(self, sex: str) -> str:
        rng = self.names
        first = WOMEN if sex == "w" else MEN
        last = (lambda n: female(n)) if sex == "w" else (lambda n: n)
        for _ in range(200):
            name = f"{rng.choice(first)} {last(rng.choice(LAST))}"
            if name not in self._names:
                self._names.add(name)
                return name
        return f"{rng.choice(first)} {last(rng.choice(LAST))} {len(self._names)}"

    def _person(self, role: str, shift: str) -> Person:
        sex = "w" if self.names.random() < self.config.women.get(role, 0.3) else "m"
        if self.n_wc.get(sex, 0) == 0:
            sex = "m" if self.n_wc.get("m") else "w"
        person = Person(next(self._ids), self._name(sex), role, shift, sex)
        self.persons[person.id] = person
        return person

    def _build_roster(self) -> None:
        used = {"maintenance": 0, "it": 0, "foreman": 0, "logistics": 0, "manager": 0}   # workstations taken by the earlier shifts
        for ordinal, (shift, staff) in enumerate(self.config.shifts.items()):
            ros: dict = {"primaries": {}, "reliefs": [], "maintenance": [], "it": [], "foreman": [], "logistics": [],
                         "manager": []}
            for key in self.seat_order:
                person = self._person(OPERATOR, shift)
                person.home_seat = self.seats[key]
                ros["primaries"][key] = person
            ros["reliefs"] = [self._person(OPERATOR, shift) for _ in range(staff.relief)]
            keys = self.seat_order
            size = -(-len(keys) // max(1, len(ros["reliefs"])))
            for j, relief in enumerate(ros["reliefs"]):
                relief.base = self._reserve_for(keys[j * size:(j + 1) * size], ordinal)
            # everybody has a workstation of their own; the three shifts have different ones, so
            # nobody stands on the place of a colleague during the hand-over
            for role, key, prefix, count in ((MAINTENANCE, "maintenance", "workshop", staff.maintenance),
                                             (IT, "it", "it", staff.it),
                                             (FOREMAN, "foreman", "office", staff.foreman),
                                             (LOGISTICS, "logistics", "dockwait", staff.logistics),
                                             (MANAGER, "manager", "manager", staff.manager)):
                for i in range(count):
                    person = self._person(role, shift)
                    person.base = f"{prefix}:{used[key] + i}"
                    ros[key].append(person)
                used[key] += count
            self.roster[shift] = ros

    def _reserve_for(self, keys: list[str], ordinal: int) -> str:
        """Where the relief person of these seats waits: at the team desk of the first line."""
        if not keys:
            return "door"
        seat = self.seats[keys[0]]
        reserve = f"reserve:{seat.line}:{ordinal}"
        return reserve if reserve in self.walkways.places else seat.home

    def _all_in(self, shift: str) -> list[Person]:
        ros = self.roster[shift]
        out = list(ros["primaries"].values()) + ros["reliefs"]
        for k in ("maintenance", "it", "foreman", "logistics", "manager"):
            out += ros[k]
        return out

    def _start_shift_zero(self) -> None:
        """The plant starts in the middle of a shift: its people are already at work."""
        code, start, end = self.shift_window(0.0)
        self.current_shift, self.window = code, (start, end)
        self._arrive(code, instant=True)
        self.env.process(self._shift_manager())

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
        if person.role == OPERATOR:
            person.esd_ok = True
        else:
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

    # ------------------------------------------------------------------ shifts
    def _arrive(self, shift: str, instant: bool = False) -> None:
        """People of a shift come on site (at the door; at once to their places when `instant`)."""
        ros = self.roster[shift]
        start_t = self.window[0]
        for person in self._all_in(shift):
            person.on_site = instant                      # the others come through the door later
            person.on_break = False
            person.busy = False
            person.walk = None
            person.target = None
            person.break_s = 0.0
            person.esd_ok = person.coat = False
            person.arrived = instant
            if instant:
                self._settle(person, ros)
            else:
                person.place = "door"
                person.act = "off"
                self.env.process(self._take_post(person, ros, start_t))
        if instant:
            self._start_routines(shift)

    def _settle(self, person: Person, ros: dict) -> None:
        """Put a person where he waits at the start (used for the first shift)."""
        if person.home_seat is not None:
            seat = person.home_seat
            seat.occupant = person
            person.seat = seat
            self._settle_at(person, seat.home)
            person.act = "post"
        elif person.role == OPERATOR:           # relief: waits at the team desk of the lines
            self._settle_at(person, person.base)
            person.act = "idle"
        else:
            self._settle_at(person, person.base)
            person.available = person.role in (MAINTENANCE, IT, LOGISTICS)
            person.act = "idle"
        person.esd_ok = person.role == OPERATOR
        person.coat = person.role in (MAINTENANCE, IT, FOREMAN, MANAGER)

    def _take_post(self, person: Person, ros: dict, start_t: float):
        """SimPy process: a person of the new shift comes in some minutes before the shift starts,
        passes the ESD gate (or puts on a coat), walks to the place and takes over when the shift starts."""
        yield self.env.timeout(self.rng.uniform(0.0, ARRIVE_SPREAD_S))
        person.on_site = True
        person.place = "street:log" if person.role == LOGISTICS else "door"
        person.act = "walk"
        yield from self._enter(person)
        if person.home_seat is not None:
            seat = person.home_seat
            yield from self.walk(person, seat.handover)   # beside the seat: the colleague is still there
            person.act = "idle"
            person.arrived = True
            # the hand-over: a few minutes around the start of the shift, seat by seat (the two talk, then
            # the old one goes), so the people of the old shift do not all leave in the same minute
            hand_at = start_t + self.rng.uniform(-HANDOVER_EARLY_S, HANDOVER_LATE_S)
            if hand_at > self.env.now:
                yield self.env.timeout(hand_at - self.env.now)
            with seat.busy.request() as req:
                yield req
                old = seat.occupant
                seat.occupant = person
                person.seat = seat
                person.act = "post"
            if old is not None and old is not person:
                old.seat = None
                self.env.process(self._leave(old))
                yield self.env.timeout(0.05)              # the colleague sets off first ...
                yield from self._steps_aside(person, old, seat.home)
            yield from self.walk(person, seat.home)       # ... then the seat is taken
        elif person.role == OPERATOR:
            yield from self.walk(person, person.base)
            person.act = "idle"
            person.arrived = True
        else:
            yield from self.walk(person, person.base)
            person.act = "idle"
            person.arrived = True
            person.available = person.role in (MAINTENANCE, IT, LOGISTICS)
            if person.available:
                self._became_free(person)

    def _leave(self, person: Person):
        """SimPy process: walk to the exit and go home (when no longer needed)."""
        if person.task == "leaving" or not person.on_site:
            return
        person.task = "leaving"
        while person.busy or (person.seat is not None and person.seat.occupant is person):
            yield self.env.timeout(20)
        person.available = False
        person.act = "walk"
        if person.role == LOGISTICS:
            yield from self.walk(person, "street:log")
        else:
            if person.coat:                                # the ESD coat stays in the locker
                slot = self._grab("locker", 4, person)
                yield from self.walk(person, slot)
                person.act = "uncoat"
                yield self.env.timeout(UNCOAT_S)
                self._drop(slot, person)
                person.coat = False
                person.act = "walk"
            yield from self._turnstile(person)             # out through the turnstile in the railing
            yield from self.walk(person, "door")
        self.traffic.gone(("P", person.id))
        person.on_site = False
        person.act = "off"
        person.target = None
        person.place = "door"
        person.task = None
        person.esd_ok = person.coat = False
        person.carry = None

    def _shift_manager(self):
        while True:
            code, start, end = self.shift_window(self.env.now)
            yield self.env.timeout(max(0.0, end - ARRIVE_LEAD_S - self.env.now))
            nxt, _, _ = self.shift_window(end + 1)
            outgoing = self.current_shift
            self.current_shift, self.window = nxt, (end, self.shift_window(end + 1)[2])
            self._arrive_next(nxt, outgoing)
            yield self.env.timeout(ARRIVE_LEAD_S + 1)

    def _arrive_next(self, shift: str, outgoing: str) -> None:
        self._arrive(shift)
        self._start_routines(shift)
        # people of the old shift that hold no seat or call leave now
        for person in self._all_in(outgoing):
            if person.on_site and person.seat is None and not person.busy and person.role != OPERATOR:
                self.env.process(self._retire(person, shift))
            elif person.on_site and person.role == OPERATOR and person.home_seat is None and person.seat is None:
                self.env.process(self._leave(person))
            elif person.role in (FOREMAN, MANAGER) and person.on_site:
                self.env.process(self._retire(person, shift))

    def _retire(self, person: Person, new_shift: str):
        """A technician, IT, the foreman or a logistics worker of the old shift hands over to their own
        successor (the colleague of the next shift in the same place of the roster) around the end of the
        shift, the way the operators hand over their seats, and goes home. Somebody who comes late is
        waited for, at most HANDOVER_MAX_S past the end of the shift; work in hand is finished first."""
        person.available = False
        mine = [p for p in self._all_in(person.shift) if p.role == person.role]
        theirs = [p for p in self._all_in(new_shift) if p.role == person.role]
        successor = theirs[mine.index(person) % len(theirs)] if theirs and person in mine else None
        start = self.window[0]                                   # the new shift starts (= the old one ends)
        hand_at = start + self.rng.uniform(-HANDOVER_EARLY_S / 2, HANDOVER_LATE_S / 2)
        while person.on_site and self.env.now < start + HANDOVER_MAX_S:
            here = successor is None or (successor.on_site and successor.arrived)
            if self.env.now >= hand_at and here:
                break
            if self.env.now >= hand_at and person.act in ("idle", "lounge"):
                person.act = "handover"                          # waits for the colleague, then goes
            yield self.env.timeout(10)
        yield from self._leave(person)

    # ------------------------------------------------------------------ routines of a shift
    def _start_routines(self, shift: str) -> None:
        ros = self.roster[shift]
        start, end = self.window
        # breaks of the operators: reliefs go round their seats
        keys = self.seat_order
        count = max(1, len(ros["reliefs"]))
        size = -(-len(keys) // count)
        for j, relief in enumerate(ros["reliefs"]):
            seats = [self.seats[k] for k in keys[j * size:(j + 1) * size]]
            if seats:
                self.env.process(self._relief_rounds(relief, seats, shift, start, end))
        for role in ("maintenance", "it", "logistics"):
            for k, person in enumerate(ros[role]):
                pool = self.pools[{"maintenance": MAINTENANCE, "it": IT, "logistics": LOGISTICS}[role]]
                if person not in pool.persons:
                    pool.persons.append(person)
                self.env.process(self._technician_breaks(person, k, shift, start, end))
        for person in ros["foreman"]:
            self.env.process(self._foreman(person, shift, start, end))
        for person in ros["manager"]:
            self.env.process(self._manager(person, shift, start, end))
        for person in ros["maintenance"] + ros["it"]:
            self.env.process(self._join_meeting(person, shift, start))
        if self.n_lounge:
            for person in ros["logistics"]:
                self.env.process(self._lounge(person, shift))
        for person in list(ros["primaries"].values()) + ros["maintenance"] + ros["it"]:
            self.env.process(self._wc_trips(person, shift, start, end))

    # ----- operator breaks
    def _relief_rounds(self, relief: Person, seats: list[Seat], shift: str, start: float, end: float):
        for rule in self.config.breaks:
            round_start = start + rule.start * 60
            if round_start < self.env.now - 5 * 60:
                continue                                              # this round was before the plant began
            yield self.env.timeout(max(0.0, round_start - self.env.now))
            for seat in seats:
                if self.current_shift != shift or self.env.now + rule.length * 60 + 180 > end:
                    break
                primary = self.roster[shift]["primaries"][seat.key]
                if not primary.on_site:
                    continue
                yield from self.walk(relief, seat.handover)          # beside the seat
                if self.current_shift != shift:
                    break
                while primary.task == "wc":                           # on the toilet: wait for the return
                    yield self.env.timeout(10)
                with seat.busy.request() as req:
                    yield req
                    if seat.occupant is not primary:
                        continue
                    seat.occupant = relief
                    relief.seat = seat
                    relief.act = "post"
                    primary.seat = None
                    primary.act = "break"
                    primary.on_break = True
                returned = self.env.event()
                self.env.process(self._take_break(primary, seat, rule, returned, relief))
                yield self.env.timeout(0.05)                          # the colleague sets off first ...
                yield from self._steps_aside(relief, primary, seat.home)
                yield from self.walk(relief, seat.home)               # ... then the relief sits in
                yield returned                                        # the colleague is back beside the seat
                with seat.busy.request() as req:
                    yield req
                    if seat.occupant is relief and primary.shift == self.current_shift:
                        seat.occupant = primary
                        primary.seat = seat
                        primary.act = "post"
                    relief.seat = None
            relief.act = "idle"
            if self.current_shift == shift:
                yield from self.walk(relief, relief.base)
                # and the own half hour of the relief, when the shift has time left for it
                if end - self.env.now > (rule.length + 30) * 60 and relief.on_site and relief.seat is None:
                    yield from self._rest(relief, rule.length * 60, relief.base, rule.kind)
                    relief.act = "idle"
        if self.current_shift != shift and relief.on_site and relief.seat is None:
            self.env.process(self._leave(relief))

    def _take_break(self, person: Person, seat: Seat, rule, returned: simpy.Event, relief: Person | None = None):
        yield from self._rest(person, rule.length * 60, seat.handover, rule.kind)
        returned.succeed()
        if person.shift != self.current_shift:
            self.env.process(self._leave(person))
            return
        if relief is not None:                                       # the relief stands up and goes, then the seat
            yield self.env.timeout(0.05)
            yield from self._steps_aside(person, relief, seat.home)
        if person.seat is seat:
            yield from self.walk(person, seat.home)
            person.act = "post"

    # ----- the toilet
    def _wc_trips(self, person: Person, shift: str, start: float, end: float):
        """SimPy process: now and then a person walks to the toilet."""
        trips = self.config.wc.trips
        times = sorted(self.rng.uniform(start + 40 * 60, end - 40 * 60) for _ in range(trips))
        for at in times:
            if at < self.env.now:
                continue
            yield self.env.timeout(at - self.env.now)
            while self.current_shift == shift and person.on_site and self.env.now < end - 20 * 60:
                done = yield from self._go_to_wc(person)
                if done:
                    break
                yield self.env.timeout(60)

    def _go_to_wc(self, person: Person):
        stay = self.config.wc.minutes * 60 * self.rng.uniform(0.7, 1.4)
        cabins = f"wc{person.sex}"
        if not self._free(cabins, self.n_wc[person.sex]):
            return False                                   # all cabins taken: try again a bit later
        if person.role == OPERATOR:
            seat = person.home_seat
            if seat is None or person.on_break:
                return False
            with seat.busy.request() as req:
                yield req
                if seat.occupant is not person or person.on_break:
                    return False
                seat.occupant = None                       # the seat is empty until the person is back
                person.seat = None
                person.task = "wc"
            yield from self._toilet(person, cabins, stay)
            yield from self.walk(person, seat.home)
            with seat.busy.request() as req:
                yield req
                if seat.occupant is None and person.shift == self.current_shift:
                    seat.occupant = person
                    person.seat = seat
                    person.act = "post"
            person.task = None
            if person.seat is None:                        # the shift changed meanwhile
                self.env.process(self._leave(person))
            return True
        if not (person.available and person.on_site):
            return False
        person.available = False
        yield from self._toilet(person, cabins, stay)
        yield from self.walk(person, person.base)
        person.act = "idle"
        if person.shift == self.current_shift:
            self._became_free(person)
        elif person.on_site:
            self.env.process(self._leave(person))
        return True

    def _toilet(self, person: Person, cabins: str, stay: float):
        """SimPy generator: into a free cabin of the person's toilet (the door closes behind them), then the
        hands are washed at a basin. Ends at the basin, with the person setting off again."""
        wc = self._grab(cabins, self.n_wc[person.sex], person)
        person.act = "walk"
        yield from self.walk(person, wc)
        person.act = "wc"
        yield self.env.timeout(stay)
        self._drop(wc, person)
        self.wc_log.append((person.id, self.env.now))
        self.wc_used.append((person.id, wc))
        wash = self.config.wc.wash_s
        if wash > 0 and self.n_sink[person.sex]:
            sink = self._grab(f"sink{person.sex}", self.n_sink[person.sex], person)
            person.act = "walk"
            yield from self.walk(person, sink)
            person.act = "wash"
            yield self.env.timeout(wash)
            self._drop(sink, person)
        person.act = "walk"

    # ----- technicians, logistics
    def _technician_breaks(self, person: Person, k: int, shift: str, start: float, end: float):
        for rule in self.config.breaks:
            at = start + (rule.start + 25 * k) * 60
            if at < self.env.now - 5 * 60:
                continue
            yield self.env.timeout(max(0.0, at - self.env.now))
            if self.current_shift != shift:
                return
            while not (person.available and person.on_site):
                if self.env.now > end - 15 * 60 or not person.on_site:
                    break
                yield self.env.timeout(30)
            if self.env.now + rule.length * 60 + 300 > end or not person.on_site or not person.available:
                continue
            person.available = False
            person.busy = False
            yield from self._rest(person, rule.length * 60, person.base, rule.kind)
            person.act = "idle"
            if person.shift == self.current_shift:
                self._became_free(person)

    def _lounge(self, person: Person, shift: str):
        """SimPy process (logistics): a worker who has stood at the dock with nothing to do for a while sits
        down at the table in the corner (cards with a colleague, a book alone, see snapshot) and stays
        available: the next truck calls them from there."""
        idle_s = self.config.lounge.after_min * 60
        while self.current_shift == shift and person.on_site:
            yield self.env.timeout(20)
            if not (person.available and person.place == person.base and person.walk is None)                     or person.task == "leaving" or self.env.now - person.idle_since < idle_s:
                continue
            if not self._free("lounge", self.n_lounge):
                continue
            seat = self._grab("lounge", self.n_lounge, person, ordered=True)
            person.available = False
            person.act = "walk"
            yield from self.walk(person, seat)
            person.act = "lounge"
            if person.shift == self.current_shift and person.task != "leaving":
                self._became_free(person)                 # a truck that waits takes them at once
            while person.place == seat and person.walk is None and person.on_site:
                yield self.env.timeout(5)                 # sits until a truck, a break or the way home
            self._drop(seat, person)

    def _became_free(self, person: Person) -> None:
        person.busy = False
        person.available = True
        person.idle_since = self.env.now
        pool = self.pools.get(person.role)
        if pool is not None and pool.offer(person):
            return

    def release(self, person: Person):
        """SimPy generator: a technician is done; he takes the next call or walks back to his base."""
        person.target = None
        pool = self.pools[person.role]
        person.busy = False
        if person.shift != self.current_shift and person.on_site:
            self.env.process(self._leave(person))
            return
        if pool.offer(person):
            return
        person.act = "walk"
        if person.carry == "meter":                                  # the instruments go back to the cabinet
            yield from self.walk(person, "instr")
            yield self.env.timeout(PUT_BACK_S)
        yield from self.walk(person, person.base)
        person.carry = None
        person.act = "idle"
        if person.shift == self.current_shift:
            self._became_free(person)
        elif person.on_site:
            self.env.process(self._leave(person))

    # ----- the foreman
    # ----- the andon: escalation of a breakdown, and the shift meeting
    def level(self, call: Call) -> int:
        """Level of the andon of a call: 1 the technician, 2 the foreman (after foreman_min), 3 the
        production manager (after manager_min). Only breakdowns escalate."""
        if call.kind != "repair":
            return 1
        age = self.env.now - call.since
        rule = self.config.andon
        return 3 if age >= rule.manager_min * 60 else 2 if age >= rule.foreman_min * 60 else 1

    def _escalated(self, call: Call, level: int, who: Person | None) -> None:
        self.escalations.append({"st": call.station, "lv": level, "t": round(self.env.now, 1), "r": call.reason,
                                 "p": who.name if who is not None else None,
                                 "how": "walk" if who is not None else "phone"})

    def _meeting(self, person: Person, rule_end: float):
        """SimPy generator: stand at the shift meeting in front of the andon board until it ends."""
        spot = self._grab("meet", self.n_meet, person, ordered=True)
        person.act = "walk"
        yield from self.walk(person, spot)
        person.act = "meeting"
        if self.env.now < rule_end:
            yield self.env.timeout(rule_end - self.env.now)
        self._drop(spot, person)

    def _meeting_times(self, start: float) -> tuple[float, float]:
        rule = self.config.andon
        t0 = start + rule.meeting_at_min * 60
        return t0, t0 + rule.meeting_min * 60

    def _join_meeting(self, person: Person, shift: str, start: float):
        """SimPy process (maintenance, IT): to the shift meeting if free; a call takes them out of it."""
        t0, t1 = self._meeting_times(start)
        if self.env.now > t0 or not self.n_meet:
            return
        yield self.env.timeout(t0 - self.env.now)
        if self.current_shift != shift or not (person.on_site and person.available and person.place == person.base):
            return
        person.available = False
        spot = self._grab("meet", self.n_meet, person, ordered=True)
        person.act = "walk"
        yield from self.walk(person, spot)
        person.act = "meeting"
        self._became_free(person)                                # a breakdown calls them away from the meeting
        while self.env.now < t1 and person.available and person.place == spot:
            yield self.env.timeout(5)
        self._drop(spot, person)
        if person.available and person.place == spot:           # the meeting is over: back to the desk
            person.available = False
            person.act = "walk"
            yield from self.walk(person, person.base)
            person.act = "idle"
            if person.shift == self.current_shift:
                self._became_free(person)

    def _arrived(self, person: Person, shift: str):
        """SimPy generator: until the person of a new shift is in and at the base (the routines of a shift
        start when its people are still on the way in)."""
        while self.current_shift == shift and not (person.on_site and person.arrived):
            yield self.env.timeout(10)

    def _foreman(self, person: Person, shift: str, start: float, end: float):
        yield from self._arrived(person, shift)
        lines = [c for c in self.layout.lines if self.layout.lines[c]["kind"] in ("SMT", "FA", "PACK")]
        turn = 0
        next_round = max(self.env.now, start + 30 * 60)
        todo = [(start + (rule.start + 40) * 60, rule) for rule in self.config.breaks if start + (rule.start + 40) * 60 > self.env.now]
        meet_at, meet_end = self._meeting_times(start)
        met = self.env.now > meet_at or not self.n_meet
        while self.current_shift == shift and person.on_site and self.env.now < end - 20 * 60:
            if self.foreman_calls:                                    # called from the MES
                call, work_s = self.foreman_calls.popleft()
                yield from self._answer(person, call, work_s, "supervise")
                continue
            if not met and self.env.now >= meet_at:                   # the shift meeting: he leads it
                met = True
                yield from self._meeting(person, meet_end)
                continue
            long = [c for c in self.calls if c.kind == "repair" and not c.visited and self.level(c) >= 2]
            if long:                                                  # the andon: level 2, the foreman comes
                call = long[0]
                call.visited = True
                self._escalated(call, 2, person)
                person.act = "walk"
                yield from self.walk(person, f"hand:spot:{call.station}")
                person.act, person.target = "supervise", call.station
                yield self.env.timeout(SUPERVISE_S)
                person.target = None
            elif todo and self.env.now >= todo[0][0]:
                rule = todo.pop(0)[1]
                yield from self._rest(person, rule.length * 60, person.base, rule.kind)
                person.act = "idle"
            elif self.env.now >= next_round:
                for _ in range(4):
                    code = lines[turn % len(lines)]
                    turn += 1
                    first = self.layout.lines[code]["cells"][0]["stations"][0]
                    person.act = "walk"
                    yield from self.walk(person, f"hand:spot:{first}")
                    person.act, person.target = "supervise", first
                    yield self.env.timeout(GEMBA_STOP_S)
                    person.target = None
                next_round = self.env.now + GEMBA_EVERY_S
            else:
                if person.place != person.base:
                    person.act = "walk"
                    yield from self.walk(person, person.base)
                person.act = "idle"
                yield self.env.timeout(30)

    def _manager(self, person: Person, shift: str, start: float, end: float):
        """SimPy process: the production manager. At his desk in the production office; at the shift
        meeting of the morning; the third level of the andon: a breakdown open for an hour brings him to
        the machine (once the foreman has gone back), to decide what happens next."""
        yield from self._arrived(person, shift)
        todo = [(start + (rule.start + 55) * 60, rule) for rule in self.config.breaks if start + (rule.start + 55) * 60 > self.env.now]
        meet_at, meet_end = self._meeting_times(start)
        met = self.env.now > meet_at or not self.n_meet
        while self.current_shift == shift and person.on_site and self.env.now < end - 20 * 60:
            if not met and self.env.now >= meet_at:
                met = True
                yield from self._meeting(person, meet_end)
                continue
            urgent = [c for c in self.calls if c.kind == "repair" and not c.managed and self.level(c) >= 3]
            if urgent:
                call = urgent[0]
                call.managed = True
                self._escalated(call, 3, person)
                person.act = "walk"
                yield from self.walk(person, f"hand:spot:{call.station}")
                person.act, person.target = "supervise", call.station
                yield self.env.timeout(MANAGER_STAY_S)
                person.target = None
            elif todo and self.env.now >= todo[0][0]:
                rule = todo.pop(0)[1]
                yield from self._rest(person, rule.length * 60, person.base, rule.kind)
                person.act = "idle"
            else:
                if person.place != person.base:
                    person.act = "walk"
                    yield from self.walk(person, person.base)
                person.act = "idle"
                yield self.env.timeout(30)

    def _phone_escalations(self):
        """SimPy process: a breakdown that reaches the third level while the production manager is not on
        site (afternoon, night): he is called on the phone (logged, nobody walks)."""
        while True:
            yield self.env.timeout(60)
            here = any(p.on_site and p.role == MANAGER for p in self.persons.values())
            if here:
                continue
            for call in self.calls:
                if call.kind == "repair" and not call.managed and self.level(call) >= 3:
                    call.managed = True
                    self._escalated(call, 3, None)

    # ------------------------------------------------------------------ what stations ask for
    def attend(self, station: Station, duration: float, kind: str = "minor", on_start=None):
        """SimPy generator: the operator of the station's seat comes (one machine at a time),
        works `duration` seconds, and goes back. While the operator is away (toilet) the machine waits."""
        seat = self.seat_of.get(station.code)
        if seat is None:
            yield self.env.timeout(duration)
            return
        job = Attendance(kind)
        station.attendance = job
        waited = 0.0
        while True:
            with seat.busy.request() as req:
                yield req
                person = seat.occupant
                if person is not None or waited >= ATTEND_WAIT_S:
                    break
            yield self.env.timeout(10)                    # nobody sits there right now: wait for her
            waited += 10
        if person is None:
            job.ph = "fix"
            if on_start:
                on_start()
            yield self.env.timeout(duration)
        else:
            with seat.busy.request() as req:
                yield req
                person = seat.occupant or person
                spot = f"spot:{station.code}"
                job.person = person
                for _ in range(90):                       # just taken over: the colleague first steps away
                    if not (person.place == seat.handover and person.walk is None and person.seat is seat):
                        break
                    yield self.env.timeout(1.0)
                if person.place != spot:
                    job.ph = "walk"
                    person.act = "walk"
                    yield from self.walk(person, spot)
                job.ph = "fix"
                person.act, person.target = "fix", station.code
                if on_start:
                    on_start()
                yield self.env.timeout(duration)
                person.target = None
                person.act = "post"
                if person.place != seat.home and not seat.busy.queue:
                    person.act = "walk"
                    yield from self.walk(person, seat.home)
                    person.act = "post"
        station.attendance = None

    def service(self, station: Station, reason: str, work_s: float, kind: str = "repair"):
        """SimPy generator: a technician repairs / services the machine. The call is open
        from now until he is done; it includes waiting for a free technician and his walk."""
        spec = self.model.downtime_reasons.get(reason)
        role = (spec.crew if spec is not None and spec.crew else MAINTENANCE)
        pool = self.pools[role]
        call = Call(station.code, reason, role, kind, self.env.now)
        self.calls.append(call)
        station.call = call
        place = f"tech:{station.code}"                               # beside the operator's place
        person = yield from pool.acquire(place)
        call.person = person
        call.ph = "walk"
        person.act = "walk"
        person.task = station.code
        if role == IT:
            person.carry = "laptop"                                  # off the desk and with them
        else:
            if spec is not None and spec.needs == "meter" and person.carry != "meter":
                yield from self.walk(person, "instr")                # the calibrated instruments are in the workshop cabinet
                yield self.env.timeout(FETCH_S)
                person.carry = "meter"
            elif person.carry is None:
                person.carry = "tools"
        yield from self.walk(person, place)
        call.ph = "fix"
        person.act, person.target = "fix", station.code
        yield self.env.timeout(work_s)
        self.calls.remove(call)
        station.call = None
        yield from self.release(person)

    # ------------------------------------------------------------------ calls from the MES
    def request(self, spec: dict) -> Call | None:
        """Somebody of a department is called to a machine from the MES (e.g. to try the crew out):
        the nearest free person of that role comes, stays `minutes`, and goes back. The machine
        does not stop. Returns the call (on the andon board until it is done)."""
        role = str(spec.get("role", "")).upper()
        code = spec.get("station")
        if role not in REQUEST_ROLES or code not in self.plant.by_code:
            return None
        work_s = max(60.0, min(float(spec.get("minutes") or 10) * 60, 4 * 3600.0))
        call = Call(code, "REQUEST", role, "request", self.env.now, rid=spec.get("id"), by=spec.get("by"))
        self.calls.append(call)
        if role == FOREMAN:
            self.foreman_calls.append((call, work_s))
        else:
            self.env.process(self._request(call, work_s, bool(spec.get("meter"))))
        return call

    def _request(self, call: Call, work_s: float, meter: bool):
        person = yield from self.pools[call.role].acquire(f"tech:{call.station}")
        person.task = call.station
        if call.role == IT:
            person.carry = "laptop"
        elif call.role == MAINTENANCE:
            if meter and person.carry != "meter":
                call.person, call.ph = person, "walk"
                person.act = "walk"
                yield from self.walk(person, "instr")
                yield self.env.timeout(FETCH_S)
                person.carry = "meter"
            elif person.carry is None:
                person.carry = "tools"
        yield from self._answer(person, call, work_s, "fix")
        yield from self.release(person)

    def _answer(self, person: Person, call: Call, work_s: float, act: str):
        """SimPy generator: walk to the machine of a call, stay there, close the call."""
        call.person, call.ph = person, "walk"
        person.act = "walk"
        yield from self.walk(person, f"hand:spot:{call.station}" if act == "supervise" else f"tech:{call.station}")
        call.ph = "fix"
        arrived = self.env.now
        person.act, person.target = act, call.station
        yield self.env.timeout(work_s)
        person.target = None
        person.act = "idle"
        if call in self.calls:
            self.calls.remove(call)
        self.requests_done.append({"id": call.rid, "st": call.station, "role": call.role[:3], "p": person.name,
                                   "t": round(call.since, 1), "walk_s": round(arrived - call.since, 1),
                                   "stay_s": round(self.env.now - arrived, 1)})

    # ------------------------------------------------------------------ the repair bench
    def _seated(self, seat: Seat):
        """SimPy generator: the seat's busy lock and the person in the seat (waits while the seat is
        empty, e.g. the person is on the toilet). The caller releases the lock."""
        while True:
            req = seat.busy.request()
            yield req
            if seat.occupant is not None:
                return req, seat.occupant
            seat.busy.release(req)
            yield self.env.timeout(10)

    def bench_fetch(self, station: Station, item):
        """SimPy generator: the repair person walks to the inbound rack, takes the piece, scans it and
        brings it to the bench."""
        seat = self.seat_of.get(station.code)
        rack = self.plant.inrack
        if seat is None:
            if item in rack:
                rack.remove(item)
            station.item = item
            return
        k = station.cell.stations.index(station)
        req, person = yield from self._seated(seat)
        try:
            person.act = "walk"
            yield from self.walk(person, f"rprin:{k}")
            person.act = "take"
            yield self.env.timeout(TAKE_S)
            if item in rack:
                rack.remove(item)
            person.hold, person.hold_at = item, self.env.now
            person.act = "walk"
            yield from self.walk(person, seat.home)
            person.hold = None
            station.item = item
            person.act = "post"
        finally:
            seat.busy.release(req)

    def bench_put(self, station: Station, item, dest):
        """SimPy generator: after the repair the piece goes to the outbound rack (a robot takes it
        where the MES sends it) or, scrapped, into the scrap bin."""
        seat = self.seat_of.get(station.code)
        fleet = self.plant.fleet
        if seat is None:
            station.item = None
            if dest is not None:
                fleet.pickup("rpr:out", item, dest)
            return
        k = station.cell.stations.index(station)
        req, person = yield from self._seated(seat)
        try:
            station.item = None
            person.hold, person.hold_at = item, self.env.now
            person.act = "walk"
            yield from self.walk(person, f"rprout:{k}" if dest is not None else f"scrap:{k}")
            person.act = "put"
            yield self.env.timeout(PUT_S)
            person.hold = None
            if dest is not None:
                fleet.pickup("rpr:out", item, dest)
            person.act = "walk"
            yield from self.walk(person, seat.home)
            person.act = "post"
        finally:
            seat.busy.release(req)

    # ------------------------------------------------------------------ snapshot
    def snapshot(self, describe=None) -> dict:
        now = self.env.now
        keep_s = keep_seconds(self.plant.speed)
        people = []
        counts: dict[str, dict] = {c: {"on_site": 0, "posts": 0, "break": 0, "walking": 0, "roster": 0,
                                       "operators": 0, "technicians": 0, "other": 0}
                                   for c in self.roster}
        lounging = sum(1 for p in self.persons.values() if p.on_site and p.act == "lounge" and p.walk is None)
        for person in self.persons.values():
            c = counts[person.shift]
            c["roster"] += 1
            if not person.on_site:
                continue
            c["on_site"] += 1
            if person.on_break:
                c["break"] += 1
            if person.walk is not None:
                c["walking"] += 1
            if person.seat is not None:
                c["posts"] += 1
            if person.role == OPERATOR:
                c["operators"] += 1
            elif person.role in (MAINTENANCE, IT):
                c["technicians"] += 1
            else:
                c["other"] += 1
            act = person.act
            if act == "lounge":
                act = "cards" if lounging > 1 else "read"   # a game of cards with a colleague, a book alone
            rec: dict = {"i": person.id, "n": person.name, "r": person.role[:3], "sh": person.shift, "a": act}
            if person.sex == "w":
                rec["g"] = "w"
            if person.walk is not None:
                pts, t0, d = person.walk
                rec["w"] = {"p": [[round(x, 1), round(y, 1)] for x, y in pts], "t": round(t0, 1), "d": round(d, 1)}
            trail = [{"p": [[round(x, 1), round(y, 1)] for x, y in pts], "t": round(t0, 1), "d": round(d, 1)}
                     for pts, t0, d in person.trail if t0 + d >= now - keep_s]
            if trail:
                rec["wh"] = trail                         # the walks just before: the page draws the real way
            if person.hold is not None and describe is not None:
                rec["h"] = {**describe(person.hold), "at": round(person.hold_at, 1)}   # a piece in the hands, since
            if person.walk is None:
                x, y = self.xy(person.place)
                rec["x"], rec["y"] = round(x, 1), round(y, 1)
            if person.target:
                rec["s"] = person.target
            if person.esd_ok:
                rec["e"] = "g"                             # passed the ESD gate
            elif person.coat:
                rec["e"] = "c"                             # wears an ESD coat
            if person.carry:
                rec["c"] = person.carry                    # laptop / measuring instruments / tool box
            if person.walk is None:
                place = self.walkways.places.get(person.place)
                if place is not None and place.look is not None:
                    rec["f"] = round(math.degrees(math.atan2(place.look[1] - place.y, place.look[0] - place.x)))   # faces the screen
                if place is not None and place.pc and person.act in ("idle", "post", "desk"):
                    rec["pc"] = 1                          # works at the computer
            if person.seat is not None:
                rec["seat"] = person.seat.key
            people.append(rec)
        calls = [{"st": c.station, "r": c.reason, "role": c.role[:3], "kind": c.kind, "t": round(c.since, 1),
                  "ph": c.ph, "p": c.person.id if c.person else None, "lv": self.level(c),
                  **({"id": c.rid} if c.rid is not None else {}),
                  **({"by": c.by} if c.by else {})} for c in self.calls]
        shifts = {code: {**counts[code], "start": self.shift_times[code][0], "end": self.shift_times[code][1]}
                  for code in counts}
        meeting = any(p.act == "meeting" and p.walk is None and p.on_site for p in self.persons.values())
        return {"people": people, "crew": {"now": self.current_shift, "shifts": shifts, "calls": calls,
                                           "answered": list(self.requests_done)[-6:],
                                           "esc": list(self.escalations)[-12:], "meeting": meeting}}
