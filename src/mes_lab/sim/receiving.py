"""Goods receipt of the SMT reels and the supply of the lines (config/smt_material.yaml).

* purchasing - stands in for the ERP: when the reels of a part in the store and on order fall under its
               use during the supplier's lead time plus a safety, it orders `order_days` of use; the order
               comes with the first truck of the supplier after the lead time.
* trucks     - every supplier has a daily timetable; a truck comes when it brings something. It backs into
               the bay south of the goods-in warehouse and waits until the material handler has carried
               every box to the receiving desk.
* receipt    - the handler scans every reel against the delivery note: the MES refuses a part that was not
               ordered (the box goes back on the truck) and a lot on hold. A good reel gets its reel ID label
               and goes into the store, sealed (a moisture-sensitive one into the dry cabinet).
* supply     - the racks at the SMT lines keep a spare reel of every part their job uses (see material.py).
               A spare taken orders the next one (a kanban card); a new job orders what its rack lacks, at
               once. The handler picks the ordered reels in the store, oldest first (the MES refuses a newer
               one: FIFO), and takes them round the lines: every `milk_run_min`, at once when a line waits.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .material import Reel
from .trace import MesUnavailable

if TYPE_CHECKING:
    from .crew import Person
    from .plant import PlantSim

log = logging.getLogger("mes_lab.receiving")

TRUCK_CAP = 40                # boxes a supplier truck brings at most
CARRY = 12                    # reels the handler takes round the lines at once (the trolley)
PUT_S = 10.0                  # a scanned box into the store


class Order:
    __slots__ = ("no", "supplier", "part", "reels", "due", "placed")

    def __init__(self, no: str, supplier: str, part: str, reels: int, due: float, placed: float):
        self.no, self.supplier, self.part, self.reels, self.due, self.placed = no, supplier, part, reels, due, placed


class Card:
    """A kanban card: the rack of a line wants a reel of a part."""
    __slots__ = ("line", "part", "urgent", "since", "short")

    def __init__(self, line: str, part: str, urgent: bool, since: float):
        self.line, self.part, self.urgent, self.since = line, part, urgent, since
        self.short = False                  # the store had none when the handler looked


class Truck:
    def __init__(self, tid: int, supplier: str, note: str, plate: str, boxes: list[list[Reel]], lines: dict[str, int]):
        self.id = tid
        self.supplier = supplier
        self.note = note
        self.plate = plate
        self.boxes = boxes                 # each box: reels of one part
        self.lines = lines                 # the delivery note: part -> reels
        self.returned: list[Reel] = []     # refused at the receipt: back to the supplier
        self.phase = "in"
        self.t0 = 0.0
        self.duration = 0.0
        self.unloaded = None               # SimPy event: the handler carried the last box in


class Receiving:
    def __init__(self, plant: PlantSim):
        self.plant = plant
        self.env = plant.env
        self.cfg = plant.model.smt_material
        self.rules = self.cfg.receiving
        self.rng = plant.rng_for("receiving")
        self.tz = ZoneInfo(plant.model.plant.timezone)
        self.tag = plant.run_id.replace("-", "")[:6].upper()
        self.store: dict[str, deque[tuple[Reel, float]]] = {p: deque() for p in self.cfg.parts}   # (reel, received at)
        self.orders: list[Order] = []
        self.cards: list[Card] = []
        self.trucks: dict[int, Truck] = {}
        self.bay: Truck | None = None                   # the truck at the bay
        self.n_trucks = self.n_orders = 0
        self.stats: Counter = Counter()
        self.recent: deque[dict] = deque(maxlen=8)
        self.wake = self.env.event()                    # the handler: a truck or an urgent card
        self.next_run = self.rules.milk_run_min * 60.0
        if not self.cfg.suppliers:
            return
        for part, spec in self.cfg.parts.items():       # the store at the start: an order just came in
            sup = self.cfg.suppliers.get(spec.supplier or "")
            days = max(self.rules.start_days, (sup.lead_h / 24 if sup else 0) + self.rules.safety_days + self.rules.order_days)
            for k in range(math.ceil(spec.per_day * days)):
                reel = self.material_reel(part, received=-(k + 1) * 3600.0)
                self.store[part].appendleft((reel, -(k + 1) * 3600.0))
            self._reorder(part)
        for sup in self.cfg.suppliers.values():
            self.env.process(self._timetable(sup.code))

    # ------------------------------------------------------------------ reels and the store
    def material_reel(self, part: str, received: float | None = None) -> Reel:
        return self.plant.material._new_reel(part)

    def quarantine_lot(self, lot: str) -> int:
        """A supplier lot on hold: its reels leave the store (they go back to the supplier)."""
        n = 0
        for part, queue in self.store.items():
            keep = deque(x for x in queue if x[0].lot != lot)
            n += len(queue) - len(keep)
            self.store[part] = keep
        return n

    def take(self, part: str) -> tuple[Reel, float] | None:
        """The oldest reel of a part out of the store (None: none left)."""
        queue = self.store.get(part)
        if not queue:
            return None
        got = queue.popleft()
        self._reorder(part)
        return got

    def stock_out(self) -> list[dict]:
        """The reels in the store, for the MES (at the start of a run)."""
        clock = self.plant.clock
        return [{"serial": r.id, "part": r.part, "lot": r.lot, "qty": r.qty, "msl": r.msl,
                 "supplier": self.cfg.parts[r.part].supplier, "received": clock.iso(t)}
                for queue in self.store.values() for r, t in queue]

    def in_store(self, part: str) -> int:
        return len(self.store.get(part, ()))

    # ------------------------------------------------------------------ purchasing (the ERP)
    def _reorder(self, part: str) -> None:
        spec = self.cfg.parts[part]
        sup = self.cfg.suppliers.get(spec.supplier or "")
        if sup is None:
            return
        on_order = sum(o.reels for o in self.orders if o.part == part)
        point = spec.per_day * (sup.lead_h / 24 + self.rules.safety_days)
        if self.in_store(part) + on_order >= point:
            return
        reels = max(1, math.ceil(spec.per_day * self.rules.order_days))
        self.n_orders += 1
        due = self.env.now + sup.lead_h * 3600
        self.orders.append(Order(f"PO{self.tag}{self.n_orders:05d}", sup.code, part, reels, due, self.env.now))
        self.stats["orders"] += 1

    # ------------------------------------------------------------------ trucks
    def _next(self, times) -> float:
        now = self.plant.clock.at(self.env.now).astimezone(self.tz)
        best = None
        for day in (0, 1):
            date = (now + timedelta(days=day)).date()
            for t in times:
                at = datetime(date.year, date.month, date.day, t.hour, t.minute, tzinfo=self.tz)
                if at > now and (best is None or at < best):
                    best = at
        return (best - now).total_seconds()

    def _timetable(self, supplier: str):
        sup = self.cfg.suppliers[supplier]
        while True:
            yield self.env.timeout(self._next(sup.trucks))
            due = [o for o in self.orders if o.supplier == supplier and o.due <= self.env.now + 1]
            if not due:
                continue
            while self.bay is not None:                 # one bay: the trucks queue on the yard
                yield self.env.timeout(120)
            yield from self._visit(supplier, due)

    def _visit(self, supplier: str, due: list[Order]):
        boxes: list[list[Reel]] = []
        lines: Counter = Counter()
        for order in due[:TRUCK_CAP]:
            self.orders.remove(order)
            lines[order.part] += order.reels
            reels = [self.material_reel(order.part) for _ in range(order.reels)]
            per = self.rules.box_reels
            boxes += [reels[i:i + per] for i in range(0, len(reels), per)]
        if self.rng.random() < self.rules.wrong_part_p:       # the supplier packed a box of another part
            others = [p for p, s in self.cfg.parts.items() if s.supplier == supplier and p not in lines] or list(self.cfg.parts)
            boxes.append([self.material_reel(self.rng.choice(others)) for _ in range(2)])
        self.rng.shuffle(boxes)
        self.n_trucks += 1
        truck = Truck(self.n_trucks, supplier, f"DN{self.tag}{self.n_trucks:04d}", self._plate(supplier), boxes, dict(lines))
        truck.unloaded = self.env.event()
        self.trucks[truck.id] = truck
        self.bay = truck
        yield from self._drive(truck, "in", "recv_in")
        truck.phase = "unload"
        yield from self._ask({"kind": "ARRIVAL", "item": truck.note, "note": truck.note, "supplier": supplier,
                              "lines": [{"part": p, "reels": n} for p, n in sorted(lines.items())]})
        self._wake()
        yield truck.unloaded
        yield from self._drive(truck, "out", "recv_out")
        self.recent.append({"note": truck.note, "supplier": supplier, "boxes": len(boxes),
                            "returned": len(truck.returned), "at": round(self.env.now, 1)})
        self.bay = None
        del self.trucks[truck.id]
        self.stats["trucks"] += 1

    def _drive(self, truck: Truck, phase: str, route: str):
        truck.phase, truck.t0 = phase, self.env.now
        truck.duration = self.plant.layout.duration(route)
        yield self.env.timeout(truck.duration)

    def _plate(self, supplier: str) -> str:
        country = self.cfg.suppliers[supplier].country
        digits = self.rng.randint(1000, 9999)
        letters = "".join(self.rng.choice("ABCDEFGHJKLMNPRSTUVZ") for _ in range(2))
        return {"DE": f"R-{letters} {digits}", "CZ": f"7B{letters[0]} {digits}", "HU": f"{letters}X-{digits % 1000:03d}"}.get(
            country, f"{letters} {digits}")

    # ------------------------------------------------------------------ the kanban cards of the lines
    def order_reel(self, line: str, part: str, urgent: bool) -> None:
        for card in self.cards:
            if card.line == line and card.part == part:
                if urgent and not card.urgent:
                    card.urgent = True
                    self._wake()
                return
        self.cards.append(Card(line, part, urgent, self.env.now))
        if urgent:
            self._wake()

    def _wake(self) -> None:
        if not self.wake.triggered:
            self.wake.succeed()

    # ------------------------------------------------------------------ the material handler
    def work(self, person: Person, shift: str):
        """SimPy process: the handler of a shift: the truck at the bay first, then the lines that wait for a
        reel, the milk run when it is time; otherwise at the receiving desk."""
        crew = self.plant.crew
        yield from crew._arrived(person, shift)
        while crew.current_shift == shift and person.on_site and person.task != "leaving":
            if not person.available or person.on_break:
                yield self.env.timeout(10)
                continue
            job = None
            ready = self._ready_cards()
            if self.bay is not None and self.bay.phase == "unload" and self.bay.boxes:
                job = self._unload(person, self.bay)
            elif ready and (any(c.urgent for c in ready) or self.env.now >= self.next_run):
                job = self._milk_run(person, ready)
            if job is None:
                if self.env.now >= self.next_run:
                    self.next_run = self.env.now + self.rules.milk_run_min * 60
                if self.wake.triggered:
                    self.wake = self.env.event()
                yield self.env.any_of([self.wake, self.env.timeout(max(30.0, min(300.0, self.next_run - self.env.now)))])
                continue
            person.available = False
            person.busy = True
            yield from job
            person.busy = False
            person.carry = person.target = None
            if crew.current_shift != shift:                   # busy at the change: the colleague took over
                self.env.process(crew._leave(person))
                return
            if person.place != person.base:
                person.act = "walk"
                yield from crew.walk(person, person.base)
            person.act = "idle"
            crew._became_free(person)

    def _unload(self, person: Person, truck: Truck):
        crew = self.plant.crew
        while truck.boxes and person.on_site and crew.current_shift == person.shift:   # (the next shift goes on)
            box = truck.boxes.pop(0)
            person.act = "walk"
            yield from crew.walk(person, "recv:truck")
            person.act = "take"
            yield self.env.timeout(self.rules.unload_box_s * self.rng.uniform(0.7, 1.3))
            person.carry = "box"
            person.act = "walk"
            yield from crew.walk(person, person.base)
            person.act = "scan"
            good = []
            for reel in box:
                yield self.env.timeout(self.rules.scan_reel_s * self.rng.uniform(0.8, 1.3))
                verdict = yield from self._ask({
                    "kind": "RECEIPT", "item": reel.id, "note": truck.note, "supplier": truck.supplier, "part": reel.part,
                    "lot": reel.lot, "qty": reel.qty, "left": reel.left, "msl": reel.msl}, person)
                if verdict.ok:
                    good.append(reel)
                else:
                    truck.returned.append(reel)
                    self.stats[f"refused: {verdict.reason}"] += 1
            if good:
                person.act = "walk"
                yield from crew.walk(person, "recv:store")
                person.act = "put"
                yield self.env.timeout(PUT_S)
                for reel in good:
                    self.store[reel.part].append((reel, self.env.now))
                self.stats["reels received"] += len(good)
            person.carry = None
        if not truck.boxes and not truck.unloaded.triggered:
            truck.unloaded.succeed()

    def _ready_cards(self) -> list[Card]:
        """The cards the store can serve; a part out of stock waits for its truck (counted once)."""
        ready = []
        for card in self.cards:
            if self.store.get(card.part):
                ready.append(card)
            elif not card.short:
                card.short = True
                self.stats["stockouts"] += 1
        return ready

    def _milk_run(self, person: Person, cards: list[Card]):
        """Pick the reels the racks ordered (the urgent ones first, oldest reel first) and take them round."""
        crew = self.plant.crew
        cards = sorted(cards, key=lambda c: (not c.urgent, c.since))
        person.act = "walk"
        yield from crew.walk(person, "recv:store")
        person.act = "pick"
        load: list[tuple[Card, Reel]] = []
        for card in cards:
            if len(load) >= CARRY:
                break
            queue = self.store.get(card.part)
            if not queue:
                continue
            while True:
                yield self.env.timeout(self.rules.pick_s * self.rng.uniform(0.8, 1.3))
                wrong = len(queue) > 1 and self.rng.random() < self.rules.fifo_p
                reel, received = queue[1] if wrong else queue[0]
                verdict = yield from self._ask({"kind": "ISSUE", "item": reel.id, "part": reel.part, "line": card.line,
                                                "received": self.plant.clock.iso(received)}, person)
                if verdict.ok or verdict.reason != "FIFO":      # (only FIFO sends the handler back to the shelf)
                    queue.remove((reel, received))
                    self._reorder(card.part)
                    break
                self.stats[f"refused: {verdict.reason}"] += 1
            self.cards.remove(card)
            load.append((card, reel))
        if not load:
            self.next_run = self.env.now + self.rules.milk_run_min * 60
            return
        person.carry = "trolley"
        for line in sorted({c.line for c, _ in load}):
            person.act = "walk"
            yield from crew.walk(person, f"reels:{line}")
            person.act = "put"
            for card, reel in [x for x in load if x[0].line == line]:
                yield self.env.timeout(self.rules.drop_s)
                self.plant.material.delivered(line, reel)
        self.stats["milk runs"] += 1
        self.stats["reels to lines"] += len(load)
        self.next_run = self.env.now + self.rules.milk_run_min * 60

    # ------------------------------------------------------------------ the MES
    def _ask(self, body: dict, who=None):
        attempt = 0
        while True:
            try:
                verdict = self.plant.trace.goods(body, who)
                break
            except MesUnavailable:
                yield self.env.timeout(min(60.0, 2.0 * 2 ** attempt))
                attempt += 1
        self.plant.material.log.append({"t": round(self.env.now, 1), "st": "GI", "k": body["kind"], "i": body["item"],
                                        "p": body.get("part"), "ok": verdict.ok, "r": verdict.reason})
        return verdict

    def snapshot(self) -> dict:
        t = self.bay
        truck = None
        if t is not None:
            truck = {"i": t.id, "s": t.supplier, "plate": t.plate, "ph": t.phase, "t": round(t.t0, 1), "d": round(t.duration, 1),
                     "boxes": len(t.boxes), "note": t.note}
        return {"truck": truck, "store": sum(len(q) for q in self.store.values()),
                "cards": len(self.cards), "urgent": sum(1 for c in self.cards if c.urgent),
                "orders": len(self.orders)}
