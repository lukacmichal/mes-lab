"""Line-side material that the robots bring: two-bin kanban.

The loader of a final assembly line puts the parts of a unit on every board it loads (housing, cover,
connector, screws: the `kit` of the product, see config/products.yaml). The parts come in bins that
stand in a rack beside the loader. When the open bin runs empty, the operator opens the next full one,
puts the empty bin on the return shelf of the rack, and the kanban card of the bin orders a full one
from the goods-in warehouse: the fleet manager sends a robot (see amr.py), which takes the full bin
from the warehouse rack to the line and, while there, the empty bins back. With no full bin left the
loader stops: no material.

The scan station of packing opens a new KLT box for every box it fills; the empty boxes stand there in
stacks, and a used-up stack is ordered the same way.

Everything a robot carries is a real item with an id, so the live view can show it in the warehouse
rack, on the robot and in the rack at the line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import simpy

from mes_lab.model.schema import AreaKind

from .core import Item, State

if TYPE_CHECKING:
    from .plant import PlantSim
    from .station import Station


class Rack:
    """The material rack beside the first station of a line."""

    __slots__ = ("line", "kind", "material", "per", "cap", "parts", "full", "ordered", "waiting", "used", "starved_s")

    def __init__(self, line: str, kind: str, material: str, per: int, cap: int):
        self.line = line
        self.kind = kind              # "kit": bins of parts (final assembly) | "klt": stacks of empty boxes (packing)
        self.material = material
        self.per = per                # parts in a bin / boxes in a stack
        self.cap = cap                # bins / stacks the rack holds
        self.parts = per              # what is left in the open one
        self.full = cap - 1           # full ones waiting in the rack
        self.ordered = 0              # on their way (ordered from the warehouse)
        self.waiting: list[simpy.Event] = []
        self.used = 0                 # bins / stacks used up so far
        self.starved_s = 0.0          # time the station waited for material


class Supply:
    def __init__(self, plant: PlantSim):
        self.plant = plant
        self.env = plant.env
        cfg = plant.model.amr.supply
        self.racks: dict[str, Rack] = {}
        for line in plant.model.lines.values():
            if line.area_kind == AreaKind.FA and line.materials:
                mat = plant.model.materials[line.materials[0]]
                if mat.kit is not None:
                    self.racks[line.code] = Rack(line.code, "kit", f"KIT-{mat.code}", mat.kit.per_bin, cfg.kit_bins)
            elif line.area_kind == AreaKind.PACK:
                self.racks[line.code] = Rack(line.code, "klt", "KLT", cfg.boxes_per_stack, cfg.box_stacks)

    def take(self, station: Station):
        """SimPy generator: one set of parts (one empty box) for the station; waits while the rack is empty."""
        rack = self.racks.get(station.line.code)
        if rack is None:
            return
        while rack.parts <= 0:
            if rack.full > 0:                           # open the next bin / stack
                rack.full -= 1
                rack.parts = rack.per
                break
            station.set_state(State.STARVED, "NO_MATERIAL")
            t0 = self.env.now
            event = self.env.event()
            rack.waiting.append(event)
            yield event
            rack.starved_s += self.env.now - t0
        rack.parts -= 1
        if rack.parts == 0:
            self._used_up(rack)

    def _used_up(self, rack: Rack) -> None:
        """The open bin / stack is empty: the empty bin goes on the return shelf, the kanban card orders a full one."""
        rack.used += 1
        fleet = self.plant.fleet
        stop = f"drop:{rack.line}:0"
        if rack.kind == "kit":
            empty = Item("BIN", None, rack.material)
            empty.qty = 0
            fleet.pickup(stop, empty, ("gi",))
        full = Item("BIN" if rack.kind == "kit" else "STACK", None, rack.material)
        full.qty = rack.per
        rack.ordered += 1
        fleet.pickup("gi", full, ("kan", rack.line))

    def arrive(self, line: str, item: Item) -> None:
        """A robot put a full bin / stack into the rack."""
        rack = self.racks[line]
        rack.ordered -= 1
        rack.full += 1
        events, rack.waiting = rack.waiting, []
        for event in events:
            event.succeed()

    def snapshot(self) -> dict:
        """line -> [kind, parts left in the open one, full ones, capacity, on the way, per bin/stack]"""
        return {code: [r.kind, r.parts, r.full, r.cap, r.ordered, r.per] for code, r in self.racks.items()}
