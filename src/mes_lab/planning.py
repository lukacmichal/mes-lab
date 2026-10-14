"""Demo production planning, shared by the MES and the offline simulation.

Until the fake ERP arrives (phase 4) the plant plans itself:

* a final assembly line that runs out of work gets a new work order for one of
  its products, with a lot size picked from the material's planning data;
* PCB assemblies are made to stock: whenever the projected supermarket stock
  (stock + boards still coming from SMT - boards final assembly still needs)
  falls below the safety stock, new SMT orders are created and given to the
  SMT line with the least work in front of it.
"""

from __future__ import annotations

import math
import random

from mes_lab.model.schema import Material, Planning

PANELS_PER_MAGAZINE = 10  # SMT lots are whole magazines


def fa_lot_size(rng: random.Random, material: Material) -> int:
    return rng.choice(material.planning.lot_sizes)


def smt_lots(stock: int, open_supply: int, open_demand: int, planning: Planning,
             boards_per_panel: int) -> list[int]:
    """Board quantities of the SMT orders needed to get back above safety stock,
    rounded up to whole magazines."""
    projected = stock + open_supply - open_demand
    shortfall = planning.safety_stock - projected
    lots: list[int] = []
    while shortfall > 0:
        lot = min(max(shortfall, planning.min_lot), planning.max_lot)
        step = boards_per_panel * PANELS_PER_MAGAZINE
        lot = math.ceil(lot / step) * step
        lots.append(lot)
        shortfall -= lot
    return lots


def panels_for(boards: int, boards_per_panel: int) -> int:
    return math.ceil(boards / boards_per_panel)


def pick_line(workload_s: dict[str, float], last_material: dict[str, str | None],
              material: str, tolerance_s: float = 1800.0) -> str:
    """Least loaded line; a line already set up for the material wins when it is
    within `tolerance_s` of the least loaded one (saves a changeover)."""
    least = min(workload_s.values())
    same = [l for l, m in last_material.items()
            if m == material and workload_s[l] <= least + tolerance_s]
    if same:
        return min(same, key=lambda l: (workload_s[l], l))
    return min(workload_s, key=lambda l: (workload_s[l], l))
