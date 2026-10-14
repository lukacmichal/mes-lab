"""Production control: simulation runs, dispatching jobs to lines and the demo
planner that creates work orders until the fake ERP exists (phase 4)."""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from mes_lab import planning
from mes_lab.model.schema import AreaKind, PlantModel

from . import models as db
from .trace import Trace

log = logging.getLogger("mes_lab.control")

OPEN = ("RELEASED", "IN_PROGRESS")


# ------------------------------------------------------------------ runs

def register_run(session: Session, run_id: str, seed: int, speed: float | None,
                 now: datetime | None = None, trace: Trace | None = None,
                 initial_stock: dict[str, list[str]] | None = None,
                 lines: list[str] | None = None, knobs: dict | None = None) -> db.SimRun:
    """A new simulation run starts with an empty shop floor: unfinished work orders
    go back to the queue, units in process are lost, the opening stock of boards
    is registered, and plant time continues after the previous run. A run of a tuning
    scenario may have fewer lines: the work of a line it does not have moves to one it has."""
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    last = session.scalar(select(func.max(db.SimRun.last_plant_time)))
    start = now if last is None or last < now else (last + timedelta(seconds=1)).replace(microsecond=0)

    unfinished = session.scalars(select(db.WorkOrder).where(db.WorkOrder.status.in_(OPEN))).all()
    for order in unfinished:
        order.status = "RELEASED"
        order.started_at = None
        order.qty_good = order.qty_scrap = 0
        for job in order.jobs:
            job.status = "QUEUED"
            job.released = job.good = job.scrap = job.in_repair = job.held = 0
            job.started_at = job.completed_at = None
            job.run_id = None
    if unfinished:
        log.info("new run %s: %d unfinished work orders back in the queue", run_id, len(unfinished))
    if lines:
        _move_work(session, unfinished, set(lines))
    session.execute(db.StationStatus.__table__.delete())
    previous = session.scalar(select(db.SimRun).order_by(db.SimRun.started_wall.desc()).limit(1))
    run = db.SimRun(id=run_id, seed=seed, speed=speed,
                    desired_speed=(previous.desired_speed if previous else None) or speed,
                    plant_start=start, last_plant_time=start, last_seen_wall=now,
                    started_wall=max(now, datetime.now(UTC)),        # exact: two runs in one second keep their order
                    lines=sorted(lines) if lines else None, knobs=knobs or None)
    session.add(run)
    session.flush()
    if trace is not None:
        trace.start_run(session, run_id, initial_stock or {}, start)
    session.commit()
    return run


def _move_work(session: Session, orders: list[db.WorkOrder], have: set[str]) -> None:
    """The work orders of lines the plant does not have go to the line of the same kind that has the least
    work (a tuning scenario with fewer SMT lines: their boards are still needed)."""
    rows = {l.code: l for l in session.scalars(select(db.Line)).all()}
    load = {code: 0 for code in have}
    for order in orders:
        for job in order.jobs:
            code = job.line.code
            if code in load:
                load[code] += job.qty
    moved = 0
    for order in orders:
        codes = {job.line.code for job in order.jobs}
        if codes <= have:
            continue
        kind = rows[next(iter(codes))].area.kind
        target = min((c for c in have if rows[c].area.kind == kind), key=lambda c: load[c], default=None)
        if target is None:
            continue
        for job in order.jobs:
            job.line_id = rows[target].id
            load[target] += job.qty
        moved += 1
    if moved:
        log.info("%d work orders moved to the lines this plant has", moved)


def run_lines(session: Session, run: db.SimRun | None) -> set[str] | None:
    """The lines of a run (None: every line of the model)."""
    return set(run.lines) if run is not None and run.lines else None


def cancel_open_orders(session: Session, now: datetime | None = None) -> int:
    """Cancel every unfinished work order and its unfinished jobs (clearing a
    backlog after an incident). Returns the number of orders cancelled."""
    now = now or datetime.now(UTC)
    orders = session.scalars(select(db.WorkOrder).where(db.WorkOrder.status.in_(OPEN))).all()
    for order in orders:
        order.status = "CANCELLED"
        order.completed_at = now
        for job in order.jobs:
            if job.status != "COMPLETED":
                job.status = "CANCELLED"
    session.commit()
    return len(orders)


def current_run(session: Session) -> db.SimRun | None:
    return session.scalar(select(db.SimRun).order_by(db.SimRun.started_wall.desc()).limit(1))


# ------------------------------------------------------------------ dispatching

def next_job(session: Session, planner: Planner, run: db.SimRun, line_code: str,
             plant_time: datetime) -> db.Job | None:
    line = session.scalar(select(db.Line).where(db.Line.code == line_code))
    if line is None or not line.job_line:
        return None
    planner.ensure_work(session, line, plant_time, run)
    order_on_hold = exists().where(db.Hold.status == "ACTIVE", db.Hold.kind == "WORK_ORDER",
                                   db.Hold.work_order_id == db.Job.work_order_id)
    job = session.scalar(select(db.Job)
                         .where(db.Job.line_id == line.id, db.Job.status == "QUEUED", ~order_on_hold)
                         .order_by(db.Job.id).limit(1))
    if job is None:
        session.commit()
        return None
    job.status = "ACTIVE"
    job.started_at = plant_time
    job.run_id = run.id
    order = job.work_order
    if order.status == "RELEASED":
        order.status = "IN_PROGRESS"
        order.started_at = plant_time
    session.commit()
    return job


