"""Read models for the web pages (JSON served under /api/v1)."""

from __future__ import annotations

import functools
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from mes_lab.model.schema import PlantModel

from . import models as db
from . import floor, health
from .control import current_run
from .hourly import HourlyBoards

OUTPUT_UNIT = {"SMT": "boards", "FA": "units", "PACK": "packed", "REPAIR": "repairs"}
STALE_AFTER = timedelta(seconds=15)


class Clock:
    """Plant time as last reported by the simulation, with the current shift."""

    def __init__(self, session: Session, model: PlantModel, now_wall: datetime | None = None):
        self.tz = ZoneInfo(model.plant.timezone)
        self.run = current_run(session)
        live = session.get(db.PlantLive, 1)
        now_wall = now_wall or datetime.now(UTC)
        if self.run is not None:
            self.now = self.run.last_plant_time
        else:
            self.now = now_wall
        if live is not None and live.plant_time > self.now:
            self.now = live.plant_time
        self.connected = bool(self.run and now_wall - self.run.last_seen_wall < STALE_AFTER)
        self.hour = self.now.replace(minute=0, second=0, microsecond=0)
        local = self.now.astimezone(self.tz)
        self.shift_code, self.shift_name, self.shift_start = self._shift(session, local)

    def _shift(self, session: Session, local: datetime):
        for shift in session.scalars(select(db.Shift).order_by(db.Shift.start)):
            start = local.replace(hour=shift.start.hour, minute=shift.start.minute,
                                  second=0, microsecond=0)
            if shift.start <= shift.end:
                if shift.start <= local.time() < shift.end:
                    return shift.code, shift.name, start.astimezone(UTC)
            else:  # crosses midnight
                if local.time() >= shift.start:
                    return shift.code, shift.name, start.astimezone(UTC)
                if local.time() < shift.end:
                    return shift.code, shift.name, (start - timedelta(days=1)).astimezone(UTC)
        return "-", "-", self.hour

    def iso(self, value: datetime | None) -> str | None:
        return value.astimezone(self.tz).isoformat(timespec="seconds") if value else None

    def as_dict(self) -> dict:
        return {
            "plant_time": self.iso(self.now),
            "shift": {"code": self.shift_code, "name": self.shift_name,
                      "start": self.iso(self.shift_start)},
            "connected": self.connected,
            "run_id": self.run.id if self.run else None,
            "speed": self.run.speed if self.run else None,
            "desired_speed": self.run.desired_speed if self.run else None,
            "mes_errors": health.ERRORS.summary(),
            "control": floor.CONTROL.state(),
            "scenario": _changed(self.run.knobs) if self.run else None,     # the live plant runs a tuning scenario
        }


@functools.lru_cache(maxsize=1)
def _base_knobs() -> tuple:
    from mes_lab import tuning
    return tuple(sorted(tuning.defaults().items()))


def _changed(knobs: dict | None) -> dict | None:
    """The knobs of a scenario that differ from the plant as configured (None: none)."""
    if not knobs:
        return None
    base = dict(_base_knobs())
    out = {k: v for k, v in knobs.items() if base.get(k) != v}
    return out or None


def line_status(stations: list[dict], job_line: bool) -> tuple[str, str | None]:
    states = [s["state"] for s in stations]
    if not states:
        return "UNKNOWN", None
    for wanted in ("DOWN", "MAINTENANCE", "CHANGEOVER"):
        hit = next((s for s in stations if s["state"] == wanted), None)
        if hit:
            return wanted, f'{hit["tag"]}: {hit["reason_name"] or wanted.title()}'
    if job_line:
        loader = stations[0]
        if loader["state"] == "STARVED" and loader["reason"] == "NO_MATERIAL":
            return "NO_MATERIAL", ("Waiting for bare PCB panels" if loader["type"] == "LOADER"
                                   else "Waiting for PCB assemblies")
        if all(s in ("NO_ORDER", "STARVED") for s in states) and "NO_ORDER" in states:
            return "NO_ORDER", None
    if any(s in ("RUNNING", "BLOCKED", "MINOR_STOP") for s in states):
        return "RUNNING", None
    return "IDLE", None


