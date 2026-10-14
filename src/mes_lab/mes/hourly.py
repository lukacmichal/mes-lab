"""The hour-by-hour board of a line: what it should have made, what it made.

Every job line hangs a screen over its aisle with the plan of the hour next to the real output, the
way plants run their lines ("hour by hour", "Stundenplan"). The MES plans from what it dispatched:

* the ideal rate of a job is the rate of its constraint: the slowest station type of the operation,
  a cell of two identical machines counting as twice as fast. An SMT line spends the time of both
  sides of a double-sided board per panel, so its rate in finished boards is boards per panel over the
  sum of both sides;
* while a job runs, the plan grows at that rate times the plan efficiency of the plant
  (`plant.plan_efficiency`, the losses a good shift still has: changeovers, small stops). A job owns the
  line from its start until the next job starts (two jobs overlap at the hand-over between them); time
  without a job is not planned;
* the output is the good count of the line's output station (finished boards at the depaneler, units
  at final inspection), the same number as on the line page.

The numbers are read from the database every few seconds and kept for everybody who asks.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from mes_lab.model.schema import AreaKind, PlantModel

from . import models as db

CACHE_S = 8.0


def ideal_rates(model: PlantModel) -> dict[tuple[str, str], float]:
    """(line, material) -> output pieces per second at the ideal rate of the constraint."""
    out: dict[tuple[str, str], float] = {}
    for line in model.lines.values():
        if not line.is_job_line:
            continue
        count: dict[str, int] = {}
        for st in line.stations:
            count[st.type] = count.get(st.type, 0) + 1
        for mat in model.materials.values():
            ops = [op for op in mat.routing if all(t in count for t in op.steps)]
            if not ops:
                continue
            per_piece = 0.0
            for op in ops:
                per_piece += max(sec / count[t] for t, sec in op.steps.items())
            pieces = mat.boards_per_panel if line.area_kind == AreaKind.SMT else 1
            if per_piece > 0:
                out[(line.code, mat.code)] = pieces / per_piece
    return out


def _overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> float:
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds())


class HourlyBoards:
    def __init__(self, model: PlantModel):
        self.model = model
        self.rates = ideal_rates(model)
        self.efficiency = model.plant.plan_efficiency
        self._lock = threading.Lock()
        self._cache: tuple[float, dict] | None = None

    def plan(self, session: Session, line: db.Line, hours: list[datetime], now: datetime) -> dict[datetime, float]:
        """Planned output of a line per hour (each hour only up to `now`)."""
        if not hours:
            return {}
        t0, t1 = hours[0], min(hours[-1] + timedelta(hours=1), now)
        rows = session.execute(
            select(db.Job.started_at, db.Job.completed_at, db.Material.code)
            .join(db.WorkOrder, db.Job.work_order_id == db.WorkOrder.id)
            .join(db.Material, db.WorkOrder.material_id == db.Material.id)
            .where(db.Job.line_id == line.id, db.Job.started_at.is_not(None), db.Job.started_at < t1,
                   (db.Job.completed_at.is_(None)) | (db.Job.completed_at > t0))
            .order_by(db.Job.started_at)).all()
        # also the job that started before the window and still owned the line when it began
        before = session.execute(
            select(db.Job.started_at, db.Job.completed_at, db.Material.code)
            .join(db.WorkOrder, db.Job.work_order_id == db.WorkOrder.id)
            .join(db.Material, db.WorkOrder.material_id == db.Material.id)
            .where(db.Job.line_id == line.id, db.Job.started_at.is_not(None), db.Job.started_at <= t0)
            .order_by(db.Job.started_at.desc()).limit(1)).all()
        jobs = sorted({(s, c, m) for s, c, m in [*before, *rows]}, key=lambda r: r[0])
        out = {h: 0.0 for h in hours}
        for i, (start, done, material) in enumerate(jobs):
            rate = self.rates.get((line.code, material))
            if not rate:
                continue
            end = jobs[i + 1][0] if i + 1 < len(jobs) else now
            if done is not None:
                end = min(end, done)
            end = min(end, now)
            for h in hours:
                out[h] += _overlap(start, end, h, h + timedelta(hours=1)) * rate * self.efficiency
        return out

    def actual(self, session: Session, line_ids: list[int], since: datetime) -> dict[tuple[int, datetime], int]:
        q = (select(db.Station.line_id, db.ProductionCount.hour, func.sum(db.ProductionCount.good))
             .join(db.Station, db.ProductionCount.station_id == db.Station.id)
             .join(db.Line, and_(db.Line.id == db.Station.line_id, db.Line.output_type_id == db.Station.station_type_id))
             .where(db.ProductionCount.hour >= since, db.Station.line_id.in_(line_ids))
             .group_by(db.Station.line_id, db.ProductionCount.hour))
        return {(lid, hour): int(good or 0) for lid, hour, good in session.execute(q)}

    def boards(self, session: Session, clock) -> dict:
        """Every job line: the hours of this shift, plan and output, and the totals of this and of the
        last shift (for the shift meeting)."""
        with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < CACHE_S:
                return self._cache[1]
        now = clock.now
        shift_start = clock.shift_start
        prev_start = shift_start - timedelta(hours=8)
        first_hour = prev_start.replace(minute=0, second=0, microsecond=0)
        hours = []
        h = first_hour
        while h <= now:
            hours.append(h)
            h += timedelta(hours=1)
        lines = [l for l in session.scalars(select(db.Line).order_by(db.Line.sort)) if l.job_line]
        actual = self.actual(session, [l.id for l in lines], first_hour)
        out = {}
        for line in lines:
            plan = self.plan(session, line, hours, now)
            rows = []
            for h in hours:
                if h < shift_start.replace(minute=0, second=0, microsecond=0):
                    continue
                full = h + timedelta(hours=1) <= now
                rows.append({"hour": clock.iso(h), "label": h.astimezone(clock.tz).strftime("%H"),
                             "plan": round(plan[h]), "actual": actual.get((line.id, h), 0), "done": full})
            this = [h for h in hours if h >= shift_start.replace(minute=0, second=0, microsecond=0)]
            last = [h for h in hours if prev_start <= h < shift_start]
            out[line.code] = {
                "hours": rows,
                "shift": {"plan": round(sum(plan[h] for h in this)), "actual": sum(actual.get((line.id, h), 0) for h in this)},
                "last_shift": {"plan": round(sum(plan[h] for h in last)), "actual": sum(actual.get((line.id, h), 0) for h in last)},
            }
        result = {"lines": out, "efficiency": self.efficiency, "now": clock.iso(now),
                  "shift": {"code": clock.shift_code, "start": clock.iso(shift_start)}}
        with self._lock:
            self._cache = (time.monotonic(), result)
        return result
