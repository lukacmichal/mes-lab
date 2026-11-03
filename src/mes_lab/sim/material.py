"""SMT material on the shop floor: the component reels on the feeders of the placers, the solder paste of
the printers and the moisture-sensitive parts (config/smt_material.yaml).

* reels      - a placer has a feeder for every part its job places. A panel takes `pieces per board x
               boards` from every reel; when a reel has `splice_warn_min` minutes of placing left, the
               operator of the machine fetches the next reel from the rack at the line, scans it and the
               feeder at the machine (the MES checks the part and the floor life) and splices it to the
               running tape: the machine keeps placing. A reel that runs out first stops the machine
               (REEL_EMPTY) until the splice is done.
* changeover - the reels of the old job go back to the rack at the line (a partly used reel is taken first
               next time); the reels of the new one are put on and scanned.
* the rack   - beside a partly used reel the rack keeps a spare (sealed) reel of every part the job of the
               line uses; taking one orders the next from goods-in, a new job orders what is missing, and a
               machine that needs a reel the rack does not have waits for it (see receiving.py).
* paste      - a printer prints from one jar. Empty, or refused by the MES because it is open too long
               (PASTE_EXPIRED), the operator takes the jar that thawed longest from the thaw cabinet, puts
               the next one from the fridge in, scans the jar at the printer (the MES refuses one that did
               not thaw long enough) and changes it; the printer waits meanwhile.
* msl        - a reel of a moisture-sensitive part counts its time out of the dry bag (on the machine and
               in the rack at the line, which is no dry cabinet). The MES refuses one whose floor life ran
               out and has one on a machine taken off (offline the plant watches it itself); such a reel
               is baked and comes back with a new floor life.

Reel IDs are labels the plant prints at goods-in (the run id keeps them unique). The MES hears every scan
synchronously (it may refuse) and the rest with the edge flush: when a reel starts and stops feeding (the
time windows of the genealogy: which boards got parts of which reel) and how much is left.
"""

from __future__ import annotations

import logging
from collections import Counter, deque
from typing import TYPE_CHECKING

from .core import State
from .trace import LocalTrace, MesUnavailable

if TYPE_CHECKING:
    from .core import Job
    from .plant import PlantSim
    from .station import Station

log = logging.getLogger("mes_lab.material")

PICK_WRONG_S = 30.0          # back to the rack for the right reel after the MES refused one
MSL_WATCH_S = 900.0          # offline: how often the plant looks at the floor life of the reels on machines


class Reel:
    __slots__ = ("id", "part", "lot", "qty", "left", "msl", "used_s", "out_since")

    def __init__(self, rid: str, part: str, lot: str, qty: int, msl: str | None):
        self.id = rid
        self.part = part
        self.lot = lot
        self.qty = qty
        self.left = qty
        self.msl = msl
        self.used_s = 0.0                     # floor life used before `out_since`
        self.out_since: float | None = None   # out of the dry bag since (None: still sealed, or baked)

    def floor_used(self, now: float) -> float:
        return self.used_s + (now - self.out_since if self.out_since is not None else 0.0)


class Feeder:
    __slots__ = ("part", "per_panel", "reel", "next", "splicing", "waiting")

    def __init__(self, part: str, per_panel: int):
        self.part = part
        self.per_panel = per_panel           # pieces one panel of the job takes
        self.reel: Reel | None = None        # the running reel
        self.next: Reel | None = None        # spliced to it, runs on when it ends
        self.splicing = False                # an operator is fetching the next reel
        self.waiting: list = []              # a stopped machine waits for the splice

    def pieces(self) -> int:
        return (self.reel.left if self.reel else 0) + (self.next.left if self.next else 0)


class Jar:
    __slots__ = ("id", "lot", "out_at", "opened_at", "prints")

    def __init__(self, jid: str, lot: str, out_at: float, prints: int):
        self.id = jid
        self.lot = lot
        self.out_at = out_at                 # out of the fridge (thawing) since
        self.opened_at: float | None = None  # opened on the printer
        self.prints = prints


