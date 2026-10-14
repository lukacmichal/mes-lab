"""Where lines get their work from.

* MesDispatcher - asks the MES (normal operation).
* LocalDispatcher - plans on its own, for offline runs and tests. Uses the same
  planning rules as the MES (mes_lab.planning).
"""

from __future__ import annotations

import logging
import random
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mes_lab import planning
from mes_lab.model.schema import AreaKind, Material, PlantModel

from .core import JobSpec

if TYPE_CHECKING:
    from .mes_client import MesClient
    from .plant import PlantSim

log = logging.getLogger("mes_lab.sim")


@dataclass
class _Order:
    order_no: str
    material: Material
    qty: int
    line: str
    jobs: list[JobSpec] = field(default_factory=list)


class LocalDispatcher:
    def __init__(self, model: PlantModel, seed: int = 1):
        self.model = model
        self.rng = random.Random(f"{seed}:planner")
        self.queues: dict[str, deque[JobSpec]] = {code: deque() for code in model.lines}
        self.orders: list[_Order] = []
        self.order_count = 0
        self.job_count = 0
        self.plant: PlantSim | None = None

    def attach(self, plant: PlantSim) -> None:
        self.plant = plant

    # ------------------------------------------------------------------ progress
    def _job(self, spec: JobSpec):
        return self.plant.jobs.get(spec.id) if self.plant else None

    def _order_done(self, order: _Order) -> bool:
        job = self._job(order.jobs[-1])
        return job is not None and job.status == "COMPLETED"

    def _remaining_release(self, spec: JobSpec) -> int:
        job = self._job(spec)
        return spec.qty - (job.released if job else 0)

    # ------------------------------------------------------------------ orders
    def _new_order(self, material: Material, qty: int, line: str, panels: int | None = None) -> _Order:
        self.order_count += 1
        order = _Order(f"WO{self.order_count:07d}", material, qty, line)
        for op in material.routing:
            self.job_count += 1
            spec = JobSpec(self.job_count, order.order_no, material.code, op.op, panels or qty)
            order.jobs.append(spec)
            self.queues[line].append(spec)
        self.orders.append(order)
        return order

    def _create_fa_order(self, line_code: str) -> None:
        line = self.model.lines[line_code]
        material = self.model.materials[self.rng.choice(line.materials)]
        self._new_order(material, planning.fa_lot_size(self.rng, material), line_code)

    def _replenish(self) -> None:
        self.orders = [o for o in self.orders if not self._order_done(o)]
        smt_lines = [l.code for l in self.model.lines_of_kind(AreaKind.SMT)]
        for pcba in self.model.pcbas():
            stock = len(self.plant.supermarket[pcba.code].items)
            supply = 0
            demand = 0
            for order in self.orders:
                if order.material.code == pcba.code:
                    final = self._job(order.jobs[-1])
                    supply += order.qty - (final.good + final.scrap if final else 0)
                elif order.material.pcba == pcba.code:
                    demand += self._remaining_release(order.jobs[0])
            for lot in planning.smt_lots(stock, supply, demand, pcba.planning, pcba.boards_per_panel):
                workload = {code: 0.0 for code in smt_lines}
                last: dict[str, str | None] = {code: None for code in smt_lines}
                for order in self.orders:
                    if order.line in workload:
                        for spec in order.jobs:
                            op = order.material.operation(spec.op)
                            workload[order.line] += self._remaining_release(spec) * op.bottleneck_s
                        last[order.line] = order.material.code
                line = planning.pick_line(workload, last, pcba.code)
                self._new_order(pcba, lot, line, planning.panels_for(lot, pcba.boards_per_panel))

    def next_job(self, line_code: str) -> JobSpec | None:
        line = self.model.lines[line_code]
        if line.area_kind == AreaKind.FA and not self.queues[line_code]:
            self._create_fa_order(line_code)
        self._replenish()
        queue = self.queues[line_code]
        return queue.popleft() if queue else None


class MesDispatcher:
    def __init__(self, client: MesClient, run_id: str):
        self.client = client
        self.run_id = run_id
        self.plant: PlantSim | None = None
        self.failures = 0

    def attach(self, plant: PlantSim) -> None:
        self.plant = plant

    def next_job(self, line_code: str) -> JobSpec | None:
        try:
            data = self.client.next_job(self.run_id, line_code, self.plant.clock.iso(self.plant.now))
        except Exception as exc:  # noqa: BLE001 - the line just waits and asks again
            self.failures += 1
            if self.failures % 20 == 1:
                log.warning("MES dispatch for %s failed: %s", line_code, exc)
            return None
        if not data:
            return None
        return JobSpec(id=data["id"], order_no=data["order_no"], material=data["material"],
                       op=data["operation"], qty=data["qty"])
