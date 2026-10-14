"""Read models for traceability, quality and holds pages."""

from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from mes_lab.model.schema import PlantModel

from . import models as db
from .trace import Trace
from .views import Clock

TREE_LIMIT = 400
ANCESTRY_LINKS = ("SPLIT", "ASSEMBLY")  # physical origin, not packaging

_TREE_SQL = """
WITH RECURSIVE t(id, parent, depth, link) AS (
    SELECT CAST(:id AS bigint), CAST(NULL AS bigint), 0, CAST(NULL AS varchar(10))
    UNION ALL
    SELECT l.{next}, t.id, t.depth + 1, l.kind
    FROM unit_link l JOIN t ON l.{here} = t.id
    WHERE t.depth < 6
)
SELECT t.id, t.parent, t.depth, t.link, u.serial, u.kind, u.status, m.code
FROM t JOIN unit u ON u.id = t.id JOIN material m ON m.id = u.material_id
ORDER BY t.depth, u.serial
LIMIT {limit}
"""


class TraceViews:
    def __init__(self, model: PlantModel, trace: Trace):
        self.model = model
        self.trace = trace

    # ------------------------------------------------------------------ helpers
    def _tree(self, session: Session, unit_id: int, direction: str) -> tuple[dict, list[dict], bool]:
        """Genealogy tree; up = what it was made from, down = what it went into."""
        sql = _TREE_SQL.format(next="source_id" if direction == "up" else "target_id",
                               here="target_id" if direction == "up" else "source_id",
                               limit=TREE_LIMIT + 1)
        rows = session.execute(text(sql), {"id": unit_id}).all()
        truncated = len(rows) > TREE_LIMIT
        nodes: dict[int, dict] = {}
        flat = []
        for uid, parent, depth, link, serial, kind, status, material in rows[:TREE_LIMIT]:
            node = {"serial": serial, "kind": kind, "status": status, "material": material,
                    "link": link, "depth": depth, "children": []}
            if uid in nodes:  # reached twice: keep the first
                continue
            nodes[uid] = node
            flat.append((uid, parent, node))
            if parent is not None and parent in nodes:
                nodes[parent]["children"].append(node)
        root = nodes.get(unit_id, {"children": []})
        ancestry = [unit_id]
        if direction == "up":
            wanted = {unit_id}
            for uid, parent, node in flat:
                if parent in wanted and node["link"] in ANCESTRY_LINKS:
                    wanted.add(uid)
                    ancestry.append(uid)
        return root, ancestry, truncated

    def _active_holds(self, session: Session, unit: db.Unit) -> list[dict]:
        q = select(db.Hold).join(db.HoldUnit, db.HoldUnit.hold_id == db.Hold.id).where(
            db.HoldUnit.unit_id == unit.id, db.Hold.status == "ACTIVE")
        holds = list(session.scalars(q))
        if unit.work_order_id:
            holds += list(session.scalars(select(db.Hold).where(
                db.Hold.status == "ACTIVE", db.Hold.kind == "WORK_ORDER",
                db.Hold.work_order_id == unit.work_order_id)))
        return [{"id": h.id, "kind": h.kind, "target": h.target, "reason": h.reason}
                for h in {h.id: h for h in holds}.values()]

    # ------------------------------------------------------------------ unit
    def unit(self, session: Session, serial: str) -> dict:
        clock = Clock(session, self.model)
        unit = self.trace.unit(session, serial.strip())
        if unit is None:
            raise HTTPException(404, f"{serial} not found")
        order = session.get(db.WorkOrder, unit.work_order_id) if unit.work_order_id else None
        up, ancestry, up_cut = self._tree(session, unit.id, "up")
        down, _, down_cut = self._tree(session, unit.id, "down")
        serials = dict(session.execute(select(db.Unit.id, db.Unit.serial)
                                       .where(db.Unit.id.in_(ancestry))).all())
        records = session.execute(
            select(db.OperationRecord, db.Station.code)
            .join(db.Station, db.Station.id == db.OperationRecord.station_id)
            .where(db.OperationRecord.unit_id.in_(ancestry))
            .order_by(db.OperationRecord.ended_at, db.OperationRecord.id).limit(400)).all()
        defects = session.execute(
            select(db.Defect, db.DefectCode.name, db.Station.code)
            .join(db.DefectCode, db.DefectCode.code == db.Defect.code)
            .join(db.Station, db.Station.id == db.Defect.station_id)
            .where(db.Defect.unit_id.in_(ancestry)).order_by(db.Defect.found_at)).all()
        station = session.get(db.Station, unit.station_id) if unit.station_id else None
        units_in = {}
        for r, _ in records:
            units_in.setdefault(r.unit_id, serials.get(r.unit_id))
        return {
            "clock": clock.as_dict(),
            "unit": {
                "serial": unit.serial, "kind": unit.kind, "material": unit.material.code,
                "material_name": unit.material.name, "status": unit.status,
                "location": unit.location, "station": station.code if station else None,
                "work_order": order.order_no if order else None,
                "operation": unit.operation, "completed": unit.completed or [],
                "steps": self.trace.steps(unit.material.code, unit.operation),
                "quarantine_reason": unit.quarantine_reason, "qty": unit.qty,
                "created_at": clock.iso(unit.created_at), "updated_at": clock.iso(unit.updated_at),
            },
            "holds": self._active_holds(session, unit),
            "up": up, "down": down, "truncated": up_cut or down_cut,
            "history": [{
                "serial": serials.get(r.unit_id), "station": code, "step": r.step,
                "operation": r.operation, "start": clock.iso(r.started_at), "end": clock.iso(r.ended_at),
                "result": r.result, "action": r.action, "measurements": r.measurements or {},
            } for r, code in records],
            "defects": [{
                "serial": serials.get(d.unit_id), "code": d.code, "name": name, "station": code,
                "found_at": clock.iso(d.found_at), "status": d.status, "closed_at": clock.iso(d.closed_at),
            } for d, name, code in defects],
        }

    # ------------------------------------------------------------------ shipping
    def shipments(self, session: Session, limit: int = 30) -> dict:
        clock = Clock(session, self.model)
        rows = session.scalars(select(db.Shipment).order_by(db.Shipment.departed_at.desc(),
                                                            db.Shipment.id.desc()).limit(limit)).all()
        return {"clock": clock.as_dict(), "shipments": [self._shipment(clock, s) for s in rows]}

    @staticmethod
    def _shipment(clock: Clock, s: db.Shipment) -> dict:
        return {"asn": s.asn_no, "customer": s.customer, "plate": s.plate, "departed": clock.iso(s.departed_at),
                "pallets": s.pallets, "klt": s.klt, "units": s.units}

    def shipment(self, session: Session, asn: str) -> dict:
        clock = Clock(session, self.model)
        s = session.scalar(select(db.Shipment).where(db.Shipment.asn_no == asn.strip()))
        if s is None:
            raise HTTPException(404, f"{asn} not found")
        pallets = session.scalars(select(db.Unit.serial).where(db.Unit.shipment_id == s.id, db.Unit.kind == "PALLET")
                                  .order_by(db.Unit.serial)).all()
        return {"clock": clock.as_dict(), **self._shipment(clock, s), "pallet_serials": list(pallets)}

    def search(self, session: Session, q: str) -> dict:
        q = q.strip()
        if not q:
            return {"kind": "empty"}
        if self.trace.unit(session, q) is not None:
            return {"kind": "unit", "serial": q}
        order = session.scalar(select(db.WorkOrder).where(func.upper(db.WorkOrder.order_no) == q.upper()))
        if order is not None:
            units = session.scalars(select(db.Unit).where(db.Unit.work_order_id == order.id)
                                    .order_by(db.Unit.id).limit(300)).all()
            total = session.scalar(select(func.count()).select_from(db.Unit)
                                   .where(db.Unit.work_order_id == order.id))
            return {"kind": "order", "order_no": order.order_no, "material": order.material.code,
                    "total": total, "units": [{"serial": u.serial, "kind": u.kind, "status": u.status,
                                               "location": u.location} for u in units]}
        like = q.replace("%", "").replace("_", "\\_") + "%"
        matches = session.scalars(select(db.Unit).where(db.Unit.serial.ilike(like))
                                  .order_by(db.Unit.serial).limit(25)).all()
        return {"kind": "matches", "query": q, "matches": [
            {"serial": u.serial, "kind": u.kind, "status": u.status, "location": u.location}
            for u in matches]}

    # ------------------------------------------------------------------ quality
    def quality(self, session: Session) -> dict:
        clock = Clock(session, self.model)
        since = clock.now - timedelta(hours=24)
        open_rows = session.execute(
            select(db.Defect, db.Unit.serial, db.Unit.kind, db.Unit.location, db.DefectCode.name,
                   db.Station.code)
            .join(db.Unit, db.Unit.id == db.Defect.unit_id)
            .join(db.DefectCode, db.DefectCode.code == db.Defect.code)
            .join(db.Station, db.Station.id == db.Defect.station_id)
            .where(db.Defect.status == "OPEN", db.Unit.status == "NOK")
            .order_by(db.Defect.found_at.desc()).limit(100)).all()
        quarantine = session.execute(
            select(db.Unit, db.Material.code).join(db.Material, db.Material.id == db.Unit.material_id)
            .where(db.Unit.status == "QUARANTINE").order_by(db.Unit.updated_at.desc()).limit(200)).all()
        repairs = session.execute(
            select(db.OperationRecord, db.Unit.serial, db.Station.code)
            .join(db.Unit, db.Unit.id == db.OperationRecord.unit_id)
            .join(db.Station, db.Station.id == db.OperationRecord.station_id)
            .where(db.OperationRecord.step == "REPAIR_BENCH")
            .order_by(db.OperationRecord.ended_at.desc()).limit(30)).all()
        pareto = session.execute(
            select(db.Defect.code, db.DefectCode.name, func.count())
            .join(db.DefectCode, db.DefectCode.code == db.Defect.code)
            .where(db.Defect.found_at >= since)
            .group_by(db.Defect.code, db.DefectCode.name).order_by(func.count().desc())).all()
        count = lambda q: session.scalar(select(func.count()).select_from(q.subquery()))  # noqa: E731
        repaired_24h = count(select(db.OperationRecord.id).where(
            db.OperationRecord.step == "REPAIR_BENCH", db.OperationRecord.result == "REPAIRED",
            db.OperationRecord.ended_at >= since))
        scrapped_24h = count(select(db.Unit.id).where(db.Unit.status == "SCRAPPED",
                                                      db.Unit.updated_at >= since))
        open_units = count(select(db.Unit.id).where(db.Unit.status == "NOK"))
        return {
            "clock": clock.as_dict(),
            "kpis": {"open_units": open_units, "quarantine": len(quarantine),
                     "repaired_24h": repaired_24h, "scrapped_24h": scrapped_24h},
            "open": [{"serial": serial, "kind": kind, "location": loc, "code": d.code, "name": name,
                      "station": st, "found_at": clock.iso(d.found_at)}
                     for d, serial, kind, loc, name, st in open_rows],
            "quarantine": [{"serial": u.serial, "kind": u.kind, "material": mat, "reason": u.quarantine_reason,
                            "location": u.location, "since": clock.iso(u.updated_at)}
                           for u, mat in quarantine],
            "repairs": [{"serial": serial, "station": st, "at": clock.iso(r.ended_at), "result": r.result,
                         "defects": r.action} for r, serial, st in repairs],
            "pareto": [{"code": c, "name": n, "count": k} for c, n, k in pareto],
        }

    # ------------------------------------------------------------------ holds
    def holds(self, session: Session) -> dict:
        clock = Clock(session, self.model)
        holds = session.scalars(select(db.Hold).order_by((db.Hold.status == "ACTIVE").desc(),
                                                         db.Hold.id.desc()).limit(100)).all()
        out = []
        for h in holds:
            held = select(db.Unit.id).join(db.HoldUnit, db.HoldUnit.unit_id == db.Unit.id).where(
                db.HoldUnit.hold_id == h.id, db.Unit.status == "QUARANTINE")
            if h.work_order_id:
                held = select(db.Unit.id).where(db.Unit.status == "QUARANTINE", or_(
                    db.Unit.id.in_(select(db.HoldUnit.unit_id).where(db.HoldUnit.hold_id == h.id)),
                    db.Unit.work_order_id == h.work_order_id))
            in_quarantine = session.scalar(select(func.count()).select_from(held.subquery()))
            out.append({
                "id": h.id, "kind": h.kind, "target": h.target, "reason": h.reason, "status": h.status,
                "affected": h.affected, "in_quarantine": in_quarantine, "disposition": h.disposition,
                "window_from": clock.iso(h.window_from), "window_to": clock.iso(h.window_to),
                "created_at": clock.iso(h.created_at), "created_by": h.created_by,
                "released_at": clock.iso(h.released_at), "released_by": h.released_by,
            })
        return {"clock": clock.as_dict(), "holds": out}