class SmtMaterial:
    def __init__(self, plant: PlantSim):
        self.plant = plant
        self.env = plant.env
        self.cfg = plant.model.smt_material
        self.parts = self.cfg.parts
        self.rng = plant.rng_for("smt-material")
        self.tag = plant.run_id.replace("-", "")[:6].upper()
        self.n_reels = 0
        self.n_jars = 0
        self.lots: dict[str, list[int]] = {}           # part -> [lot number, reels left in it]
        self.held_lots: set[str] = set()                # supplier lots on hold in the MES: never on a machine
        self.racks: dict[str, list[Reel]] = {}         # partly used reels at the line, oldest first
        self.spares: dict[str, list[Reel]] = {}        # sealed reels at the line (from goods-in)
        self.waiting: dict[tuple[str, str], list] = {} # (line, part): machines waiting for a reel
        self.supplied = bool(self.cfg.suppliers)       # reels come from goods-in (else from nowhere)
        self.line_parts: dict[str, set[str]] = {}      # the parts of the job each line runs
        self.banks: dict[str, dict[str, Feeder]] = {}  # placer -> part -> feeder
        self.jars: dict[str, Jar | None] = {}          # printer -> the jar on its stencil
        self.cabinet: list[Jar] = []                   # jars thawing, oldest first
        self.baking: list[tuple[float, Reel, str]] = []
        self.events: list[dict] = []                   # for the MES (taken by the edge)
        self.dirty: set[str] = set()                   # reels and jars whose level changed since the last flush
        self.log: deque = deque(maxlen=40)             # the last scans
        self.stats: Counter = Counter()                # splices, empty reels, paste changes, refusals
        self.cycle: dict[str, float] = {}              # placer -> seconds per panel of its job (ideal)
        paste = self.cfg.paste
        for line in plant.lines.values():
            if line.spec.area_kind.value == "SMT":
                self.racks[line.spec.code] = []
                self.spares[line.spec.code] = []
                for st in line.stations:
                    if st.type.code == "PASTE_PRINTER":       # every printer starts with a jar open for a while
                        opened = -self.rng.uniform(0, paste.open_h * 0.7) * 3600
                        jar = self._new_jar(opened - (paste.thaw_h + self.rng.uniform(0.2, 2)) * 3600)
                        jar.opened_at = opened
                        jar.prints = int(paste.prints * self.rng.uniform(0.3, 1.0))
                        self.jars[st.code] = jar
                        self._event("START", jar.id, st, "PASTE", jar.prints, **self._jar_info(jar))
        for k in range(paste.cabinet):                 # the cabinet is full: the first ones thawed
            self.cabinet.append(self._new_jar(-(paste.thaw_h + 3 - k * paste.thaw_h / max(1, paste.cabinet - 2)) * 3600))
        self.cabinet.sort(key=lambda j: j.out_at)
        if isinstance(plant.trace, LocalTrace):
            self.env.process(self._msl_watch())

    # ------------------------------------------------------------------ what a station needs
    def needs(self, station: Station, job: Job | None) -> dict[str, int] | None:
        """Pieces per panel of every part this placer places for the job (None: not a placer)."""
        if job is None or station.type.code not in ("PNP_CHIP", "PNP_IC"):
            return None
        boards = job.material.boards_per_panel
        return {part: qty * boards for part, qty in job.operation.components.items()
                if self.parts[part].machine == station.type.code}

    def job_started(self, line: str, job: Job) -> None:
        """A new job on the line (its loader took it): the rack orders a spare of every part the job places
        that it has none of; at once when there is not even a partly used reel."""
        self.line_parts[line] = set(job.operation.components)
        if not self.supplied:
            return
        for part in job.operation.components:
            if not self._has(line, part):
                self.plant.receiving.order_reel(line, part, urgent=True)

    def _reel_for(self, station: Station, part: str, mistake: bool, first: bool = False):
        """SimPy generator: a reel of the part from the rack at the line; without one the machine waits until
        goods-in brings it (at the start of a run the lines are set up: straight from the store)."""
        line = station.line.code
        while True:
            reel = self._pick(line, part, mistake)
            if reel is not None:
                return reel
            if first or not self.supplied:
                got = self.plant.receiving.take(part) if self.supplied else None
                return got[0] if got else self._new_reel(part)
            self.plant.receiving.order_reel(line, part, urgent=True)
            self.stats["waits for a reel"] += 1
            event = self.env.event()
            self.waiting.setdefault((line, part), []).append(event)
            yield event

    def delivered(self, line: str, reel: Reel) -> None:
        """Goods-in put a reel into the rack of the line."""
        self.spares.setdefault(line, []).append(reel)
        self.events.append({"kind": "AT_LINE", "item": reel.id, "line": line, "at": self.plant.clock.iso(self.env.now)})
        for event in self.waiting.pop((line, reel.part), []):
            event.succeed()

    def setup(self, station: Station, job: Job, first: bool = False):
        """SimPy generator, at a changeover (and the first job): the reels of the old job go back to the
        rack at the line, the new job's are put on the feeders and scanned."""
        need = self.needs(station, job)
        if need is None:
            return
        self.cycle[station.code] = job.steps.get(station.type.code, 30.0)
        bank = self.banks.setdefault(station.code, {})
        line = station.line.code
        for part in [p for p in bank if p not in need]:
            feeder = bank.pop(part)
            for reel in (feeder.reel, feeder.next):
                if reel is not None:
                    self._event("END", reel.id, station, part, reel.left, "CHANGEOVER")
                    self._to_rack(line, reel)
            for event in feeder.waiting:
                event.succeed()
        for part, per in need.items():
            feeder = bank.get(part)
            if feeder is None:
                feeder = bank[part] = Feeder(part, per)
            feeder.per_panel = per
            while feeder.reel is None:
                if not self._has(line, part) and not first:
                    station.set_state(State.STARVED, "NO_MATERIAL")
                reel = yield from self._reel_for(station, part, mistake=True, first=first)
                ok = yield from self._scan(station, part, reel, "LOAD")
                if ok:
                    feeder.reel = reel
                    self._event("START", reel.id, station, part, reel.left)
                else:
                    yield self.env.timeout(PICK_WRONG_S)

    def ready(self, station: Station, job: Job | None):
        """SimPy generator, before a cycle: every reel has enough for the panel (or the machine stops until
        the splice); the printer has paste."""
        if station.type.code == "PASTE_PRINTER":
            jar = self.jars.get(station.code)
            if jar is None or jar.prints <= 0:
                yield from self.change_paste(station, "EMPTY")
            return
        bank = self.banks.get(station.code)
        if not bank:
            return
        for feeder in list(bank.values()):
            while feeder.pieces() < feeder.per_panel and bank.get(feeder.part) is feeder:
                self._want_splice(station, feeder)
                station.set_state(State.MINOR_STOP, "REEL_EMPTY")
                event = self.env.event()
                feeder.waiting.append(event)
                yield event

    def used(self, station: Station, job: Job | None, item=None) -> None:
        """After a cycle: the panel took its pieces (or its print). A reel of a bad supplier lot (a training
        scenario) spoils some of the boards: a latent defect the testers find later, or not."""
        if station.type.code == "PASTE_PRINTER":
            jar = self.jars.get(station.code)
            if jar is not None:
                jar.prints -= 1
                self.dirty.add(jar.id)
            return
        bank = self.banks.get(station.code)
        if not bank:
            return
        bad = self.plant.faults_of("BAD_LOT")
        if bad and item is not None:
            for feeder in bank.values():
                fault = bad.get(feeder.reel.lot) if feeder.reel is not None else None
                if fault:
                    for piece in item.pieces():
                        if self.rng.random() < fault["rate"]:
                            piece.defects.append(fault["defect"])
        cycle = self.cycle.get(station.code, 30.0)
        for feeder in bank.values():
            take = feeder.per_panel
            reel = feeder.reel
            if reel is None:
                continue
            if reel.left < take and feeder.next is not None:       # the tape runs on into the spliced reel
                take -= reel.left
                reel.left = 0
                self._event("END", reel.id, station, feeder.part, 0, "EMPTY")
                self.stats["reels used up"] += 1
                feeder.reel, feeder.next = feeder.next, None
                reel = feeder.reel
                self._event("START", reel.id, station, feeder.part, reel.left)
            reel.left = max(0, reel.left - take)
            self.dirty.add(reel.id)
            if feeder.next is None and not feeder.splicing:
                minutes = reel.left / max(1, feeder.per_panel) * cycle / 60
                if minutes <= self.cfg.reels.splice_warn_min:
                    self._want_splice(station, feeder)

    # ------------------------------------------------------------------ splicing
    def _want_splice(self, station: Station, feeder: Feeder) -> None:
        if feeder.splicing or feeder.next is not None:
            return
        feeder.splicing = True
        self.env.process(self._splice(station, feeder))

    def _splice(self, station: Station, feeder: Feeder):
        line = station.line.code

        def check(reel: Reel):
            need = self.needs(station, station.last_job) or {}
            if self.banks.get(station.code, {}).get(feeder.part) is not feeder or feeder.part not in need:
                return True                            # a changeover takes the feeder off: the reel goes back
            return (yield from self._scan(station, feeder.part, reel, "SPLICE"))

        reel = yield from self.plant.crew.fetch(station, f"reels:{line}", "reel",
                                                lambda: self._reel_for(station, feeder.part, mistake=True),
                                                check, self.cfg.reels.splice_s)
        feeder.splicing = False
        if self.banks.get(station.code, {}).get(feeder.part) is not feeder                 or feeder.part not in (self.needs(station, station.last_job) or {}):
            self._to_rack(line, reel)                  # a changeover took (takes) the feeder off meanwhile
            return
        self.stats["splices"] += 1
        if feeder.reel is None or feeder.reel.left <= 0:
            if feeder.reel is not None:
                self._event("END", feeder.reel.id, station, feeder.part, 0, "EMPTY")
                self.stats["reels used up"] += 1
            feeder.reel = reel
            self._event("START", reel.id, station, feeder.part, reel.left)
        else:
            feeder.next = reel
        for event in feeder.waiting:
            event.succeed()
        feeder.waiting.clear()

    # ------------------------------------------------------------------ reels: the rack, the scan, baking
    def _has(self, line: str, part: str) -> bool:
        return any(r.part == part for r in self.racks.get(line, ())) or any(r.part == part for r in self.spares.get(line, ()))

    def _pick(self, line: str, part: str, mistake: bool = False) -> Reel | None:
        """The reel the operator takes from the rack at the line: now and then the one of the part beside it;
        otherwise a partly used reel of the part first, else its spare (which orders the next one). None:
        the rack has none."""
        rack = self.racks.setdefault(line, [])
        spares = self.spares.setdefault(line, [])
        if mistake and self.rng.random() < self.cfg.reels.wrong_reel_p:
            others = [r for r in rack + spares if r.part != part]
            if others:
                reel = self.rng.choice(others)
                (rack if reel in rack else spares).remove(reel)
                return reel
            if not self.supplied:
                return self._new_reel(self.rng.choice([p for p in self.parts if p != part]))
        for reel in rack:
            if reel.part == part:
                rack.remove(reel)
                return reel
        for reel in spares:
            if reel.part == part:
                spares.remove(reel)
                if self.supplied and part in self.line_parts.get(line, ()):
                    self.plant.receiving.order_reel(line, part, urgent=False)     # the kanban card of the spare
                return reel
        return None if self.supplied else self._new_reel(part)

    def _new_reel(self, part: str) -> Reel:
        spec = self.parts[part]
        self.n_reels += 1
        lot = self.lots.setdefault(part, [0, 0])
        index = list(self.parts).index(part) + 1
        if lot[1] <= 0 or f"L{index:02d}-{lot[0]:04d}" in self.held_lots:   # the next supplier lot (a held
            lot[0] += 1                                                       # one the supplier replaces)
            lot[1] = self.cfg.reels.lot_reels
        lot[1] -= 1
        return Reel(f"RL{self.tag}{self.n_reels:06d}", part, f"L{index:02d}-{lot[0]:04d}", spec.reel, spec.msl)

    def _to_rack(self, line: str, reel: Reel) -> None:
        if reel.left <= 0:
            return
        if reel.out_since is None and reel.left == reel.qty:          # still sealed: a spare again
            self.spares.setdefault(line, []).append(reel)
        else:
            self.racks.setdefault(line, []).append(reel)
        for event in self.waiting.pop((line, reel.part), []):
            event.succeed()

    def _scan(self, station: Station, slot: str, reel: Reel, kind: str):
        """SimPy generator: the operator scans the feeder and the reel; True when the MES accepts it. A
        refused reel goes back to the rack, or to the bake oven when its floor life ran out."""
        verdict = yield from self._ask(station, {
            "kind": kind, "slot": slot, "item": reel.id, "part": reel.part, "lot": reel.lot, "qty": reel.qty,
            "left": reel.left, "msl": reel.msl, "floor_used_s": round(reel.used_s, 1),
            "out_since": self.plant.clock.iso(reel.out_since),
            "job_id": station.last_job.id if station.last_job is not None else None}, reel)
        if verdict.ok:
            if reel.out_since is None:
                reel.out_since = self.env.now          # the dry bag is opened
            return True
        self.stats[f"refused: {verdict.reason}"] += 1
        if verdict.reason == "MSL_EXPIRED":
            self._bake(station.line.code, reel)
        elif verdict.reason == "LOT_ON_HOLD":         # the whole lot goes (the MES also sends HOLD_LOT)
            self.hold_lot(reel.lot)
        else:
            self._to_rack(station.line.code, reel)
        return False

    def _ask(self, station: Station, body: dict, thing):
        attempt = 0
        while True:
            try:
                verdict = self.plant.trace.material_scan(station, body, thing)
                break
            except MesUnavailable:
                yield self.env.timeout(min(60.0, 2.0 * 2 ** attempt))
                attempt += 1
        self.log.append({"t": round(self.env.now, 1), "st": station.code, "k": body["kind"], "i": body["item"],
                         "p": body.get("part"), "ok": verdict.ok, "r": verdict.reason})
        return verdict

    def _bake(self, line: str, reel: Reel) -> None:
        until = self.env.now + self.cfg.msl.bake_h * 3600
        self.baking.append((until, reel, line))
        self.stats["reels baked"] += 1
        self.env.process(self._baked(until, reel, line))

    def _baked(self, until: float, reel: Reel, line: str):
        yield self.env.timeout(until - self.env.now)
        reel.used_s, reel.out_since = 0.0, None        # dry again: a new floor life
        self.baking = [b for b in self.baking if b[1] is not reel]
        self.events.append({"kind": "BAKED", "item": reel.id, "at": self.plant.clock.iso(self.env.now)})
        self._to_rack(line, reel)

    def hold_lot(self, lot: str) -> int:
        """The MES put a supplier lot on hold: its reels come off the machines and out of the racks and the
        store (into quarantine); the feeders get reels of another lot."""
        self.held_lots.add(lot)
        n = 0
        for code, bank in self.banks.items():
            station = self.plant.by_code[code]
            for feeder in bank.values():
                for which in ("next", "reel"):
                    reel = getattr(feeder, which)
                    if reel is None or reel.lot != lot:
                        continue
                    self._event("END", reel.id, station, feeder.part, reel.left, "HOLD")
                    setattr(feeder, which, None)
                    n += 1
                if feeder.reel is None and feeder.next is not None:
                    feeder.reel, feeder.next = feeder.next, None
                    self._event("START", feeder.reel.id, station, feeder.part, feeder.reel.left)
                if feeder.reel is None or feeder.next is None:
                    self._want_splice(station, feeder)
        for shelf in list(self.racks.values()) + list(self.spares.values()):
            keep = [r for r in shelf if r.lot != lot]
            n += len(shelf) - len(keep)
            shelf[:] = keep
        n += self.plant.receiving.quarantine_lot(lot)
        self.stats["reels of a held lot taken away"] += n
        return n

    def release_lot(self, lot: str) -> None:
        self.held_lots.discard(lot)

    def msl_expired(self, reel_id: str) -> bool:
        """The floor life of a reel on a machine ran out (the MES says so, or offline the plant itself):
        it comes off and is baked; the feeder runs on its spliced reel or waits for a new one."""
        for code, bank in self.banks.items():
            for feeder in bank.values():
                for which in ("reel", "next"):
                    reel = getattr(feeder, which)
                    if reel is None or reel.id != reel_id:
                        continue
                    station = self.plant.by_code[code]
                    self._event("END", reel.id, station, feeder.part, reel.left, "MSL")
                    setattr(feeder, which, None)
                    if which == "reel" and feeder.next is not None:
                        feeder.reel, feeder.next = feeder.next, None
                        self._event("START", feeder.reel.id, station, feeder.part, feeder.reel.left)
                    self._bake(station.line.code, reel)
                    if feeder.reel is None:
                        self._want_splice(station, feeder)
                    return True
        return False

    def _msl_watch(self):
        """Offline stand-in for the MES alarm: reels on the machines whose floor life ran out."""
        while True:
            yield self.env.timeout(MSL_WATCH_S)
            now = self.env.now
            for bank in list(self.banks.values()):
                for feeder in list(bank.values()):
                    for reel in (feeder.reel, feeder.next):
                        if reel is not None and self.expired(reel, now):
                            self.msl_expired(reel.id)

    def expired(self, reel: Reel, now: float) -> bool:
        if reel.msl is None:
            return False
        return reel.floor_used(now) > self.cfg.msl.floor_h[reel.msl] * 3600

    # ------------------------------------------------------------------ solder paste
    def _new_jar(self, out_at: float) -> Jar:
        self.n_jars += 1
        return Jar(f"PJ{self.tag}{self.n_jars:05d}", f"SP-{(self.n_jars - 1) // 12 + 1:04d}", out_at,
                   self.cfg.paste.prints)

    def change_paste(self, station: Station, why: str):
        """SimPy generator: a new jar on the printer (the printer waits)."""
        paste = self.cfg.paste
        station.set_state(State.MINOR_STOP, "PASTE_CHANGE")

        def take():
            """At the thaw cabinet: the jar that thawed longest (now and then by mistake the newest); one
            that needs a while yet is waited for. The next one comes out of the fridge into the cabinet."""
            if not self.cabinet:
                self.cabinet.append(self._new_jar(self.env.now))
            early = self.rng.random() < paste.early_p and len(self.cabinet) > 1
            jar = self.cabinet.pop(-1 if early else 0)
            if len(self.cabinet) < paste.cabinet:
                self.cabinet.append(self._new_jar(self.env.now))
            wait = jar.out_at + paste.thaw_h * 3600 - self.env.now
            if wait > 0 and not early:
                station.set_state(State.MINOR_STOP, "NO_MATERIAL")
                yield self.env.timeout(wait)
                station.set_state(State.MINOR_STOP, "PASTE_CHANGE")
            return jar

        def check(jar: Jar):
            verdict = yield from self._ask(station, {
                "kind": "PASTE", "item": jar.id, "lot": jar.lot, "out_at": self.plant.clock.iso(jar.out_at),
                "prints": jar.prints}, jar)
            if verdict.ok:
                return True
            self.stats[f"refused: {verdict.reason}"] += 1
            self.cabinet.append(jar)                   # back into the cabinet
            self.cabinet.sort(key=lambda j: j.out_at)
            return False

        jar = yield from self.plant.crew.fetch(station, "paste", "paste", take, check, paste.change_s)
        old = self.jars.get(station.code)
        if old is not None:
            self._event("END", old.id, station, "PASTE", old.prints, why)
        jar.opened_at = self.env.now
        self.jars[station.code] = jar
        self.stats["paste changes"] += 1
        self._event("START", jar.id, station, "PASTE", jar.prints, **self._jar_info(jar))

    def paste_expired(self, station: Station, now: float) -> bool:
        """Offline stand-in for the MES check at the printer: open longer than allowed."""
        jar = self.jars.get(station.code)
        return jar is not None and jar.opened_at is not None and now - jar.opened_at > self.cfg.paste.open_h * 3600

    # ------------------------------------------------------------------ for the MES and the live view
    def _event(self, kind: str, item: str, station: Station, slot: str, left: int, reason: str | None = None,
               **extra) -> None:
        self.events.append({"kind": kind, "item": item, "station": station.code, "slot": slot,
                            "at": self.plant.clock.iso(self.env.now), "left": left, "reason": reason, **extra})
        self.dirty.discard(item)

    def _jar_info(self, jar: Jar) -> dict:
        clock = self.plant.clock
        return {"lot": jar.lot, "out_at": clock.iso(jar.out_at), "opened_at": clock.iso(jar.opened_at)}

    def take_report(self) -> dict:
        """What the edge sends with the next flush: the events and the levels that changed."""
        levels = []
        now = self.env.now
        for code, bank in self.banks.items():
            cycle = self.cycle.get(code, 30.0)
            for feeder in bank.values():
                reel = feeder.reel
                if reel is not None and reel.id in self.dirty:
                    eta = feeder.pieces() / max(1, feeder.per_panel) * cycle
                    levels.append({"id": reel.id, "left": reel.left, "eta_s": round(eta)})
        for jar in self.jars.values():
            if jar is not None and jar.id in self.dirty:
                levels.append({"id": jar.id, "left": jar.prints})
        return {"events": list(self.events), "levels": levels, "t": now}

    def report_sent(self, report: dict) -> None:
        del self.events[:len(report["events"])]
        for row in report["levels"]:
            self.dirty.discard(row["id"])

    def station_entry(self, station: Station) -> dict:
        """The feeders of a placer, the jar of a printer, for the live view."""
        now = self.env.now
        if station.type.code == "PASTE_PRINTER":
            jar = self.jars.get(station.code)
            if jar is None:
                return {}
            open_s = now - jar.opened_at if jar.opened_at is not None else 0.0
            return {"pj": [round(min(1.0, open_s / (self.cfg.paste.open_h * 3600)), 3),
                           round(max(0.0, jar.prints / self.cfg.paste.prints), 3), jar.id]}
        bank = self.banks.get(station.code)
        if not bank:
            return {}
        out = []
        for feeder in bank.values():
            reel = feeder.reel
            fill = reel.left / reel.qty if reel else 0.0
            flags = (1 if feeder.splicing else 0) | (2 if feeder.next is not None else 0)
            msl = None
            if reel is not None and reel.msl is not None:
                msl = round(max(0.0, 1 - reel.floor_used(now) / (self.cfg.msl.floor_h[reel.msl] * 3600)), 3)
            out.append([feeder.part, round(fill, 3), flags, reel.id if reel else None, msl])
        return {"fd": out}

    def snapshot(self) -> dict:
        now = self.env.now
        thaw = self.cfg.paste.thaw_h * 3600
        return {"racks": {line: len(rack) + len(self.spares.get(line, ())) for line, rack in self.racks.items()},
                "cab": [round(min(1.0, (now - j.out_at) / thaw), 3) if thaw else 1.0 for j in self.cabinet],
                "bake": len(self.baking), "scans": list(self.log)[-8:]}
