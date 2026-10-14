"""Shipping: trucks of the customers take the finished pallets away.

Every customer sends a truck on a timetable (config/crew.yaml, local time). The truck drives
into its bay below the warehouse, a logistics worker loads the oldest pallets of the
customer's products one by one (the pallet rides the short belt out of the warehouse, the
worker needs `load_s` to put it in), and when nothing is left or the truck is full it leaves
with a delivery note. The MES gets the shipment (ASN) with the serial numbers of the pallets
and marks everything on them as shipped.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .core import Item
from .crew import LOGISTICS

if TYPE_CHECKING:
    from mes_lab.model.schema import TruckSchedule

    from .plant import PlantSim

log = logging.getLogger("mes_lab.shipping")

RETRY_S = 1800.0
RETRIES = 3
PLATE = {"DE": ("M", "KW"), "SE": ("ABC", ""), "CZ": ("1AB", "")}


class Truck:
    def __init__(self, tid: int, customer: str, plate: str, bay: int, capacity: int):
        self.id = tid
        self.customer = customer
        self.plate = plate
        self.bay = bay
        self.capacity = capacity
        self.phase = "in"                    # in -> load -> out
        self.t0 = 0.0
        self.duration = 0.0
        self.pallets: list[Item] = []


class Shipping:
    def __init__(self, plant: PlantSim):
        self.plant = plant
        self.env = plant.env
        self.model = plant.model
        self.layout = plant.layout
        self.rng = plant.rng_for("shipping")
        self.tz = ZoneInfo(plant.model.plant.timezone)
        self.tag = plant.run_id.replace("-", "")[:5].upper()
        self.trucks: dict[int, Truck] = {}
        self.recent: deque[dict] = deque(maxlen=10)
        self.counter = 0
        self.shipped: dict[str, dict[str, int]] = {m.code: {"pallets": 0, "klt": 0, "units": 0}
                                                   for m in self.model.finished_goods()}
        for schedule in self.model.shipping.trucks:
            self.env.process(self._timetable(schedule))

    # ------------------------------------------------------------------ time
    def _next(self, times: list[str]) -> float:
        """Simulation seconds until the next of the daily local times."""
        now = self.plant.clock.at(self.env.now).astimezone(self.tz)
        best = None
        for day in (0, 1):
            date = (now + timedelta(days=day)).date()
            for text in times:
                hh, mm = (int(x) for x in text.split(":"))
                at = datetime(date.year, date.month, date.day, hh, mm, tzinfo=self.tz)
                if at > now and (best is None or at < best):
                    best = at
        return (best - now).total_seconds()

    # ------------------------------------------------------------------ stock
    def _materials(self, customer: str) -> list[str]:
        return [m.code for m in self.model.finished_goods() if m.customer == customer]

    def available(self, customer: str) -> int:
        return sum(len(self.plant.stock[m]) for m in self._materials(customer))

    def _take(self, customer: str) -> Item | None:
        """The oldest pallet of the customer's products leaves the warehouse."""
        oldest = None
        for material in self._materials(customer):
            queue = self.plant.stock[material]
            if queue and (oldest is None or queue[0].id < self.plant.stock[oldest][0].id):
                oldest = material
        if oldest is None:
            return None
        pallet = self.plant.stock[oldest].popleft()
        counts = self.plant.warehouse[oldest]
        counts["pallets"] -= 1
        counts["klt"] -= len(pallet.children)
        counts["units"] -= sum(len(k.children) for k in pallet.children)
        return pallet

    # ------------------------------------------------------------------ trucks
    def _plate(self, customer: str) -> str:
        country = self.model.customers[customer].country
        a, b = PLATE.get(country, ("X", ""))
        digits = self.rng.randint(1000, 9999)
        letters = "".join(self.rng.choice("ABCDEFGHJKLMNPRSTUVZ") for _ in range(2))
        if country == "DE":
            return f"{a}-{b} {digits}"
        if country == "SE":
            return f"{a} {self.rng.randint(100, 999)}"
        return f"{a} {letters}{digits}"[:12]

    def _timetable(self, schedule: TruckSchedule):
        while True:
            yield self.env.timeout(self._next(schedule.at))
            for attempt in range(RETRIES + 1):
                if self.available(schedule.customer) >= schedule.min_pallets:
                    break
                if attempt == RETRIES:
                    break
                yield self.env.timeout(RETRY_S)
            if self.available(schedule.customer) == 0:
                continue
            yield from self._visit(schedule)

    def _visit(self, schedule: TruckSchedule):
        crew = self.plant.crew
        self.counter += 1
        truck = Truck(self.counter, schedule.customer, self._plate(schedule.customer), schedule.bay, schedule.capacity)
        self.trucks[truck.id] = truck
        bay = schedule.bay
        yield from self._drive(truck, "in", f"truck_in:{bay}")
        truck.phase = "load"
        pool = crew.pools[LOGISTICS]
        worker = yield from pool.acquire(f"dock:{bay}")
        worker.act = "walk"
        yield from crew.walk(worker, f"dock:{bay}")
        worker.act, worker.target = "fix", f"dock:{bay}"
        while len(truck.pallets) < truck.capacity:
            pallet = self._take(schedule.customer)
            if pallet is None:
                break
            ride = self.plant.logistics.send(f"ship:{bay}", pallet)
            yield self.env.timeout(ride.dur + self.model.shipping.load_s)
            truck.pallets.append(pallet)
        worker.target = None
        self._leave_dock(worker)
        self._document(truck)
        yield from self._drive(truck, "out", f"truck_out:{bay}")
        del self.trucks[truck.id]

    def _leave_dock(self, worker) -> None:
        self.env.process(self.plant.crew.release(worker))

    def _drive(self, truck: Truck, phase: str, route: str):
        truck.phase = phase
        truck.t0 = self.env.now
        truck.duration = self.layout.duration(route)
        yield self.env.timeout(truck.duration)

    def _document(self, truck: Truck) -> dict:
        """The delivery note: what left with the truck."""
        asn = f"ASN{self.tag}{truck.id:04d}"
        klt = sum(len(p.children) for p in truck.pallets)
        units = sum(len(k.children) for p in truck.pallets for k in p.children)
        shipment = {"asn": asn, "customer": truck.customer, "plate": truck.plate, "bay": truck.bay,
                    "departed": self.plant.clock.iso(self.env.now),
                    "pallets": [p.serial for p in truck.pallets if p.serial], "klt": klt, "units": units}
        for pallet in truck.pallets:
            counts = self.shipped[pallet.material]
            counts["pallets"] += 1
            counts["klt"] += len(pallet.children)
            counts["units"] += sum(len(k.children) for k in pallet.children)
        self.recent.appendleft({"asn": asn, "customer": truck.customer, "plate": truck.plate, "pallets": len(truck.pallets),
                                "units": units, "t": round(self.env.now, 1)})
        self.plant.edge.add_shipment(shipment)
        log.info("%s left with %d pallets (%d units) for %s", asn, len(truck.pallets), units, truck.customer)
        return shipment

    # ------------------------------------------------------------------ snapshot
    def snapshot(self) -> dict:
        trucks = []
        for t in self.trucks.values():
            rec = {"i": t.id, "c": t.customer, "plate": t.plate, "bay": t.bay, "ph": t.phase, "t": round(t.t0, 1),
                   "d": round(t.duration, 1), "cap": t.capacity,
                   "load": [{"p": p.material, "sn": p.serial, "n": len(p.children)} for p in t.pallets]}
            trucks.append(rec)
        return {"trucks": trucks, "ship": list(self.recent),
                "shipped": self.shipped}
