"""The KPI page: what the MES knows about a time window, condensed into mes_lab.kpi.Facts.

Everything comes from the condensed production history the edge gateway reports (time per station
and state, counts per station and job, breakdown events) and from the traceability records (scrap,
repairs, shipments). The people on site come from the roster of the shifts in the window; the
robots report their totals with the live snapshot of the plant.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from mes_lab import kpi
from mes_lab.model.schema import PlantModel

from . import models as db
from .views import Clock

WINDOWS = {"shift": None, "8h": 8, "24h": 24, "7d": 24 * 7}


class KpiViews:
    def __init__(self, model: PlantModel, seats: int):
        self.model = model
        self.seats = seats
        self.tz = ZoneInfo(model.plant.timezone)
        self._codes: dict[int, str] | None = None
        self._types: dict[int, str] | None = None

    def _station_maps(self, session: Session) -> tuple[dict[int, str], dict[int, str]]:
        if self._codes is None:
            rows = session.execute(select(db.Station.id, db.Station.code, db.StationType.code)
                                   .join(db.StationType, db.Station.station_type_id == db.StationType.id)).all()
            self._codes = {sid: code for sid, code, _ in rows}
            self._types = {sid: t for sid, _, t in rows}
        return self._codes, self._types

    def window(self, session: Session, name: str) -> tuple[datetime, datetime, Clock]:
        clock = Clock(session, self.model)
        until = clock.now
        if name not in WINDOWS:
            name = "shift"
        hours = WINDOWS[name]
        since = clock.shift_start if hours is None else until - timedelta(hours=hours)
        run = clock.run
        if run is not None and since < run.plant_start:
            since = run.plant_start                     # a run begins with an empty plant
        return since, until, clock

    def person_hours(self, since: datetime, until: datetime) -> dict[str, float]:
        """The roster of the shift that was on, hour by hour (config/crew.yaml)."""
        per_shift = kpi.staff_per_shift(self.model, self.seats)
        shifts = self.model.shifts
        out: dict[str, float] = {}
        t = since
        while t < until:
            step = min(until, t + timedelta(minutes=15))
            local = t.astimezone(self.tz).time()
            code = shifts[-1].code
            for s in shifts:
                if (s.start <= s.end and s.start <= local < s.end) or (s.start > s.end and (local >= s.start or local < s.end)):
                    code = s.code
            for role, n in per_shift[code].items():
                out[role] = out.get(role, 0.0) + n * (step - t).total_seconds() / 3600
            t = step
        return out

    def facts(self, session: Session, since: datetime, until: datetime, live: dict) -> kpi.Facts:
        codes, types = self._station_maps(session)
        hour0 = since.replace(minute=0, second=0, microsecond=0)
        state_s: dict[str, dict[str, float]] = {}
        for sid, state, sec in session.execute(
                select(db.StationStateTime.station_id, db.StationStateTime.state, func.sum(db.StationStateTime.seconds))
                .where(db.StationStateTime.hour >= hour0, db.StationStateTime.hour < until)
                .group_by(db.StationStateTime.station_id, db.StationStateTime.state)):
            state_s.setdefault(codes[sid], {})[state] = float(sec or 0.0)

        counts: dict[str, list[int]] = {}
        by_material: dict[tuple[str, str], list[int]] = {}
        earned: dict[str, float] = {}
        q = (select(db.ProductionCount.station_id, db.Material.code, db.OperationStep.ideal_cycle_s,
                    func.sum(db.ProductionCount.processed), func.sum(db.ProductionCount.good),
                    func.sum(db.ProductionCount.bad), func.sum(db.ProductionCount.false_calls))
             .join(db.Station, db.Station.id == db.ProductionCount.station_id)
             .outerjoin(db.Job, db.Job.id == db.ProductionCount.job_id)
             .outerjoin(db.WorkOrder, db.WorkOrder.id == db.Job.work_order_id)
             .outerjoin(db.Material, db.Material.id == db.WorkOrder.material_id)
             .outerjoin(db.OperationStep, and_(db.OperationStep.operation_id == db.Job.operation_id,
                                               db.OperationStep.station_type_id == db.Station.station_type_id))
             .where(db.ProductionCount.hour >= hour0, db.ProductionCount.hour < until)
             .group_by(db.ProductionCount.station_id, db.Material.code, db.OperationStep.ideal_cycle_s))
        stypes = self.model.station_types
        for sid, mat, ideal, p, g, b, fc in session.execute(q):
            code = codes[sid]
            vals = (int(p or 0), int(g or 0), int(b or 0), int(fc or 0))
            acc = counts.setdefault(code, [0, 0, 0, 0])
            accm = by_material.setdefault((code, mat or ""), [0, 0, 0, 0])
            for i, v in enumerate(vals):
                acc[i] += v
                accm[i] += v
            if ideal is None:
                ideal = stypes[types[sid]].default_cycle_s or 0.0
            earned[code] = earned.get(code, 0.0) + vals[0] * float(ideal)

        down: dict[str, tuple[int, float]] = {}
        end = func.coalesce(db.DowntimeEvent.end_at, until)
        for sid, n, sec in session.execute(
                select(db.DowntimeEvent.station_id, func.count(),
                       func.sum(func.extract("epoch", end - func.greatest(db.DowntimeEvent.start_at, since))))
                .where(db.DowntimeEvent.state == "DOWN", db.DowntimeEvent.start_at < until, end > since)
                .group_by(db.DowntimeEvent.station_id)):
            down[codes[sid]] = (int(n), float(sec or 0.0))

        scrapped = {mat: int(n) for mat, n in session.execute(
            select(db.Material.code, func.count()).select_from(db.Unit)
            .join(db.Material, db.Material.id == db.Unit.material_id)
            .where(db.Unit.status == "SCRAPPED", db.Unit.kind.in_(("UNIT", "BOARD")),
                   db.Unit.updated_at >= since, db.Unit.updated_at < until)
            .group_by(db.Material.code))}
        shipped = {mat: int(n) for mat, n in session.execute(
            select(db.Material.code, func.count()).select_from(db.Unit)
            .join(db.Material, db.Material.id == db.Unit.material_id)
            .join(db.Shipment, db.Shipment.id == db.Unit.shipment_id)
            .where(db.Unit.kind == "UNIT", db.Shipment.departed_at >= since, db.Shipment.departed_at < until)
            .group_by(db.Material.code))}
        repair_ids = [sid for sid, t in types.items() if stypes[t].kind == "repair"]
        repairs = session.scalar(select(func.count()).select_from(db.OperationRecord)
                                 .where(db.OperationRecord.station_id.in_(repair_ids),
                                        db.OperationRecord.ended_at >= since, db.OperationRecord.ended_at < until)) or 0

        hours = max(1e-6, (until - since).total_seconds() / 3600)
        fleet = (live or {}).get("fleet") or {}
        robots = {}
        if fleet:
            elapsed = max(1.0, fleet.get("elapsed_s", 0.0))
            share = min(1.0, hours * 3600 / elapsed)            # the totals are since the run began
            robots = {"n": fleet.get("n", 0), "busy_s": fleet.get("busy_s", 0.0) * share,
                      "transports": int(fleet.get("transports", 0) * share), "by_kind": fleet.get("by_kind", {})}
        return kpi.Facts(hours=hours, state_s=state_s, counts={k: tuple(v) for k, v in counts.items()},
                         by_material={k: tuple(v) for k, v in by_material.items()}, earned_s=earned, down=down,
                         person_h=self.person_hours(since, until), robots=robots, shipped=shipped,
                         scrapped=scrapped, repairs=int(repairs))

    def page(self, session: Session, window: str) -> dict:
        since, until, clock = self.window(session, window)
        live_row = session.get(db.PlantLive, 1)
        live = live_row.payload if live_row else {}
        facts = self.facts(session, since, until, live)
        result = kpi.compute(self.model, facts)
        return {"clock": clock.as_dict(), "window": {"name": window if window in WINDOWS else "shift",
                                                      "since": clock.iso(since), "until": clock.iso(until),
                                                      "hours": facts.hours},
                **result}
