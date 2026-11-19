"""The people of the plant: roles, timings, names, and what the crew keeps about a person, a seat, a call
(see crew.py for how they live)."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import simpy

if TYPE_CHECKING:
    from .crew import Crew
    pass

OPERATOR, MAINTENANCE, IT, FOREMAN, LOGISTICS = "OPERATOR", "MAINTENANCE", "IT", "FOREMAN", "LOGISTICS"
MANAGER = "MANAGER"           # the production manager (morning shift): the third level of the andon
MATERIAL = "MATERIAL"         # the material handler: goods-in of the SMT reels and the milk run (receiving.py)
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
MAX_ROUND = 8                 # a relief person covers the breaks of at most this many seats
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
                 "trail", "hold", "sex", "idle_since", "arrived", "hold_at", "skills", "absent", "shift_seat",
                 "errand", "revoked")

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
        self.skills: dict[str, float | None] = {}               # skill -> certificate valid until (sim s), None: no expiry
        self.revoked: set[str] = set()                          # taken away in the MES: no course renews them
        self.absent: str | None = None                          # this shift: "sick" | "holiday" | "training"
        self.shift_seat: Seat | None = None                     # the seat this person holds this shift
        self.errand = False                                     # a relief person on a check: the rounds wait for it


class Seat:
    def __init__(self, env: simpy.Environment, key: str, spec: dict):
        self.key = key
        self.line = spec["line"]
        self.code = spec["code"]
        self.area = spec["area"]
        self.home = spec["home"]
        self.handover = spec.get("handover") or spec["home"]     # where the next person waits for the seat
        self.fixed = spec["fixed"]
        self.skill: str = spec.get("skill") or spec["area"]
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