# ------------------------------------------------------------------ planning

class Planner:
    """Same rules as the offline planner (mes_lab.planning), on database state."""

    def __init__(self, model: PlantModel):
        self.model = model

    def ensure_work(self, session: Session, line: db.Line, plant_time: datetime,
                    run: db.SimRun) -> None:
        spec = self.model.lines[line.code]
        if spec.area_kind == AreaKind.FA:
            queued = session.scalar(select(func.count()).select_from(db.Job).where(
                db.Job.line_id == line.id, db.Job.status == "QUEUED"))
            if not queued:
                n_orders = session.scalar(select(func.count()).select_from(db.WorkOrder))
                rng = random.Random(f"{run.seed}:{n_orders}")
                material = self.model.materials[rng.choice(spec.materials)]
                self.create_order(session, material.code, planning.fa_lot_size(rng, material),
                                  line.code, plant_time)
        self.replenish(session, plant_time, run)

    def create_order(self, session: Session, material_code: str, qty: int, line_code: str,
                     plant_time: datetime) -> db.WorkOrder:
        mat = self.model.materials[material_code]
        mat_row = session.scalar(select(db.Material).where(db.Material.code == material_code))
        line_row = session.scalar(select(db.Line).where(db.Line.code == line_code))
        order = db.WorkOrder(order_no=f"NEW-{plant_time.timestamp()}", material_id=mat_row.id,
                             qty=qty, status="RELEASED", created_at=plant_time)
        session.add(order)
        session.flush()
        order.order_no = f"WO{order.id:07d}"
        smt = mat.kind == "PCBA"
        handling = planning.panels_for(qty, mat.boards_per_panel) if smt else qty
        for seq, op in enumerate(mat.routing, start=1):
            op_row = session.scalar(select(db.Operation).where(
                db.Operation.material_id == mat_row.id, db.Operation.code == op.op))
            last = seq == len(mat.routing)
            session.add(db.Job(
                work_order_id=order.id, seq=seq, operation_id=op_row.id, line_id=line_row.id,
                status="QUEUED", qty=handling, unit="PANEL" if smt else "UNIT",
                expected=handling * mat.boards_per_panel if smt and last else handling,
                queued_at=plant_time))
        session.flush()
        log.info("planned %s: %s x %d on %s", order.order_no, material_code, qty, line_code)
        return order

    def replenish(self, session: Session, plant_time: datetime, run: db.SimRun) -> None:
        live = session.get(db.PlantLive, 1)
        stock_now = live.payload.get("supermarket", {}) if live and live.run_id == run.id else {}
        have = run_lines(session, run)
        smt_lines = [l.code for l in self.model.lines_of_kind(AreaKind.SMT) if have is None or l.code in have]

        open_jobs = session.execute(
            select(db.Job, db.WorkOrder, db.Material.code, db.Operation.code, db.Line.code)
            .join(db.WorkOrder, db.Job.work_order_id == db.WorkOrder.id)
            .join(db.Material, db.WorkOrder.material_id == db.Material.id)
            .join(db.Operation, db.Job.operation_id == db.Operation.id)
            .join(db.Line, db.Job.line_id == db.Line.id)
            .where(db.WorkOrder.status.in_(OPEN))
        ).all()

        for pcba in self.model.pcbas():
            stock = stock_now.get(pcba.code, pcba.planning.initial_stock)
            supply = demand = 0
            for job, order, mat_code, op_code, _ in open_jobs:
                mat = self.model.materials[mat_code]
                if mat_code == pcba.code and job.seq == len(mat.routing):
                    supply += order.qty - (job.good + job.scrap)
                elif mat.pcba == pcba.code:
                    demand += job.qty - job.released
            lots = planning.smt_lots(stock, supply, demand, pcba.planning, pcba.boards_per_panel)
            for lot in lots:
                workload = {code: 0.0 for code in smt_lines}
                last: dict[str, str | None] = {code: None for code in smt_lines}
                for job, order, mat_code, op_code, line_code in sorted(open_jobs, key=lambda r: r[0].id):
                    if line_code in workload and job.status != "COMPLETED":
                        op = self.model.materials[mat_code].operation(op_code)
                        workload[line_code] += (job.qty - job.released) * op.bottleneck_s
                        last[line_code] = mat_code
                line = planning.pick_line(workload, last, pcba.code)
                order = self.create_order(session, pcba.code, lot, line, plant_time)
                for job in order.jobs:
                    open_jobs.append((job, order, pcba.code, job.operation.code, line))