class Views:
    def __init__(self, model: PlantModel):
        self.model = model
        self.hourly = HourlyBoards(model)

    # ------------------------------------------------------------------ helpers
    def _station_rows(self, session: Session, line_ids: list[int] | None = None) -> dict[int, list[dict]]:
        q = (select(db.Station, db.StationType, db.StationStatus, db.DowntimeReason.name)
             .join(db.StationType, db.Station.station_type_id == db.StationType.id)
             .outerjoin(db.StationStatus, db.StationStatus.station_id == db.Station.id)
             .outerjoin(db.DowntimeReason, db.DowntimeReason.code == db.StationStatus.reason_code)
             .order_by(db.Station.line_id, db.Station.sort))
        if line_ids is not None:
            q = q.where(db.Station.line_id.in_(line_ids))
        out: dict[int, list[dict]] = {}
        for st, stype, status, reason_name in session.execute(q):
            out.setdefault(st.line_id, []).append({
                "id": st.id, "code": st.code, "name": st.name, "tag": st.code.split("-")[-1],
                "type": stype.code, "kind": stype.kind, "cell": st.cell,
                "state": status.state if status else "UNKNOWN",
                "reason": status.reason_code if status else None,
                "reason_name": reason_name,
                "since": status.since if status else None,
                "item": status.item if status else None,
                "job_id": status.job_id if status else None,
            })
        return out

    def _jobs(self, session: Session, statuses: tuple[str, ...], line_id: int | None = None,
              limit: int | None = None) -> list[dict]:
        q = (select(db.Job, db.WorkOrder, db.Material, db.Operation, db.Line.code)
             .join(db.WorkOrder, db.Job.work_order_id == db.WorkOrder.id)
             .join(db.Material, db.WorkOrder.material_id == db.Material.id)
             .join(db.Operation, db.Job.operation_id == db.Operation.id)
             .join(db.Line, db.Job.line_id == db.Line.id)
             .where(db.Job.status.in_(statuses))
             .order_by(db.Job.id))
        if line_id is not None:
            q = q.where(db.Job.line_id == line_id)
        if limit:
            q = q.limit(limit)
        return [{
            "id": job.id, "order_no": order.order_no, "material": mat.code,
            "material_name": mat.name, "operation": op.code, "operation_name": op.name,
            "line": line_code, "line_id": job.line_id, "status": job.status, "qty": job.qty,
            "unit": job.unit, "released": job.released, "good": job.good, "scrap": job.scrap,
            "in_repair": job.in_repair, "expected": job.expected,
            "piece": "boards" if job.unit == "PANEL" and job.expected != job.qty else
                     ("panels" if job.unit == "PANEL" else "units"),
            "queued_at": job.queued_at, "started_at": job.started_at,
        } for job, order, mat, op, line_code in session.execute(q)]

    def _output_by_line_hour(self, session: Session, since: datetime,
                             line_ids: list[int] | None = None) -> dict[tuple[int, datetime], dict]:
        q = (select(db.Station.line_id, db.ProductionCount.hour,
                    func.sum(db.ProductionCount.good), func.sum(db.ProductionCount.bad))
             .join(db.Station, db.ProductionCount.station_id == db.Station.id)
             .join(db.Line, and_(db.Line.id == db.Station.line_id,
                                 db.Line.output_type_id == db.Station.station_type_id))
             .where(db.ProductionCount.hour >= since)
             .group_by(db.Station.line_id, db.ProductionCount.hour))
        if line_ids is not None:
            q = q.where(db.Station.line_id.in_(line_ids))
        return {(line_id, hour): {"good": int(good or 0), "bad": int(bad or 0)}
                for line_id, hour, good, bad in session.execute(q)}

    @staticmethod
    def _job_out(clock: Clock, job: dict) -> dict:
        return {**job, "queued_at": clock.iso(job["queued_at"]), "started_at": clock.iso(job["started_at"])}

    # ------------------------------------------------------------------ overview
    def overview(self, session: Session) -> dict:
        clock = Clock(session, self.model)
        live_row = session.get(db.PlantLive, 1)
        live = live_row.payload if live_row else {}
        stations = self._station_rows(session)
        active = self._jobs(session, ("ACTIVE",))
        queued = session.execute(select(db.Job.line_id, func.count()).where(db.Job.status == "QUEUED")
                                 .group_by(db.Job.line_id)).all()
        queued = dict(queued)
        prev_hour = clock.hour - timedelta(hours=1)
        output = self._output_by_line_hour(session, min(prev_hour, clock.shift_start))

        run = session.scalar(select(db.SimRun).order_by(db.SimRun.started_wall.desc()).limit(1))
        have = set(run.lines) if run is not None and run.lines else None
        areas = []
        for area in session.scalars(select(db.Area).order_by(db.Area.sort)):
            lines = []
            for line in area.lines:
                st = stations.get(line.id, [])
                status, detail = line_status(st, line.job_line)
                if have is not None and line.code not in have:
                    status, detail = "OFF", "Not in the live plant (tuning scenario)"
                loading_id = (live.get("lines", {}).get(line.code) or {}).get("job_id")
                line_jobs = [j for j in active if j["line_id"] == line.id]
                current = next((j for j in line_jobs if j["id"] == loading_id), None) or \
                    (line_jobs[-1] if line_jobs else None)
                shift_total = sum(v["good"] for (lid, hour), v in output.items()
                                  if lid == line.id and hour >= clock.shift_start)
                lines.append({
                    "code": line.code, "name": line.name, "status": status, "detail": detail,
                    "job": self._job_out(clock, current) if current else None,
                    "active_jobs": len(line_jobs),
                    "queue": queued.get(line.id, 0),
                    "output": {
                        "this_hour": output.get((line.id, clock.hour), {}).get("good", 0),
                        "last_hour": output.get((line.id, prev_hour), {}).get("good", 0),
                        "shift": shift_total,
                        "unit": OUTPUT_UNIT.get(area.kind, "units"),
                    },
                    "wip": (live.get("lines", {}).get(line.code) or {}).get("wip"),
                    "stations": [{"code": s["code"], "tag": s["tag"], "name": s["name"], "type": s["type"],
                                  "state": s["state"], "reason_name": s["reason_name"],
                                  "since": clock.iso(s["since"]), "item": s["item"], "job_id": s["job_id"]}
                                 for s in st],
                })
            areas.append({"code": area.code, "name": area.name, "kind": area.kind, "lines": lines})

        safety = {m.code: m.planning.safety_stock for m in self.model.pcbas()}
        supermarket = [{"material": code, "stock": stock, "safety": safety.get(code, 0)}
                       for code, stock in live.get("supermarket", {}).items()]
        warehouse = [{"material": code, **values} for code, values in live.get("warehouse", {}).items()]
        down = sum(1 for st in stations.values() for s in st if s["state"] == "DOWN")
        fa_shift = sum(l["output"]["shift"] for a in areas if a["kind"] == "FA" for l in a["lines"])
        packed_shift = sum(l["output"]["shift"] for a in areas if a["kind"] == "PACK" for l in a["lines"])
        open_orders = session.scalar(select(func.count()).select_from(db.WorkOrder)
                                     .where(db.WorkOrder.status.in_(("RELEASED", "IN_PROGRESS"))))
        return {
            "plant": {"code": self.model.plant.code, "name": self.model.plant.name},
            "clock": clock.as_dict(),
            "areas": areas,
            "supermarket": supermarket,
            "warehouse": warehouse,
            "repair": live.get("repair", {}),
            "washing": live.get("washing", 0),
            "kpis": {"fa_output_shift": fa_shift, "packed_shift": packed_shift,
                     "down_stations": down, "open_orders": open_orders},
        }

    # ------------------------------------------------------------------ line
    def line(self, session: Session, code: str) -> dict | None:
        line = session.scalar(select(db.Line).where(db.Line.code == code))
        if line is None:
            return None
        clock = Clock(session, self.model)
        live_row = session.get(db.PlantLive, 1)
        live_line = ((live_row.payload if live_row else {}).get("lines", {}) or {}).get(code) or {}
        stations = self._station_rows(session, [line.id]).get(line.id, [])
        station_ids = [s["id"] for s in stations]
        shift_start = clock.shift_start

        state_rows = session.execute(
            select(db.StationStateTime.station_id, db.StationStateTime.state,
                   func.sum(db.StationStateTime.seconds))
            .where(db.StationStateTime.station_id.in_(station_ids),
                   db.StationStateTime.hour >= shift_start.replace(minute=0, second=0, microsecond=0))
            .group_by(db.StationStateTime.station_id, db.StationStateTime.state)).all()
        shift_states: dict[int, dict[str, float]] = {}
        for sid, state, seconds in state_rows:
            shift_states.setdefault(sid, {})[state] = round(float(seconds), 1)

        count_rows = session.execute(
            select(db.ProductionCount.station_id, func.sum(db.ProductionCount.processed),
                   func.sum(db.ProductionCount.good), func.sum(db.ProductionCount.bad),
                   func.sum(db.ProductionCount.false_calls))
            .where(db.ProductionCount.station_id.in_(station_ids),
                   db.ProductionCount.hour >= shift_start)
            .group_by(db.ProductionCount.station_id)).all()
        shift_counts = {sid: {"processed": int(p), "good": int(g), "bad": int(b), "false_calls": int(f)}
                        for sid, p, g, b, f in count_rows}

        buffers = live_line.get("buffers") or []
        cells: list[dict] = []
        for s in stations:
            if not cells or cells[-1]["index"] != s["cell"]:
                cells.append({"index": s["cell"], "type": s["type"],
                              "buffer": buffers[s["cell"]] if s["cell"] < len(buffers) else None,
                              "stations": []})
            cells[-1]["stations"].append({
                **{k: v for k, v in s.items() if k not in ("id", "since")},
                "since": clock.iso(s["since"]),
                "shift": shift_counts.get(s["id"], {"processed": 0, "good": 0, "bad": 0, "false_calls": 0}),
                "states": shift_states.get(s["id"], {}),
            })

        since = clock.hour - timedelta(hours=11)
        output = self._output_by_line_hour(session, since, [line.id])
        hours = [since + timedelta(hours=i) for i in range(12)]
        plan = self.hourly.plan(session, line, hours, clock.now) if line.job_line else {}
        hourly = []
        for hour in hours:
            values = output.get((line.id, hour), {"good": 0, "bad": 0})
            hourly.append({"hour": clock.iso(hour), "label": hour.astimezone(clock.tz).strftime("%H:%M"),
                           **values, "current": hour == clock.hour,
                           "plan": round(plan[hour]) if hour in plan else None})

        events = session.execute(
            select(db.DowntimeEvent, db.Station.code, db.DowntimeReason.name)
            .join(db.Station, db.DowntimeEvent.station_id == db.Station.id)
            .outerjoin(db.DowntimeReason, db.DowntimeReason.code == db.DowntimeEvent.reason_code)
            .where(db.Station.line_id == line.id)
            .order_by(db.DowntimeEvent.start_at.desc()).limit(20)).all()
        event_rows = [{
            "station": st_code, "state": ev.state, "reason": ev.reason_code, "reason_name": name,
            "start": clock.iso(ev.start_at), "end": clock.iso(ev.end_at),
            "duration_s": ((ev.end_at or clock.now) - ev.start_at).total_seconds(),
        } for ev, st_code, name in events]

        defects = session.execute(
            select(db.DefectCount.defect_code, db.DefectCode.name, func.sum(db.DefectCount.found))
            .join(db.DefectCode, db.DefectCode.code == db.DefectCount.defect_code)
            .where(db.DefectCount.station_id.in_(station_ids), db.DefectCount.hour >= shift_start)
            .group_by(db.DefectCount.defect_code, db.DefectCode.name)
            .order_by(func.sum(db.DefectCount.found).desc()).limit(8)).all()

        status, detail = line_status(stations, line.job_line)
        area = line.area
        return {
            "line": {"code": line.code, "name": line.name, "area": area.code, "area_name": area.name,
                     "kind": area.kind, "job_line": line.job_line, "buffer_capacity": line.buffer,
                     "output_unit": OUTPUT_UNIT.get(area.kind, "units")},
            "clock": clock.as_dict(),
            "status": status, "detail": detail,
            "wip": live_line.get("wip"),
            "loading_job_id": live_line.get("job_id"),
            "cells": cells,
            "jobs": {
                "active": [self._job_out(clock, j) for j in self._jobs(session, ("ACTIVE",), line.id)],
                "queued": [self._job_out(clock, j) for j in self._jobs(session, ("QUEUED",), line.id, 8)],
            },
            "hourly": hourly,
            "events": event_rows,
            "defects": [{"code": c, "name": n, "found": int(f)} for c, n, f in defects],
        }

    # ------------------------------------------------------------------ andon
    def andon(self, session: Session, crew: dict, rule) -> dict:
        clock = Clock(session, self.model)
        run = clock.run
        t_plant = (lambda t: clock.iso(run.plant_start + timedelta(seconds=t))) if run is not None else (lambda t: None)
        names = {st.code: st.name for st in session.scalars(select(db.Station))}
        reasons = {r.code: r.name for r in session.scalars(select(db.DowntimeReason))}
        open_calls = [{"station": c["st"], "name": names.get(c["st"]), "reason": reasons.get(c["r"], c["r"]), "role": c["role"],
                       "kind": c["kind"], "phase": c["ph"], "level": c.get("lv", 1), "since": t_plant(c["t"])}
                      for c in crew.get("calls") or []]
        esc = [{"station": e["st"], "level": e["lv"], "at": t_plant(e["t"]), "reason": reasons.get(e.get("r"), e.get("r")), "person": e.get("p"),
                "how": e.get("how")} for e in reversed(crew.get("esc") or [])]
        since = clock.now - timedelta(hours=24)
        rows = session.execute(
            select(db.DowntimeEvent, db.Station.code, db.DowntimeReason.name)
            .join(db.Station, db.DowntimeEvent.station_id == db.Station.id)
            .outerjoin(db.DowntimeReason, db.DowntimeReason.code == db.DowntimeEvent.reason_code)
            .where(db.DowntimeEvent.state == "DOWN", db.DowntimeEvent.start_at >= since)
            .order_by(db.DowntimeEvent.start_at.desc())).all()
        history, counts = [], {1: 0, 2: 0, 3: 0}
        for ev, code, reason in rows:
            minutes = ((ev.end_at or clock.now) - ev.start_at).total_seconds() / 60
            level = 3 if minutes >= rule.manager_min else 2 if minutes >= rule.foreman_min else 1
            counts[level] += 1
            if level >= 2:
                history.append({"station": code, "reason": reason or ev.reason_code, "start": clock.iso(ev.start_at),
                                "end": clock.iso(ev.end_at), "minutes": round(minutes, 1), "level": level,
                                "open": ev.end_at is None})
        return {"clock": clock.as_dict(), "rule": {"foreman_min": rule.foreman_min, "manager_min": rule.manager_min,
                "meeting_at_min": rule.meeting_at_min, "meeting_min": rule.meeting_min},
                "open": open_calls, "escalations": esc, "history": history[:60], "counts": counts,
                "meeting": bool(crew.get("meeting"))}

    # ------------------------------------------------------------------ orders
    def orders(self, session: Session, status: str | None = None, limit: int = 200) -> dict:
        clock = Clock(session, self.model)
        q = (select(db.WorkOrder).order_by(db.WorkOrder.id.desc()).limit(limit)
             .options(selectinload(db.WorkOrder.material),
                      selectinload(db.WorkOrder.jobs).selectinload(db.Job.operation),
                      selectinload(db.WorkOrder.jobs).selectinload(db.Job.line)))
        if status == "open":
            q = q.where(db.WorkOrder.status.in_(("RELEASED", "IN_PROGRESS")))
        elif status:
            q = q.where(db.WorkOrder.status == status.upper())
        rows = []
        for order in session.scalars(q):
            rows.append({
                "order_no": order.order_no, "material": order.material.code,
                "material_name": order.material.name, "kind": order.material.kind,
                "qty": order.qty, "status": order.status, "qty_good": order.qty_good,
                "qty_scrap": order.qty_scrap, "created_at": clock.iso(order.created_at),
                "started_at": clock.iso(order.started_at), "completed_at": clock.iso(order.completed_at),
                "jobs": [{"operation": j.operation.code, "line": j.line.code, "status": j.status,
                          "released": j.released, "qty": j.qty, "good": j.good,
                          "expected": j.expected, "unit": j.unit} for j in order.jobs],
            })
        counts = dict(session.execute(select(db.WorkOrder.status, func.count())
                                      .group_by(db.WorkOrder.status)).all())
        return {"clock": clock.as_dict(), "orders": rows, "counts": counts}
