"""Training scenarios in the live plant (mes_lab.training has the catalog): start one, follow it, grade it.

Start: the MES picks the target from what the plant does right now (a lot on a feeder, an AOI and an oven of
a line that runs, a shipped unit for the complaint) and sends the fault to the plant (FAULT_START). The fault
ends when it is fixed (the AOI: somebody called to it), held (the lot), when the oven broke down, or by itself
after MAX_HOURS: the plant reports that in its live snapshot.

Grade (when the trainee says done): the truth is every piece the fault may have reached, from the records of
the MES (the genealogy of the lot, what the AOI passed in the storm, what was in the oven, what the
screwdriver did in the window). Each suspect piece is
  contained - stopped by a hold made during the scenario (quarantined, scrapped or held),
  caught     - found bad by a tester of the line (not thanks to the trainee, but it did not leave),
  escaped    - left on a truck after the scenario began,
  open       - still in the plant and nothing stops it,
  before     - had left before the scenario began (for a complaint: what must be reported).
Over-containment counts the pieces the trainee's holds stopped that were not suspect. Holds the MES makes by
itself (SPC alarms) are not the trainee's: they count neither as their holds nor as their over-containment.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mes_lab import training as catalog
from mes_lab.model.schema import PlantModel

from . import models as db
from .trace import Trace

log = logging.getLogger("mes_lab.training")


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


class Training:
    def __init__(self, model: PlantModel, trace: Trace):
        self.model = model
        self.trace = trace
        self.tz = ZoneInfo(model.plant.timezone)

    def running(self, session: Session) -> db.TrainingRun | None:
        return session.scalar(select(db.TrainingRun).where(db.TrainingRun.status == "RUNNING").limit(1))

    # ------------------------------------------------------------------ start
    def start(self, session: Session, scenario: str, by: str, now: datetime, run: db.SimRun | None) -> db.TrainingRun:
        if scenario not in catalog.SCENARIOS:
            raise HTTPException(404, f"unknown scenario {scenario}")
        if self.running(session) is not None:
            raise HTTPException(409, "a scenario is running: finish it first")
        if run is None:
            raise HTTPException(409, "the plant is not running")
        rng = random.Random(f"{scenario}:{now.isoformat()}")
        params = getattr(self, f"_pick_{scenario.lower()}")(session, rng, now)
        entry = db.TrainingRun(scenario=scenario, status="RUNNING", run_id=run.id, started_at=now, started_by=by,
                               params=params)
        session.add(entry)
        if scenario != "COMPLAINT":
            session.add(db.SimCommand(serial=params["target"][:40], action="FAULT_START", created_at=now,
                                      data={"kind": scenario, "target": params["target"], **catalog.FAULTS[scenario]}))
        session.commit()
        log.info("training %s started by %s: %s", scenario, by, params)
        return entry

    def _running_lines(self, session: Session, kind: str) -> list[str]:
        """Lines of the area with a job in work, the one with the most machines running first."""
        working = set(session.scalars(select(db.Line.code).join(db.Job, db.Job.line_id == db.Line.id)
                                      .join(db.Area, db.Area.id == db.Line.area_id)
                                      .where(db.Area.kind == kind, db.Job.status == "ACTIVE")))
        running = dict(session.execute(select(db.Line.code, func.count()).join(db.Station, db.Station.line_id == db.Line.id)
                                       .join(db.StationStatus, db.StationStatus.station_id == db.Station.id)
                                       .where(db.StationStatus.state == "RUNNING").group_by(db.Line.code)).all())
        return sorted(working | {code for code in running if code in self.model.lines
                                 and self.model.lines[code].area_kind.value == kind},
                      key=lambda code: (-running.get(code, 0), code))

    def _station_of(self, line: str, station_type: str) -> str:
        spec = self.model.lines[line]
        return next(st.code for st in spec.stations if st.type == station_type)

    def _pick_bad_lot(self, session: Session, rng: random.Random, now: datetime) -> dict:
        rows = session.execute(select(db.Reel.lot, db.Reel.part, db.Line.code)
                               .join(db.Station, db.Station.id == db.Reel.station_id)
                               .join(db.Line, db.Line.id == db.Station.line_id)
                               .where(db.Reel.status == "FEEDER", db.Reel.part.in_(catalog.BAD_LOT_PARTS))).all()
        if not rows:
            raise HTTPException(409, "no reel of a chip capacitor or resistor is on a machine right now: try in a moment")
        lot, part, _ = rng.choice(sorted(rows))
        lines = sorted({line for l, _, line in rows if l == lot})
        return {"target": lot, "part": part, "lines": lines}

    def _pick_aoi_storm(self, session: Session, rng: random.Random, now: datetime) -> dict:
        lines = self._running_lines(session, "SMT")
        if not lines:
            raise HTTPException(409, "no SMT line runs right now")
        line = lines[0]
        return {"target": self._station_of(line, "AOI"), "line": line}

    def _pick_oven_failure(self, session: Session, rng: random.Random, now: datetime) -> dict:
        lines = self._running_lines(session, "SMT")
        if not lines:
            raise HTTPException(409, "no SMT line runs right now")
        line = lines[0]
        return {"target": self._station_of(line, "REFLOW_OVEN"), "line": line}

    def _pick_complaint(self, session: Session, rng: random.Random, now: datetime) -> dict:
        rec, unit = db.OperationRecord, db.Unit
        rows = session.execute(
            select(unit.serial, rec.ended_at, db.Station.code, db.Shipment.asn_no, db.Shipment.customer)
            .join(rec, rec.unit_id == unit.id).join(db.Station, db.Station.id == rec.station_id)
            .join(db.Shipment, db.Shipment.id == unit.shipment_id)
            .where(unit.kind == "UNIT", unit.status == "SHIPPED", rec.step == "SCREWDRIVER",
                   rec.ended_at >= now - timedelta(days=3))
            .order_by(rec.ended_at.desc()).limit(400)).all()
        if not rows:
            raise HTTPException(409, "no unit screwed in the last days has left the plant yet: try later")
        serial, at, station, asn, customer = rng.choice(rows)
        start = at - timedelta(minutes=rng.uniform(60, 180))
        end = min(at + timedelta(minutes=rng.uniform(30, 150)), now - timedelta(minutes=5))
        name = self.model.customers[customer].name if customer in self.model.customers else customer
        return {"target": station, "serial": serial, "asn": asn, "customer": customer, "customer_name": name,
                "station": station, "window_from": _iso(start), "window_to": _iso(end)}

    # ------------------------------------------------------------------ while it runs
    def tick(self, session: Session, now: datetime, live: dict | None, run: db.SimRun | None) -> None:
        """The fault ended (from the plant's live snapshot), or ends now by itself (MAX_HOURS), or the plant
        started a new run (the fault went with the old one)."""
        entry = self.running(session)
        if entry is None or entry.fault_end_at is not None or entry.scenario == "COMPLAINT":
            return
        key = (entry.scenario, entry.params["target"])
        if run is not None and run.id != entry.run_id:
            entry.fault_end_at, entry.fault_end_how = now, "the plant started a new run"
        elif live:
            faults = live.get("faults") or {}
            if not any((f["kind"], f["target"]) == key for f in faults.get("active", [])):
                ended = [f for f in faults.get("ended", []) if (f["kind"], f["target"]) == key
                         and datetime.fromisoformat(f["end"]) >= entry.started_at]
                if ended:
                    entry.fault_end_at = datetime.fromisoformat(ended[-1]["end"])
                    entry.fault_end_how = ended[-1]["how"][:80]
        if entry.fault_end_at is None and entry.scenario == "BAD_LOT" and session.scalar(
                select(db.Hold.created_at).where(db.Hold.kind == "MATERIAL_LOT", db.Hold.target == entry.params["target"],
                                                 db.Hold.created_at >= entry.started_at).limit(1)):
            self._end(session, entry, now, "the lot was put on hold")
        elif entry.fault_end_at is None and now >= entry.started_at + timedelta(hours=catalog.MAX_HOURS):
            self._end(session, entry, now, "ended by itself (nobody fixed it)")
        session.commit()

    def _end(self, session: Session, entry: db.TrainingRun, now: datetime, how: str) -> None:
        entry.fault_end_at, entry.fault_end_how = now, how
        session.add(db.SimCommand(serial=entry.params["target"][:40], action="FAULT_END", created_at=now,
                                  data={"kind": entry.scenario, "target": entry.params["target"], "how": how}))

    # ------------------------------------------------------------------ the truth and the grade
    def _seeds(self, session: Session, entry: db.TrainingRun, now: datetime) -> list[int]:
        p, rec = entry.params, db.OperationRecord
        if entry.scenario == "BAD_LOT":
            return self.trace.material.lot_units(session, p["target"]) if self.trace.material else []
        station_id, _ = self.trace.station(session, p["target"])
        if entry.scenario == "AOI_STORM":
            end = entry.fault_end_at or now
            return list(session.scalars(select(rec.unit_id).distinct().where(
                rec.station_id == station_id, rec.ended_at >= entry.started_at, rec.ended_at <= end)))
        if entry.scenario == "OVEN_FAILURE":
            failed = session.scalar(select(db.DowntimeEvent.start_at).where(
                db.DowntimeEvent.station_id == station_id, db.DowntimeEvent.reason_code == catalog.FAULTS["OVEN_FAILURE"]["reason"],
                db.DowntimeEvent.start_at >= entry.started_at).order_by(db.DowntimeEvent.start_at).limit(1))
            if failed is None:
                return []
            return list(session.scalars(select(rec.unit_id).distinct().where(
                rec.station_id == station_id, rec.started_at <= failed, rec.ended_at >= failed)))
        start, end = datetime.fromisoformat(p["window_from"]), datetime.fromisoformat(p["window_to"])
        return list(session.scalars(select(rec.unit_id).distinct().where(
            rec.station_id == station_id, rec.ended_at >= start, rec.ended_at <= end)))

    def grade(self, session: Session, entry: db.TrainingRun, now: datetime) -> dict:
        ids = self.trace._affected_ids(session, self._seeds(session, entry, now))
        pieces = session.execute(text("""
            SELECT u.id, u.serial, u.kind, u.status, s.asn_no, s.departed_at,
                   EXISTS (SELECT 1 FROM hold_unit hu JOIN hold h ON h.id = hu.hold_id
                           WHERE hu.unit_id = u.id AND h.created_at >= :start AND h.created_by <> 'SPC') AS held,
                   EXISTS (SELECT 1 FROM defect d WHERE d.unit_id = u.id AND d.found_at >= :start) AS caught
              FROM unit u LEFT JOIN shipment s ON s.id = u.shipment_id
             WHERE u.id = ANY(:ids) AND (u.kind = 'UNIT' OR (u.kind = 'BOARD' AND u.status <> 'CONSUMED'))"""),
            {"ids": ids, "start": entry.started_at}).all() if ids else []
        counts = {"contained": 0, "caught": 0, "escaped": 0, "open": 0, "before": 0}
        asns_before, asns_after, examples = set(), set(), {}
        for _, serial, kind, status, asn, departed, held, caught in pieces:
            if status == "SHIPPED" and departed is not None and departed < entry.started_at:
                cat = "before"
                asns_before.add(asn)
            elif status == "SHIPPED":
                cat = "escaped"
                asns_after.add(asn)
            elif held or status in ("QUARANTINE", "SCRAPPED"):
                cat = "contained"
            elif caught:
                cat = "caught"
            else:
                cat = "open"
            counts[cat] += 1
            examples.setdefault(cat, serial)
        suspect_ids = {row[0] for row in pieces}
        holds = session.scalars(select(db.Hold).where(db.Hold.created_at >= entry.started_at, db.Hold.created_by != "SPC")
                                .order_by(db.Hold.created_at)).all()
        over = session.scalar(text("""
            SELECT count(DISTINCT u.id) FROM hold_unit hu JOIN hold h ON h.id = hu.hold_id JOIN unit u ON u.id = hu.unit_id
             WHERE h.created_at >= :start AND h.created_by <> 'SPC' AND NOT (u.id = ANY(:ids))
               AND (u.kind = 'UNIT' OR (u.kind = 'BOARD' AND u.status <> 'CONSUMED'))"""),
            {"start": entry.started_at, "ids": list(suspect_ids) or [0]}) or 0
        reachable = counts["contained"] + counts["caught"] + counts["escaped"] + counts["open"]
        score = round(100 * (counts["contained"] + counts["caught"]) / reachable) if reachable else None
        first = holds[0].created_at if holds else None
        out = {
            "graded_at": _iso(now), "suspects": len(pieces), "counts": counts, "score": score,
            "over_contained": int(over), "examples": examples,
            "holds": [{"id": h.id, "kind": h.kind, "target": h.target, "affected": h.affected, "at": _iso(h.created_at),
                       "by": h.created_by, "from": _iso(h.window_from), "to": _iso(h.window_to)} for h in holds],
            "first_hold_min": round((first - entry.started_at).total_seconds() / 60, 1) if first else None,
            "fault_minutes": round(((entry.fault_end_at or now) - entry.started_at).total_seconds() / 60, 1),
            "asns_to_report": sorted(a for a in asns_before | asns_after if a),
        }
        if entry.scenario == "OVEN_FAILURE":           # panels still in the oven have no record of it yet
            out["oven_in_repair"] = session.scalar(text("""
                SELECT count(*) FROM downtime_event e JOIN station s ON s.id = e.station_id
                 WHERE s.code = :code AND e.end_at IS NULL AND e.start_at >= :start"""),
                {"code": entry.params["target"], "start": entry.started_at}) > 0
        if entry.scenario == "COMPLAINT":
            told = {a.strip().upper() for a in (entry.answer or {}).get("asns", []) if a.strip()}
            truth = set(out["asns_to_report"])
            out["report"] = {"right": sorted(told & truth), "missed": sorted(truth - told), "wrong": sorted(told - truth)}
        return out

    def finish(self, session: Session, by: str, now: datetime, asns: list[str], abort: bool) -> db.TrainingRun:
        entry = self.running(session)
        if entry is None:
            raise HTTPException(409, "no scenario is running")
        if entry.fault_end_at is None and entry.scenario != "COMPLAINT":
            self._end(session, entry, now, "the scenario was ended")
        entry.answer = {"asns": [a.strip().upper() for a in asns if a.strip()][:50]}
        entry.status = "ABORTED" if abort else "DONE"
        entry.finished_at, entry.finished_by = now, by
        entry.result = None if abort else self.grade(session, entry, now)
        session.commit()
        log.info("training %s #%d %s by %s: %s", entry.scenario, entry.id, entry.status.lower(), by,
                 (entry.result or {}).get("counts"))
        return entry

    def release_holds(self, session: Session, run_id: int, by: str, now: datetime) -> int:
        """After the grade: the holds made during a scenario go back to production (the pieces were sorted),
        or the live plant would keep waiting for them."""
        entry = session.get(db.TrainingRun, run_id)
        if entry is None or entry.status == "RUNNING":
            raise HTTPException(409, "grade the scenario first")
        until = entry.finished_at or now
        holds = session.scalars(select(db.Hold).where(db.Hold.status == "ACTIVE", db.Hold.created_by != "SPC",
                                                      db.Hold.created_at >= entry.started_at, db.Hold.created_at <= until)).all()
        for hold in holds:
            self.trace.release_hold(session, hold.id, "RETURN", by, now)
        return len(holds)

    def active_holds(self, session: Session, entry: db.TrainingRun) -> int:
        if entry.status == "RUNNING":
            return 0
        return session.scalar(select(func.count()).select_from(db.Hold).where(
            db.Hold.status == "ACTIVE", db.Hold.created_by != "SPC", db.Hold.created_at >= entry.started_at,
            db.Hold.created_at <= (entry.finished_at or entry.started_at))) or 0

    # ------------------------------------------------------------------ the page
    def out(self, entry: db.TrainingRun, now: datetime) -> dict:
        return {"id": entry.id, "scenario": entry.scenario, "status": entry.status, "params": entry.params,
                "started_at": _iso(entry.started_at), "started_by": entry.started_by,
                "fault_end_at": _iso(entry.fault_end_at), "fault_end_how": entry.fault_end_how,
                "finished_at": _iso(entry.finished_at), "finished_by": entry.finished_by,
                "minutes": round(((entry.finished_at or now) - entry.started_at).total_seconds() / 60, 1),
                "answer": entry.answer, "result": entry.result}

    def view(self, session: Session, now: datetime) -> dict:
        entry = self.running(session)
        live = None
        if entry is not None:
            holds = session.scalars(select(db.Hold).where(db.Hold.created_at >= entry.started_at, db.Hold.created_by != "SPC")
                                    .order_by(db.Hold.created_at)).all()
            live = {**self.out(entry, now), "holds": [{"id": h.id, "kind": h.kind, "target": h.target, "affected": h.affected,
                                                      "at": _iso(h.created_at)} for h in holds]}
        past = session.scalars(select(db.TrainingRun).where(db.TrainingRun.status != "RUNNING")
                               .order_by(db.TrainingRun.id.desc()).limit(20)).all()
        return {"running": live, "past": [{**self.out(e, now), "active_holds": self.active_holds(session, e)} for e in past],
                "scenarios": {code: {"name": s["name"], "brief": s["brief"], "hints": s["hints"]}
                              for code, s in catalog.SCENARIOS.items()},
                "max_hours": catalog.MAX_HOURS}
