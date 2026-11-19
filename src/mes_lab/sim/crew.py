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
* Qualifications and absences: every operator is qualified for the seats of their skill (SMT, final
  assembly, the visual inspection, packing, repair; the inspection and the repair need a certificate that
  runs out). At the start of every shift some people are off (sick, on holiday). The foreman covers their
  seats with qualified relief people, the machine seats first (loading, inspection, packing, repair, where
  nothing works without a person), then the desk seats; a seat nobody qualified can take stays empty for
  the shift. Fewer relief people left over means fewer covered breaks: an operator without a relief takes
  the break anyway and the machine waits.
* A call from the MES (`request`): somebody of a department (maintenance, IT, the foreman,
  logistics) is asked to come to a machine for a while. The nearest free one walks there, works
  and walks back; the machine goes on producing. It is on the andon board like any other call.
* Every walk is kept for a while after it ended (`trail`), so the live view can draw a person
  along the real way even when a snapshot comes only after the next walk has begun.

The class is long, so its methods live in parts by what they are about: crew_skills (qualifications, absences,
cover), crew_walk (places and walking), crew_shifts, crew_routines (rounds, breaks, andon, meetings) and
crew_calls (what stations and the MES ask for, the repair bench); what they share is in crew_base.
"""

from __future__ import annotations

import itertools
import math
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import simpy

from .crew_base import (
    FOREMAN,
    IT,
    LAST,
    LOGISTICS,
    MAINTENANCE,
    MANAGER,
    MATERIAL,
    MEN,
    OPERATOR,
    WOMEN,
    Call,
    Person,
    Pool,
    Seat,
    female,
)
from .crew_calls import CallsMixin
from .crew_routines import RoutinesMixin
from .crew_shifts import ShiftsMixin
from .crew_skills import SkillsMixin
from .crew_walk import WalkMixin
from .traffic import keep_seconds

if TYPE_CHECKING:
    from .plant import PlantSim


class Crew(SkillsMixin, WalkMixin, ShiftsMixin, RoutinesMixin, CallsMixin):
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
        self.skill_rng = plant.rng_for("skills")
        self.absence_rng = plant.rng_for("absence")
        self.round_rng = plant.rng_for("rounds")          # the rounds between the calls, apart from the rest
        self.holder: dict[str, Person] = {}              # this shift: seat -> the person who holds it
        self.uncovered: list[str] = []                  # this shift: seats nobody qualified could take
        self.plan_until = 0.0                            # this shift: certificates must hold until then
        self.relief_keys: dict[int, list[str]] = {}      # relief person -> the seats of their break round
        self.solo: list[str] = []                        # this shift: seats whose breaks have no relief
        self.break_reliefs: list[Person] = []            # this shift: the relief people left for the breaks
        self.attendance: list[dict] = []                 # who came and who did not, for the MES (taken by the edge)
        self.absent_log: list[tuple[str, int, str]] = []    # (shift, person, why) of every absence
        self.renewals: list[tuple[int, str, float]] = []    # (person, skill, valid until): certificates renewed, for the MES
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
                         "manager": [], "material": []}
            for key in self.seat_order:
                person = self._person(OPERATOR, shift)
                person.home_seat = self.seats[key]
                ros["primaries"][key] = person
            ros["reliefs"] = [self._person(OPERATOR, shift) for _ in range(staff.relief)]
            keys = self.seat_order
            size = -(-len(keys) // max(1, len(ros["reliefs"])))
            for j, relief in enumerate(ros["reliefs"]):
                relief.base = self._reserve_for(keys[j * size:(j + 1) * size], ordinal)
                self.relief_keys[relief.id] = keys[j * size:(j + 1) * size]       # the seats they are trained for
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
        n = 0
        for shift, staff in self.config.shifts.items():   # the material handlers come last, so that the people
            for _ in range(staff.material):                # of the plant before them keep their names
                person = self._person(MATERIAL, shift)
                person.base = f"recv:{n}"
                self.roster[shift]["material"].append(person)
                n += 1
        self._give_skills()

    # ------------------------------------------------------------------ snapshot
    def snapshot(self, describe=None) -> dict:
        now = self.env.now
        keep_s = keep_seconds(self.plant.speed)
        people = []
        counts: dict[str, dict] = {c: {"on_site": 0, "posts": 0, "break": 0, "walking": 0, "roster": 0, "absent": 0,
                                       "operators": 0, "technicians": 0, "other": 0}
                                   for c in self.roster}
        lounging = sum(1 for p in self.persons.values() if p.on_site and p.act == "lounge" and p.walk is None)
        for person in self.persons.values():
            c = counts[person.shift]
            c["roster"] += 1
            if person.absent:
                c["absent"] += 1                          # off in their last shift (sick, on holiday)
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
            if person.skills:
                rec["sk"] = sorted(person.skills)
            people.append(rec)
        calls = [{"st": c.station, "r": c.reason, "role": c.role[:3], "kind": c.kind, "t": round(c.since, 1),
                  "ph": c.ph, "p": c.person.id if c.person else None, "lv": self.level(c),
                  **({"id": c.rid} if c.rid is not None else {}),
                  **({"by": c.by} if c.by else {})} for c in self.calls]
        shifts = {code: {**counts[code], "start": self.shift_times[code][0], "end": self.shift_times[code][1]}
                  for code in counts}
        meeting = any(p.act == "meeting" and p.walk is None and p.on_site for p in self.persons.values())
        cur = self.roster.get(self.current_shift, {})
        absent = [{"i": p.id, "n": p.name, "r": p.role[:3], "why": p.absent} for p in self._all_in(self.current_shift)
                  if p.absent] if cur else []
        return {"people": people, "crew": {"now": self.current_shift, "shifts": shifts, "calls": calls,
                                           "answered": list(self.requests_done)[-6:],
                                           "esc": list(self.escalations)[-12:], "meeting": meeting,
                                           "absent": absent, "uncovered": list(self.uncovered)}}
