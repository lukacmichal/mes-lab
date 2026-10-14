"""Storing what the edge gateway reports. Every write is an upsert, so the same
payload can arrive twice without doing harm."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import models as db
from .schemas import IngestPayload
from .trace import Trace


class StationDirectory:
    """station code -> id, cached (master data rarely changes)."""

    def __init__(self):
        self.ids: dict[str, int] = {}

    def resolve(self, session: Session, code: str) -> int:
        if code not in self.ids:
            self.ids = dict(session.execute(select(db.Station.code, db.Station.id)).all())
        if code not in self.ids:
            raise HTTPException(422, f"unknown station {code}")
        return self.ids[code]


def _upsert(session: Session, model, rows: list[dict], keys: list[str], update: list[str]) -> None:
    if not rows:
        return
    stmt = insert(model)
    stmt = stmt.on_conflict_do_update(index_elements=keys,
                                      set_={c: getattr(stmt.excluded, c) for c in update})
    session.execute(stmt, rows)


def ingest(session: Session, stations: StationDirectory, trace: Trace, payload: IngestPayload,
           now: datetime | None = None) -> dict:
    run = session.get(db.SimRun, payload.run_id)
    if run is None:
        raise HTTPException(404, f"unknown run {payload.run_id}")
    sid = lambda code: stations.resolve(session, code)  # noqa: E731
    run_id = run.id
    trace.apply_records(session, run_id, payload.records)
    trace.apply_shipments(session, run_id, payload.shipments)

    _upsert(session, db.StationStateTime,
            [{"run_id": run_id, "station_id": sid(r.station), "hour": r.hour, "state": r.state,
              "seconds": r.seconds, "occurrences": r.occurrences} for r in payload.state_time],
            ["run_id", "station_id", "hour", "state"], ["seconds", "occurrences"])
    _upsert(session, db.ProductionCount,
            [{"run_id": run_id, "station_id": sid(r.station), "hour": r.hour, "job_id": r.job_id,
              "processed": r.processed, "good": r.good, "bad": r.bad, "false_calls": r.false_calls}
             for r in payload.counts],
            ["run_id", "station_id", "hour", "job_id"], ["processed", "good", "bad", "false_calls"])
    _upsert(session, db.DefectCount,
            [{"run_id": run_id, "station_id": sid(r.station), "hour": r.hour,
              "defect_code": r.code, "found": r.found} for r in payload.defects],
            ["run_id", "station_id", "hour", "defect_code"], ["found"])
    _upsert(session, db.DowntimeEvent,
            [{"id": e.id, "run_id": run_id, "station_id": sid(e.station), "state": e.state,
              "reason_code": e.reason, "job_id": e.job_id, "start_at": e.start, "end_at": e.end}
             for e in payload.events],
            ["id"], ["end_at", "reason_code"])
    _upsert(session, db.StationStatus,
            [{"station_id": sid(s.code), "run_id": run_id, "state": s.state, "reason_code": s.reason,
              "since": s.since, "job_id": s.job_id, "item": s.item, "updated_at": payload.plant_time}
             for s in payload.stations],
            ["station_id"], ["run_id", "state", "reason_code", "since", "job_id", "item", "updated_at"])
    if payload.live:
        _upsert(session, db.PlantLive,
                [{"id": 1, "run_id": run_id, "plant_time": payload.plant_time, "payload": payload.live}],
                ["id"], ["run_id", "plant_time", "payload"])

    touched_orders: set[int] = set()
    for progress in payload.jobs:
        job = session.get(db.Job, progress.id)
        if job is None or job.run_id != run_id:
            continue  # a job of an older run, already reset
        job.released = progress.released
        job.good = progress.good
        job.scrap = progress.scrap
        job.in_repair = progress.in_repair
        job.held = progress.held
        if progress.status == "COMPLETED" and job.status != "COMPLETED":
            job.status = "COMPLETED"
            job.completed_at = progress.completed_at or payload.plant_time
        touched_orders.add(job.work_order_id)
    for order_id in touched_orders:
        _update_order(session.get(db.WorkOrder, order_id))

    run.last_plant_time = max(run.last_plant_time, payload.plant_time)
    run.last_seen_wall = now or datetime.now(UTC)
    run.speed = payload.speed
    commands = trace.pending_commands(session, payload.acks, payload.plant_time)
    session.commit()
    return {"speed": run.desired_speed, "commands": commands}


def _update_order(order: db.WorkOrder) -> None:
    jobs = order.jobs
    final = jobs[-1]
    order.qty_good = final.good
    order.qty_scrap = sum(j.scrap for j in jobs)
    if all(j.status == "COMPLETED" for j in jobs) and order.status != "COMPLETED":
        order.status = "COMPLETED"
        order.completed_at = final.completed_at
