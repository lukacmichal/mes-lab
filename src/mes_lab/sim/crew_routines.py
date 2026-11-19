"""What people do in a shift besides their machine: rounds, breaks, the toilet, the technicians'
and the logistics' day, the foreman, the escalation of the andon and the shift meeting.

A part of the Crew (crew.py), which holds the state these methods work on."""

from __future__ import annotations

import simpy

from .core import State
from .crew_base import (
    GEMBA_EVERY_S,
    GEMBA_STOP_S,
    IT,
    LOGISTICS,
    MAINTENANCE,
    MANAGER,
    MANAGER_STAY_S,
    MAX_ROUND,
    OPERATOR,
    PUT_BACK_S,
    SUPERVISE_S,
    Call,
    Person,
    Seat,
)


class RoutinesMixin:
    # ------------------------------------------------------------------ routines of a shift
    def _start_routines(self, shift: str) -> None:
        ros = self.roster[shift]
        start, end = self.window
        # breaks of the operators: the relief people left over go round the seats (at most MAX_ROUND each);
        # the operators of the seats beyond that take their breaks without a relief (the machine waits)
        keys = [k for k in self.seat_order if k in self.holder]
        rounds = self._break_rounds(keys, self.break_reliefs)
        for relief, seats in rounds.items():
            if seats:
                self.env.process(self._relief_rounds(relief, seats, shift, start, end))
        covered = {s.key for seats in rounds.values() for s in seats}
        self.solo = [k for k in keys if k not in covered]
        for key in self.solo:
            self.env.process(self._solo_breaks(self.holder[key], self.seats[key], shift, start, end))
        def came(people: list[Person]) -> list[Person]:
            return [p for p in people if not p.absent]
        for role in ("maintenance", "it", "logistics"):
            for k, person in enumerate(ros[role]):
                pool = self.pools[{"maintenance": MAINTENANCE, "it": IT, "logistics": LOGISTICS}[role]]
                if person not in pool.persons:
                    pool.persons.append(person)
                if not person.absent:
                    self.env.process(self._technician_breaks(person, k, shift, start, end))
        for person in came(ros["foreman"]):
            self.env.process(self._foreman(person, shift, start, end))
        for person in came(ros["manager"]):
            self.env.process(self._manager(person, shift, start, end))
        for person in came(ros["maintenance"] + ros["it"]):
            self.env.process(self._join_meeting(person, shift, start))
        if self.n_lounge:
            for person in came(ros["logistics"]):
                self.env.process(self._lounge(person, shift))
        for k, person in enumerate(came(ros["material"])):
            self.env.process(self._technician_breaks(person, k + 1, shift, start, end))
            self.env.process(self._material_handler(person, shift))
        for person in list(self.holder.values()) + came(ros["maintenance"] + ros["it"] + ros["material"]):
            self.env.process(self._wc_trips(person, shift, start, end))
        rounds = self.config.rounds
        if rounds.operator_min > 0:
            for key in keys:                                 # the desk seats check the machines of their line
                if not self.seats[key].fixed:
                    self.env.process(self._operator_rounds(self.seats[key], shift, start, end))
            for relief in self.break_reliefs:                # relief people between their break rounds
                self.env.process(self._relief_checks(relief, shift, start, end))
        if rounds.maintenance_min > 0:
            for k, person in enumerate(came(ros["maintenance"])):
                self.env.process(self._inspection_rounds(person, k, shift, start, end))

    def _material_handler(self, person: Person, shift: str):
        """SimPy process: the work of a material handler (receiving.py; the plant builds it after the crew)."""
        yield self.env.timeout(0)
        yield from self.plant.receiving.work(person, shift)

    # ----- rounds between the calls (config crew.rounds)
    def _calm(self, code: str) -> bool:
        """A machine one may stand at for a check: no breakdown, no call open at it."""
        station = self.plant.by_code.get(code)
        return (station is not None and station.call is None and station.state not in (State.DOWN, State.MAINTENANCE)
                and not any(c.station == code for c in self.calls))

    def _operator_rounds(self, seat: Seat, shift: str, start: float, end: float):
        """SimPy process: whoever sits in a desk seat goes now and then to a machine of the seat and checks it
        (state, feeders, trays, a look at the screen; a handheld terminal). It holds the seat like a minor stop
        does: the stops, a break or the hand-over wait for it, and it goes back unless the next one is waiting."""
        rule = self.config.rounds
        rng = self.round_rng
        every, check_s = rule.operator_min * 60, rule.check_min * 60
        yield self.env.timeout(max(0.0, start + 20 * 60 - self.env.now) + rng.uniform(0, every))
        while self.current_shift == shift and self.env.now < end - 20 * 60:
            codes = [c for c in seat.attends if f"spot:{c}" != seat.home and self._calm(c)]
            if codes:
                code = rng.choice(codes)
                with seat.busy.request() as req:
                    yield req
                    person = seat.occupant
                    if person is not None and not person.on_break and self.current_shift == shift and self._calm(code):
                        person.act = "walk"
                        yield from self.walk(person, f"spot:{code}")
                        person.act, person.target = "check", code
                        until = self.env.now + check_s * rng.uniform(0.7, 1.3)
                        while self.env.now < until and not seat.busy.queue:
                            yield self.env.timeout(2)     # a machine that stops, a break, the hand-over: at once
                        person.act, person.target = "post", None
                        if person.place != seat.home and not seat.busy.queue:
                            person.act = "walk"
                            yield from self.walk(person, seat.home)
                            person.act = "post"
            yield self.env.timeout(every * rng.uniform(0.7, 1.3))

    def _check_codes(self, line: str) -> list[str]:
        """The machines of the desk seats of a line a relief person may check, from the place beside the operator's
        (not the machines whose place is a seat, not the first of the line where the foreman stops)."""
        homes = {seat.home for seat in self.seats.values()}
        cells = self.layout.lines.get(line, {}).get("cells") or []
        first = cells[0]["stations"][0] if cells and cells[0]["stations"] else None
        return [c for seat in self.seats.values() if seat.line == line and not seat.fixed
                for c in seat.attends if f"spot:{c}" not in homes and c != first]

    def _relief_checks(self, relief: Person, shift: str, start: float, end: float):
        """SimPy process: a relief person between the break rounds goes along the line they wait at and checks a
        machine, from beside the operator's place, then back to the team desk. Not near the start of a round
        (the round waits for a check that runs, see _relief_rounds), not at the end of the shift."""
        rule = self.config.rounds
        rng = self.round_rng
        every, check_s = rule.operator_min * 60, rule.check_min * 60
        parts = relief.base.split(":")
        codes = self._check_codes(parts[1]) if parts[0] == "reserve" and len(parts) == 3 else []
        if not codes:
            return
        rounds = [start + b.start * 60 for b in self.config.breaks]
        yield from self._arrived(relief, shift)
        yield self.env.timeout(rng.uniform(0, every))
        while self.current_shift == shift and self.env.now < end - 30 * 60:
            now = self.env.now
            near = any(r - 12 * 60 <= now <= r + 5 * 60 for r in rounds)
            ready = (relief.on_site and relief.seat is None and not relief.on_break and relief.task is None
                     and relief.walk is None and relief.place == relief.base and relief.act == "idle")
            free = [c for c in codes if self._calm(c) and f"chk:{c}" not in self.held]
            if near or not ready or not free:
                yield self.env.timeout(60)
                continue
            code = rng.choice(free)
            key = f"chk:{code}"
            self.held[key] = relief.id
            relief.errand = True
            relief.act = "walk"
            yield from self.walk(relief, f"hand:spot:{code}")
            relief.act, relief.target = "check", code
            until = self.env.now + check_s * rng.uniform(0.7, 1.3)
            while self.env.now < until and self.current_shift == shift and self._calm(code):
                yield self.env.timeout(5)                 # a breakdown there: the technician and the foreman come
            relief.target = None
            self._drop(key, relief)
            if self.current_shift == shift and relief.on_site:
                relief.act = "walk"
                yield from self.walk(relief, relief.base)
            relief.act = "idle"
            relief.errand = False
            yield self.env.timeout(every * rng.uniform(0.7, 1.3))

    def _inspection_rounds(self, person: Person, k: int, shift: str, start: float, end: float):
        """SimPy process: a maintenance technician walks an inspection round along one line now and then (noise,
        leaks, filters, lubrication): a few machines, some minutes each, from the technician's place beside the
        operator's. Free all the time at the machines: a call takes them from there at once (as at the meeting)."""
        rule = self.config.rounds
        rng = self.round_rng
        every, look_s = rule.maintenance_min * 60, rule.inspect_min * 60
        lines = [c for c, info in self.layout.lines.items() if info["kind"] in ("SMT", "FA", "PACK")]
        yield from self._arrived(person, shift)
        yield self.env.timeout(max(0.0, start + (40 + 15 * k) * 60 - self.env.now))
        while self.current_shift == shift and self.env.now < end - 45 * 60:
            if not (lines and person.on_site and person.available and person.place == person.base and person.walk is None):
                yield self.env.timeout(60)
                continue
            line = rng.choice(lines)
            codes = [c for cell in self.layout.lines[line]["cells"] for c in cell["stations"]]
            stops = sorted(rng.sample(codes, min(rule.stops, len(codes))), key=codes.index)   # along the line
            taken = False
            for code in stops:
                if not self._calm(code) or f"chk:tech:{code}" in self.held:
                    continue
                spot = f"tech:{code}"
                person.available = False
                person.act = "walk"
                yield from self.walk(person, spot)
                if self.current_shift != shift or person.task == "leaving":
                    taken = True
                    break
                if not self._calm(code):
                    continue
                self.held[f"chk:tech:{code}"] = person.id
                person.act = "inspect"
                self._became_free(person)                         # a call takes them from here
                until = self.env.now + look_s * rng.uniform(0.7, 1.3)
                while self.env.now < until and person.available and person.place == spot and self._calm(code):
                    yield self.env.timeout(5)                     # (a call of IT there: they step away)
                self._drop(f"chk:tech:{code}", person)
                if not (person.available and person.place == spot):
                    taken = True                                  # a call or the break took them
                    break
            if not taken and person.on_site and self.current_shift == shift:
                person.available = False
                person.act = "walk"
                yield from self.walk(person, person.base)
                person.act = "idle"
                self._became_free(person)
            yield self.env.timeout(every * rng.uniform(0.7, 1.3))

    # ----- operator breaks
    def _break_rounds(self, keys: list[str], reliefs: list[Person]) -> dict[Person, list[Seat]]:
        """Which seats every relief person goes round on a break: only seats they are qualified for, at
        most MAX_ROUND and about the same number each. The machine seats are given out first (a desk seat
        without a relief only waits for its minor stops); a relief takes the seats of their own round
        first, then those on the line of the seat before (a short way round)."""
        out: dict[Person, list[Seat]] = {r: [] for r in reliefs}
        if not reliefs:
            return out
        cap = min(MAX_ROUND, -(-len(keys) // len(reliefs)))
        until = self.plan_until
        for key in sorted(keys, key=lambda k: (not self.seats[k].fixed, self.seat_order.index(k))):   # machine seats first
            seat = self.seats[key]
            able = [r for r in reliefs if len(out[r]) < cap and self.qualified(r, seat.skill, until)]
            if not able:
                continue

            def far(r: Person) -> tuple:
                last = out[r][-1] if out[r] else None
                return (key not in self.relief_keys.get(r.id, ()),             # the seats of their own round first
                        last is not None and last.line != seat.line, len(out[r]))
            out[min(able, key=far)].append(seat)
        for seats in out.values():
            seats.sort(key=lambda st: self.seat_order.index(st.key))              # the way round
        return out

    def _relief_rounds(self, relief: Person, seats: list[Seat], shift: str, start: float, end: float):
        for rule in self.config.breaks:
            round_start = start + rule.start * 60
            if round_start < self.env.now - 5 * 60:
                continue                                              # this round was before the plant began
            yield self.env.timeout(max(0.0, round_start - self.env.now))
            for seat in seats:
                if self.current_shift != shift or self.env.now + rule.length * 60 + 180 > end:
                    break
                primary = self.holder.get(seat.key)
                if primary is None or not primary.on_site:
                    continue
                while relief.errand:                                  # on a check at a machine: it ends first
                    yield self.env.timeout(5)
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
                    yield self.env.timeout(0.05)                      # the colleague sets off first ...
                    yield from self._steps_aside(relief, primary, seat.home)
                    yield from self.walk(relief, seat.home)           # ... then the relief sits in (holding the
                                                                      # seat: no machine calls them away meanwhile)
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

    def _solo_breaks(self, person: Person, seat: Seat, shift: str, start: float, end: float):
        """SimPy process: an operator whose seat no relief person covers takes the breaks anyway; the seat
        is empty meanwhile (the machines that need the person wait)."""
        yield from self._arrived(person, shift)
        for rule in self.config.breaks:
            at = start + (rule.start + self.rng.uniform(0, 40)) * 60
            if at < self.env.now - 5 * 60:
                continue
            yield self.env.timeout(max(0.0, at - self.env.now))
            if self.current_shift != shift or self.env.now + rule.length * 60 + 180 > end:
                return
            while person.task == "wc":
                yield self.env.timeout(10)
            with seat.busy.request() as req:
                yield req
                if seat.occupant is not person:
                    continue
                seat.occupant = None
                person.seat = None
                person.on_break = True
            returned = self.env.event()
            self.env.process(self._take_break(person, seat, rule, returned))
            yield returned
            with seat.busy.request() as req:
                yield req
                if seat.occupant is None and person.shift == self.current_shift:
                    seat.occupant = person
                    person.seat = seat
            if person.seat is seat:
                yield from self.walk(person, seat.home)
                person.act = "post"

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
            seat = person.shift_seat
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
        yield from self._arrived(person, shift)           # the routines start while the shift is still on the way in
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
