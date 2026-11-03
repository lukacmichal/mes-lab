"""Traceability between the plant and the MES.

* check-in  - an interlocked station asks the MES before it works on an item:
              may I process this serial? The MES enforces the route, holds and
              quality status, and issues serial numbers (panel laser, product
              start in final assembly, KLT and pallet IDs at packing).
* record    - after every operation the station reports what happened (result,
              defects found, measurements). Records are buffered and travel with
              the next check-in or edge flush, so the MES always sees the history
              of a unit before it decides about its next step.
* repair    - the repair bench reports the outcome; the MES answers where the
              unit goes next (back to a station, to the supermarket, or scrap).

LocalTrace stands in for the MES in offline runs: it approves everything,
issues serial numbers and decides when a box is full.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import httpx

from .core import Item, Job, SerialGenerator

if TYPE_CHECKING:
    from .mes_client import MesClient
    from .plant import PlantSim
    from .station import Station

log = logging.getLogger("mes_lab.trace")


class MesUnavailable(Exception):
    """The MES did not answer; interlocked stations have to wait."""


@dataclass
class Decision:
    ok: bool
    reason: str | None = None
    message: str | None = None
    data: dict = field(default_factory=dict)


class TraceClient(Protocol):
    def check_in(self, station: Station, item: Item, job: Job | None,
                 consume: Item | None = None) -> Decision: ...
    def record(self, station: Station, item: Item, job: Job | None, start: float, end: float,
               result: str, defects: list[tuple[Item, list[str]]] = (),
               measurements: dict | None = None, action: str | None = None) -> None: ...
    def repair_done(self, station: Station, item: Item, success: bool) -> Decision: ...
    def verification(self, station: Station, result: str, start: float, person: str | None = None) -> None: ...
    def material_scan(self, station: Station, body: dict, thing) -> Decision: ...
    def goods(self, body: dict, who=None) -> Decision: ...
    def ship_check(self, pallet: Item) -> Decision: ...
    def take_records(self) -> list[dict]: ...
    def restore_records(self, records: list[dict]) -> None: ...


class _Buffered:
    def __init__(self, plant: PlantSim):
        self.plant = plant
        self.pending: list[dict] = []

    def record(self, station, item, job, start, end, result, defects=(), measurements=None,
               action=None) -> None:
        if not item.serial:
            return  # nothing to trace before the laser gave the panel its number
        clock = self.plant.clock
        self.pending.append({
            "serial": item.serial, "station": station.code, "step": station.type.code,
            "job_id": job.id if job else None, "start": clock.iso(start), "end": clock.iso(end),
            "result": result,
            "defects": [{"serial": piece.serial or item.serial, "code": code}
                        for piece, codes in defects for code in codes],
            "measurements": measurements or {}, "action": action,
        })

    def take_records(self) -> list[dict]:
        records, self.pending = self.pending, []
        return records

    def restore_records(self, records: list[dict]) -> None:
        self.pending[:0] = records


class LocalTrace(_Buffered):
    """Offline stand-in for the MES."""

    def __init__(self, plant: PlantSim):
        super().__init__(plant)
        self.serials = SerialGenerator()
        self.open: dict[tuple[str, str], list] = {}  # (station, material) -> [serial, count]
        self.notes: dict[str, dict[str, int]] = {}   # delivery note -> part -> reels still to come

    def record(self, *args, **kwargs) -> None:
        pass  # nobody to tell

    def ship_check(self, pallet) -> Decision:
        return Decision(True)

    def check_in(self, station, item, job, consume=None) -> Decision:
        code = station.type.code
        if code == "PASTE_PRINTER" and self.plant.material.paste_expired(station, self.plant.now):
            return Decision(False, "PASTE_EXPIRED", "the solder paste is open too long")
        if code == "LASER_PCB" and not item.serial:
            serial = self.serials.panel()
            return Decision(True, data={"serial": serial, "boards": [
                f"{serial}-{i + 1:02d}" for i in range(len(item.children))]})
        if consume is not None:
            return Decision(True, data={"serial": self.serials.unit(job.material.code)})
        if station.type.kind == "pack":
            packing = self.plant.model.materials[item.material].packing
            klt = station.type.container == "klt"
            size = packing.klt_qty if klt else packing.klt_per_pallet
            key = (station.code, item.material)
            box = self.open.get(key)
            if box is None:
                box = self.open[key] = [self.serials.klt() if klt else self.serials.pallet(), 0]
            box[1] += 1
            full = box[1] >= size
            if full:
                del self.open[key]
            return Decision(True, data={"container": box[0], "full": full})
        return Decision(True)

    def repair_done(self, station, item, success) -> Decision:
        return Decision(True, data={"disposition": "RETURN" if success else "SCRAP",
                                    "return_to": None})

    def verification(self, station, result, start, person=None) -> None:
        pass                                         # offline nobody keeps it

    def goods(self, body, who=None) -> Decision:
        """The checks of the MES at goods-in (see mes/material.py): a reel on the delivery note, the oldest
        reel out of the store first."""
        receiving = self.plant.receiving
        kind = body["kind"]
        if kind == "ARRIVAL":
            self.notes[body["note"]] = {line["part"]: line["reels"] for line in body["lines"]}
            return Decision(True)
        if kind == "RECEIPT":
            left = self.notes.get(body["note"], {})
            if left.get(body["part"], 0) <= 0:
                return Decision(False, "NOT_ORDERED", f"{body['part']} is not on the delivery note")
            left[body["part"]] -= 1
            return Decision(True)
        if kind == "ISSUE":
            queue = receiving.store.get(body["part"])
            if queue and queue[0][0].id != body["item"]:
                return Decision(False, "FIFO", f"take {queue[0][0].id} first")
        return Decision(True)

    def material_scan(self, station, body, thing) -> Decision:
        """The checks of the MES at a scan of SMT material (see mes/material.py)."""
        material = self.plant.material
        now = self.plant.now
        if body["kind"] == "PASTE":
            if now - thing.out_at < material.cfg.paste.thaw_h * 3600:
                return Decision(False, "PASTE_NOT_THAWED", "the jar has not thawed long enough")
            return Decision(True)
        if thing.part != body["slot"]:
            return Decision(False, "WRONG_PART", f"{thing.part} on the feeder of {body['slot']}")
        if material.expired(thing, now):
            return Decision(False, "MSL_EXPIRED", "the floor life of the reel ran out")
        return Decision(True)


class MesTrace(_Buffered):
    """The real thing: every check-in is a synchronous call to the MES."""

    def __init__(self, plant: PlantSim, client: MesClient, run_id: str):
        super().__init__(plant)
        self.client = client
        self.run_id = run_id
        self.failures = 0

    def _call(self, path: str, body: dict) -> Decision:
        records = self.take_records()
        body = {**body, "run_id": self.run_id, "plant_time": self.plant.clock.iso(self.plant.now),
                "records": records}
        try:
            data = self.client.trace(path, body)
        except (httpx.HTTPError, ValueError) as exc:
            self.restore_records(records)
            self.failures += 1
            if self.failures % 20 == 1:
                log.warning("MES %s failed: %s", path, exc)
            raise MesUnavailable(str(exc)) from exc
        return Decision(bool(data.get("ok")), data.get("reason"), data.get("message"),
                        {k: v for k, v in data.items() if k not in ("ok", "reason", "message")})

    def check_in(self, station, item, job, consume=None) -> Decision:
        person = self.plant.crew.operator_at(station)
        return self._call("check-in", {
            "station": station.code, "job_id": job.id if job else None,
            "operator": person.id if person is not None else None,
            "serial": None if consume is not None else item.serial,
            "consume": consume.serial if consume is not None else None,
            "pieces": len(item.children) if item.kind == "PANEL" else None,
        })

    def repair_done(self, station, item, success) -> Decision:
        person = self.plant.crew.operator_at(station)
        return self._call("repair", {"station": station.code, "serial": item.serial,
                                     "success": success, "operator": person.id if person is not None else None})

    def material_scan(self, station, body, thing) -> Decision:
        """A scan of SMT material (a reel at a feeder, a paste jar at a printer): may it go on?"""
        person = self.plant.crew.operator_at(station)
        return self._call("material", {"station": station.code,
                                       "operator": person.id if person is not None else None, **body})

    def ship_check(self, pallet) -> Decision:
        """The pallet label is scanned at the truck: may it leave? (A pallet on hold stays.)"""
        return self._call("ship", {"serial": pallet.serial})

    def goods(self, body, who=None) -> Decision:
        """A scan at goods-in (a delivery note, a reel received, a reel out of the store for a line)."""
        return self._call("material", {"station": None, "place": "GI",
                                       "operator": who.id if who is not None else None, **body})

    def verification(self, station, result, start, person=None) -> None:
        """The master sample check of a tester; a lost one is simply done again when the MES asks."""
        try:
            self._call("verification", {"station": station.code, "result": result,
                                        "start": self.plant.clock.iso(start), "person": person})
        except MesUnavailable:
            pass
