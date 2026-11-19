"""What the stations ask the people for, the calls made in the MES, the repair bench.

A part of the Crew (crew.py), which holds the state these methods work on."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .core import State
from .crew_base import (
    ATTEND_WAIT_S,
    FETCH_S,
    FOREMAN,
    IT,
    MAINTENANCE,
    PUT_S,
    REQUEST_ROLES,
    TAKE_S,
    Attendance,
    Call,
    Person,
    Seat,
)

if TYPE_CHECKING:
    from .station import Station


class CallsMixin:
    # ------------------------------------------------------------------ what stations ask for
    def fetch(self, station: Station, place: str, carry: str, take, check, work_s: float):
        """SimPy generator: the operator of the station's seat fetches material for it (SMT material, see
        material.py): walks to `place` (the reel rack of the line, the thaw cabinet), takes what `take()`
        gives (a function or a generator), carries it to the machine and scans it there; `check(thing)` (a
        generator) says whether the MES accepts it, if not the person goes back for the right one. Then
        `work_s` at the machine (a splice: the machine places on meanwhile; a jar: the printer waits) and
        back to the seat. Waits while nobody holds the seat. Returns what was taken."""
        seat = self.seat_of.get(station.code)
        if seat is None:
            return (yield from self._fetch(None, station, place, carry, take, check, work_s))
        while True:
            with seat.busy.request() as req:
                yield req
                person = seat.occupant                    # (in the same hold of the seat, see attend)
                if person is not None:
                    got = yield from self._fetch(person, station, place, carry, take, check, work_s)
                    if person.place != seat.home and not seat.busy.queue:
                        person.act = "walk"
                        yield from self.walk(person, seat.home)
                        person.act = "post"
                    return got
            yield self.env.timeout(10)

    def _fetch(self, person: Person | None, station: Station, place: str, carry: str, take, check, work_s: float):
        refused = 0
        while True:
            if refused >= 2:                              # refused again and again: a closer look at the rack
                yield self.env.timeout(60.0 * min(refused, 10))
            if person is not None:
                person.act = "walk"
                yield from self.walk(person, place)
                person.act = "take"
            got = take()
            if hasattr(got, "send"):                     # a generator: it may wait (a jar still thawing)
                got = yield from got
            yield self.env.timeout(TAKE_S)
            if person is not None:
                person.carry = carry
                person.act = "walk"
                yield from self.walk(person, f"spot:{station.code}")
                person.act, person.target = "fix", station.code
            if (yield from check(got)):
                break
            refused += 1
        yield self.env.timeout(work_s)
        if person is not None:
            person.carry, person.target = None, None
            person.act = "post"
        return got

    def attend(self, station: Station, duration: float, kind: str = "minor", on_start=None, act: str = "fix"):
        """SimPy generator: the operator of the station's seat comes (one machine at a time),
        works `duration` seconds (`act`: "fix", "setup" for a changeover), and goes back. While the operator
        is away (toilet) the machine waits."""
        seat = self.seat_of.get(station.code)
        if seat is None:
            yield self.env.timeout(duration)
            return
        job = Attendance(kind)
        station.attendance = job
        waited = 0.0
        while True:
            req = seat.busy.request()
            yield req
            person = seat.occupant                        # (in the same hold of the seat: nobody leaves it meanwhile)
            if person is not None:
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
                person.act, person.target = act, station.code
                if on_start:
                    on_start()
                yield self.env.timeout(duration)
                person.target = None
                person.act = "post"
                if person.place != seat.home and not seat.busy.queue:
                    # back to the seat, still holding it (the next call waits for the walk as before), while
                    # the machine goes on already
                    self.env.process(self._back_to_seat(seat, req, person))
                else:
                    seat.busy.release(req)
                break
            seat.busy.release(req)
            if waited >= ATTEND_WAIT_S:                   # nobody came: the machine gets on with it alone
                job.ph = "fix"
                if on_start:
                    on_start()
                yield self.env.timeout(duration)
                break
            yield self.env.timeout(10)                    # nobody sits there right now: wait for her
            waited += 10
        station.attendance = None

    def _back_to_seat(self, seat, req, person: Person):
        """SimPy process: after the work at a machine the operator walks back to the seat; the hold of the seat
        (`req`) is handed over from `attend` and released at the seat."""
        try:
            person.act = "walk"
            yield from self.walk(person, seat.home)
            person.act = "post"
        finally:
            seat.busy.release(req)

    def staffed(self, station: Station):
        """SimPy generator: a machine worked by hand (loading, inspection, packing, repair) waits while
        nobody sits at it (an empty seat of the shift, a break without a relief, the toilet)."""
        seat = self.seat_of.get(station.code)
        if seat is None or not station.type.manual:
            return
        waited = False
        while seat.occupant is None:
            if not waited:
                station.set_state(State.STARVED, "NO_OPERATOR")
                waited = True
            yield self.env.timeout(10)

    def operator_at(self, station: Station) -> Person | None:
        """The person logged in at a machine: the one who holds its seat right now."""
        seat = self.seat_of.get(station.code)
        return seat.occupant if seat is not None else None

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

    def _fixes_storm(self, call: Call) -> None:
        """Maintenance or IT called to an AOI in a false call storm (a training scenario) re-teaches it."""
        if call.role in (MAINTENANCE, IT) and self.plant.fault_on("AOI_STORM", call.station):
            self.plant.end_fault(f"AOI_STORM:{call.station}", f"fixed by {call.role.lower()}")

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
        self._fixes_storm(call)
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
