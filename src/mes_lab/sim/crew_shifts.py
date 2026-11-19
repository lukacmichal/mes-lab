"""The shifts: arriving, the hand-over, going home.

A part of the Crew (crew.py), which holds the state these methods work on."""

from __future__ import annotations

from .crew_base import (
    ARRIVE_LEAD_S,
    ARRIVE_SPREAD_S,
    FOREMAN,
    HANDOVER_EARLY_S,
    HANDOVER_LATE_S,
    HANDOVER_MAX_S,
    IT,
    LOGISTICS,
    MAINTENANCE,
    MANAGER,
    MATERIAL,
    OPERATOR,
    UNCOAT_S,
    Person,
    Seat,
)


class ShiftsMixin:
    # ------------------------------------------------------------------ shifts
    def _arrive(self, shift: str, instant: bool = False) -> None:
        """People of a shift come on site (at the door; at once to their places when `instant`); the
        absent stay home."""
        ros = self.roster[shift]
        start_t = self.window[0]
        self._plan_shift(shift)
        for person in self._all_in(shift):
            if person.absent:
                person.on_site = False
                person.act = "off"
                continue
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
        if person.shift_seat is not None:
            seat = person.shift_seat
            seat.occupant = person
            person.seat = seat
            self._settle_at(person, seat.home)
            person.act = "post"
        elif person.role == OPERATOR:           # relief: waits at the team desk of the lines
            self._settle_at(person, person.base)
            person.act = "idle"
        else:
            self._settle_at(person, person.base)
            person.available = person.role in (MAINTENANCE, IT, LOGISTICS, MATERIAL)
            person.act = "idle"
        person.esd_ok = person.role in (OPERATOR, MATERIAL)
        person.coat = person.role in (MAINTENANCE, IT, FOREMAN, MANAGER)

    def _take_post(self, person: Person, ros: dict, start_t: float):
        """SimPy process: a person of the new shift comes in some minutes before the shift starts,
        passes the ESD gate (or puts on a coat), walks to the place and takes over when the shift starts."""
        yield self.env.timeout(self.rng.uniform(0.0, ARRIVE_SPREAD_S))
        person.on_site = True
        person.place = "street:log" if person.role == LOGISTICS else "door"
        person.act = "walk"
        yield from self._enter(person)
        if person.shift_seat is not None:
            seat = person.shift_seat
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
            person.available = person.role in (MAINTENANCE, IT, LOGISTICS, MATERIAL)
            if person.available:
                self._became_free(person)

    def _leave(self, person: Person):
        """SimPy process: walk to the exit and go home (when no longer needed)."""
        if person.task == "leaving" or not person.on_site:
            return
        person.task = "leaving"
        while person.busy or person.errand or (person.seat is not None and person.seat.occupant is person):
            yield self.env.timeout(20)
        person.available = False
        person.act = "walk"
        if person.role == LOGISTICS:
            yield from self.walk(person, "street:log")
        else:
            if person.coat:                                # the ESD coat stays in the locker
                for _ in range(60):
                    if self._free("locker", 4):
                        break
                    yield self.env.timeout(3)
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
        for key in self.uncovered:
            self.env.process(self._vacate(self.seats[key], self.window[0]))
        # people of the old shift that hold no seat or call leave now
        for person in self._all_in(outgoing):
            if person.on_site and person.seat is None and not person.busy and person.role != OPERATOR:
                self.env.process(self._retire(person, shift))
            elif person.on_site and person.role == OPERATOR and person.seat is None:
                self.env.process(self._leave(person))
            elif person.role in (FOREMAN, MANAGER) and person.on_site:
                self.env.process(self._retire(person, shift))

    def _vacate(self, seat: Seat, start_t: float):
        """SimPy process: nobody of the new shift can take this seat; its person goes home at the hand-over
        and the seat stays empty for the shift."""
        hand_at = start_t + self.rng.uniform(-HANDOVER_EARLY_S / 2, HANDOVER_LATE_S / 2)
        if hand_at > self.env.now:
            yield self.env.timeout(hand_at - self.env.now)
        with seat.busy.request() as req:
            yield req
            old = seat.occupant
            if old is not None and old.shift != self.current_shift:
                seat.occupant = None
                old.seat = None
                self.env.process(self._leave(old))

    def _retire(self, person: Person, new_shift: str):
        """A technician, IT, the foreman or a logistics worker of the old shift hands over to their own
        successor (the colleague of the next shift in the same place of the roster) around the end of the
        shift, the way the operators hand over their seats, and goes home. Somebody who comes late is
        waited for, at most HANDOVER_MAX_S past the end of the shift; work in hand is finished first."""
        person.available = False
        mine = [p for p in self._all_in(person.shift) if p.role == person.role]
        theirs = [p for p in self._all_in(new_shift) if p.role == person.role and not p.absent]
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
