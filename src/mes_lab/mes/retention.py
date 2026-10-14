"""Keeping the database small: the simulation can run 60x faster than real time,
so unit-level history (serials, records, defects) is kept for MES_RETENTION_DAYS
plant days and hourly summaries for a year. Runs in a background thread of the
MES every few minutes; switched off when MES_RETENTION_DAYS is 0 or missing."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from mes_lab.model.schema import PlantModel

from .views import Clock

log = logging.getLogger("mes_lab.retention")

FINAL_STATES = ("CONSUMED", "PACKED", "PALLETIZED", "CLOSED", "SCRAPPED", "LOST", "DONE", "SHIPPED")
SUMMARY_DAYS = 400
BATCH = 20_000


def _batched(session: Session, sql: str, params: dict) -> int:
    total = 0
    while True:
        n = session.execute(text(sql), {**params, "batch": BATCH}).rowcount or 0
        session.commit()
        total += n
        if n < BATCH:
            return total


def purge(session: Session, now: datetime, keep_days: int) -> dict:
    cutoff = now - timedelta(days=keep_days)
    summary_cutoff = now - timedelta(days=SUMMARY_DAYS)
    done = {
        "records": _batched(session, """
            DELETE FROM operation_record WHERE id IN (
                SELECT id FROM operation_record WHERE ended_at < :cutoff LIMIT :batch)""",
            {"cutoff": cutoff}),
        "defects": _batched(session, """
            DELETE FROM defect WHERE id IN (
                SELECT id FROM defect WHERE found_at < :cutoff AND status <> 'OPEN' LIMIT :batch)""",
            {"cutoff": cutoff}),
        # links and hold memberships go with their units (ON DELETE CASCADE)
        "units": _batched(session, """
            DELETE FROM unit WHERE id IN (
                SELECT u.id FROM unit u
                WHERE u.updated_at < :cutoff AND u.status = ANY(:final)
                  AND NOT EXISTS (SELECT 1 FROM hold_unit hu JOIN hold h ON h.id = hu.hold_id
                                  WHERE hu.unit_id = u.id AND h.status = 'ACTIVE')
                LIMIT :batch)""",
            {"cutoff": cutoff, "final": list(FINAL_STATES)}),
        "shipments": _batched(session, """
            DELETE FROM shipment WHERE id IN (
                SELECT s.id FROM shipment s WHERE s.departed_at < :cutoff
                  AND NOT EXISTS (SELECT 1 FROM unit u WHERE u.shipment_id = s.id) LIMIT :batch)""",
            {"cutoff": cutoff}),
        "commands": _batched(session, """
            DELETE FROM sim_command WHERE id IN (
                SELECT id FROM sim_command WHERE acked_at < :cutoff LIMIT :batch)""",
            {"cutoff": cutoff}),
    }
    for table, column in (("station_state_time", "hour"), ("production_count", "hour"),
                          ("defect_count", "hour"), ("downtime_event", "start_at")):
        done[table] = _batched(session, f"""
            DELETE FROM {table} WHERE ctid IN (
                SELECT ctid FROM {table} WHERE {column} < :cutoff LIMIT :batch)""",
            {"cutoff": summary_cutoff})
    return done


def start(sessions: sessionmaker, model: PlantModel, keep_days: int, every_s: float = 600) -> threading.Event:
    """Run purge() every `every_s` seconds in a daemon thread; set the event to stop."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(every_s):
            try:
                with sessions() as session:
                    result = purge(session, Clock(session, model).now, keep_days)
                if any(result.values()):
                    log.info("retention: deleted %s", {k: v for k, v in result.items() if v})
            except Exception:  # noqa: BLE001 - try again next time
                log.exception("retention run failed")

    threading.Thread(target=loop, name="retention", daemon=True).start()
    return stop
