"""Traceability in the MES.

The MES is the gatekeeper of the shop floor:

* check-in   - an interlocked station asks before it works on a unit. The MES
               refuses scrapped units, units on hold, units with open defects and
               units that skipped a step of their routing. It also issues serial
               numbers: panel and boards at the PCB laser, the product at the final
               assembly loader, KLT boxes and pallets at packing.
* records    - check-outs: every operation of every unit with its result,
               defects and measurements. They complete routing steps.
* repair     - the MES closes the defects and decides where the unit goes back.
* holds      - containment: a serial, a work order, or everything that passed a
               station in a time window, plus everything made from it. Units on
               hold are stopped at their next check-in and wait in quarantine
               until somebody releases or scraps them.

Genealogy is stored as links from input to output:
panel -> board (SPLIT), board -> unit (ASSEMBLY), unit -> KLT (PACK),
KLT -> pallet (PALLET).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session

from mes_lab.model.schema import PlantModel

from . import models as db
from .schemas import CheckInRequest, HoldCreate, RecordRow, RepairRequest

log = logging.getLogger("mes_lab.trace")

GS1_COMPANY_PREFIX = "8581234"  # fictional
OPEN_STATES = ("IN_PROCESS", "OK", "NOK", "QUARANTINE", "OPEN")


class Refusal(Exception):
    def __init__(self, reason: str, message: str, unit: db.Unit | None = None, data: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.unit = unit
        self.data = data or {}


def gs1_check_digit(digits: str) -> int:
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10


class Trace:
    def __init__(self, model: PlantModel):
        self.model = model
        self.tz = ZoneInfo(model.plant.timezone)
        self.station_ids: dict[str, int] = {}
        self.station_info: dict[int, tuple[str, str]] = {}  # id -> (code, type)
        self.material_ids: dict[str, int] = {}
        self.material_codes: dict[int, str] = {}
        self.operation_codes: dict[int, str] = {}
        self.jobs: dict[int, tuple[str, str, int]] = {}  # job -> (material, operation, work order)
        # "is any hold active?" is asked at every check-in; cached per version, the
        # version moves on after every committed change of holds (single process)
        self._holds_version = 0
        self._holds_cache: tuple[int, bool] | None = None
        self.orphan_records = 0

    # ------------------------------------------------------------------ lookups
    def _load(self, session: Session) -> None:
        rows = session.execute(select(db.Station.id, db.Station.code, db.StationType.code)
                               .join(db.StationType, db.Station.station_type_id == db.StationType.id))
        for sid, code, type_code in rows:
            self.station_ids[code] = sid
            self.station_info[sid] = (code, type_code)
        for mid, code in session.execute(select(db.Material.id, db.Material.code)):
            self.material_ids[code] = mid
            self.material_codes[mid] = code
        self.operation_codes = dict(session.execute(select(db.Operation.id, db.Operation.code)).all())

    def station(self, session: Session, code: str) -> tuple[int, str]:
        if code not in self.station_ids:
            self._load(session)
        if code not in self.station_ids:
            raise HTTPException(422, f"unknown station {code}")
        sid = self.station_ids[code]
        return sid, self.station_info[sid][1]

    def unit(self, session: Session, serial: str | None) -> db.Unit | None:
        if not serial:
            return None
        return session.scalar(select(db.Unit).where(db.Unit.serial == serial))

    def units(self, session: Session, serials: set[str]) -> dict[str, db.Unit]:
        if not serials:
            return {}
        with session.no_autoflush:
            rows = session.scalars(select(db.Unit).where(db.Unit.serial.in_(serials)))
            return {u.serial: u for u in rows}

    def job_info(self, session: Session, job_id: int) -> tuple[str, str, int]:
        """(material, operation, work order id) of a job; jobs never change these."""
        if job_id not in self.jobs:
            row = session.execute(
                select(db.Material.code, db.Operation.code, db.WorkOrder.id)
                .join(db.WorkOrder, db.WorkOrder.material_id == db.Material.id)
                .join(db.Job, db.Job.work_order_id == db.WorkOrder.id)
                .join(db.Operation, db.Operation.id == db.Job.operation_id)
                .where(db.Job.id == job_id)).one_or_none()
            if row is None:
                raise HTTPException(422, f"unknown job {job_id}")
            self.jobs[job_id] = tuple(row)
        return self.jobs[job_id]

    def holds_active(self, session: Session) -> bool:
        version = self._holds_version
        if self._holds_cache is not None and self._holds_cache[0] == version:
            return self._holds_cache[1]
        value = bool(session.scalar(
            select(func.count()).select_from(db.Hold).where(db.Hold.status == "ACTIVE")))
        self._holds_cache = (version, value)  # stale if holds changed meanwhile: version differs
        return value

    def holds_changed(self) -> None:
        """Call after a committed change of holds."""
        self._holds_version += 1

    def material_of(self, unit: db.Unit) -> str:
        return self.material_codes[unit.material_id]

    def steps(self, material: str, op: str | None) -> list[str]:
        try:
            return list(self.model.materials[material].operation(op).steps)
        except KeyError:
            return []

    def _operation_complete(self, material: str, op: str | None, completed: list) -> bool:
        steps = self.steps(material, op)
        return bool(steps) and all(s in completed for s in steps)

    # ------------------------------------------------------------------ serial numbers
    def next_serial(self, session: Session, kind: str, material: str | None = None,
                    when: datetime | None = None) -> str:
        n = session.scalar(select(db.SERIAL_SEQUENCES[kind].next_value()))
        if kind == "PANEL":
            return f"P{n:09d}"
        if kind == "UNIT":
            local = when.astimezone(self.tz)
            short = material.split("-")[0][:3].upper()
            return f"{short}{local:%y}{local.timetuple().tm_yday:03d}{n % 10_000_000:07d}"
        if kind == "KLT":
            return f"K{n:09d}"
        body = f"3{GS1_COMPANY_PREFIX}{n % 1_000_000_000:09d}"  # SSCC: 18 digits
        return body + str(gs1_check_digit(body))

    # ------------------------------------------------------------------ check-out records
    def apply_records(self, session: Session, run_id: str, records: list[RecordRow],
                      also: str | None = None) -> dict[str, db.Unit]:
        """Store check-out records; returns the units it loaded (plus `also`)."""
        serials = {r.serial for r in records} | {d.serial for r in records for d in r.defects}
        if also:
            serials.add(also)
        units = self.units(session, serials)
        if not records:
            return units
        record_rows: list[dict] = []
        defect_rows: list[dict] = []
        for r in records:
            unit = units.get(r.serial)
            if unit is None:
                self.orphan_records += 1
                continue
            station_id, _ = self.station(session, r.station)
            record_rows.append(dict(
                unit_id=unit.id, station_id=station_id, job_id=r.job_id, run_id=run_id,
                operation=unit.operation, step=r.step, started_at=r.start, ended_at=r.end,
                result=r.result, action=r.action[:200] if r.action else None,
                measurements=r.measurements))
            reworked = r.action == "WASH"
            for ref in r.defects:
                piece = units.get(ref.serial)
                if piece is None:
                    continue
                defect_rows.append(dict(
                    unit_id=piece.id, code=ref.code, station_id=station_id, found_at=r.end,
                    status="REWORKED" if reworked else "OPEN",
                    closed_at=r.end if reworked else None,
                    closed_station_id=station_id if reworked else None))
                if not reworked:
                    piece.status = "NOK"
                    piece.updated_at = r.end
            material = self.material_of(unit)
            completed = list(unit.completed or [])
            if r.result == "PASS" and r.step not in completed:
                completed.append(r.step)
            if reworked:  # washed panel: print and inspect again
                order = self.steps(material, unit.operation)
                cut = order.index("PASTE_PRINTER") if "PASTE_PRINTER" in order else 0
                completed = [s for s in completed if s in order and order.index(s) < cut]
            unit.completed = completed
            unit.station_id = station_id
            unit.updated_at = r.end
            unit.location = "Repair" if r.action == "REPAIR" else r.station
            kind = self.model.station_types[r.step].kind if r.step in self.model.station_types else None
            if kind == "depanel" and r.result == "PASS":
                self._depanel(session, unit, r.end)
            elif (unit.kind == "UNIT" and unit.status == "IN_PROCESS" and r.result == "PASS"
                  and unit.operation == self.model.materials[material].routing[-1].op
                  and self._operation_complete(material, unit.operation, completed)):
                unit.status = "OK"
                unit.location = "Waiting for packing"
        if record_rows:
            session.execute(insert(db.OperationRecord), record_rows)
        if defect_rows:
            session.execute(insert(db.Defect), defect_rows)
        return units

    def _depanel(self, session: Session, panel: db.Unit, when: datetime) -> None:
        panel.status = "DONE"
        panel.location = "Depaneled"
        boards = session.scalars(select(db.Unit).join(db.UnitLink, db.UnitLink.target_id == db.Unit.id)
                                 .where(db.UnitLink.source_id == panel.id,
                                        db.UnitLink.kind == "SPLIT")).all()
        for board in boards:
            board.operation = panel.operation
            board.completed = list(panel.completed)
            board.updated_at = when
            if board.status == "IN_PROCESS":
                board.status = "OK"
                board.location = "Supermarket"
            elif board.status == "NOK":
                board.location = "Repair"

    # ------------------------------------------------------------------ check-in
    def check_in(self, session: Session, req: CheckInRequest) -> dict:
        units = self.apply_records(session, req.run_id, req.records, also=req.serial)
        station_id, step = self.station(session, req.station)
        stype = self.model.station_types[step]
        job = self.job_info(session, req.job_id) if req.job_id else None
        if not self.material_codes:
            self._load(session)
        try:
            if req.consume:
                result = self._start_unit(session, req, job, station_id)
            elif req.serial is None:
                result = self._birth_panel(session, req, job, station_id, step)
            else:
                unit = units.get(req.serial)
                if unit is None:
                    raise Refusal("UNKNOWN", f"{req.serial} is not known to the MES")
                self._gate(session, unit, step, stype, job)
                unit.station_id = station_id
                unit.updated_at = req.plant_time
                if stype.kind == "pack":
                    result = self._pack(session, unit, station_id, stype, req.plant_time)
                else:
                    unit.location = req.station
                    result = {"ok": True}
        except Refusal as refusal:
            if refusal.unit is not None:
                self._to_quarantine(refusal.unit, station_id, req.station,
                                    f"{refusal.reason}: {refusal.message}", req.plant_time)
            result = {"ok": False, "reason": refusal.reason, "message": refusal.message,
                      **refusal.data}
        session.commit()
        return result

    def active_hold(self, session: Session, unit: db.Unit) -> db.Hold | None:
        if not self.holds_active(session):
            return None
        hold = session.scalar(select(db.Hold).join(db.HoldUnit, db.HoldUnit.hold_id == db.Hold.id)
                              .where(db.HoldUnit.unit_id == unit.id, db.Hold.status == "ACTIVE")
                              .limit(1))
        if hold is None and unit.work_order_id is not None:
            hold = session.scalar(select(db.Hold).where(
                db.Hold.status == "ACTIVE", db.Hold.kind == "WORK_ORDER",
                db.Hold.work_order_id == unit.work_order_id).limit(1))
        return hold

    def _gate(self, session: Session, unit: db.Unit, step: str, stype,
              job: tuple[str, str, int] | None) -> None:
        """Raise Refusal if the unit may not be processed at this station."""
        if unit.status in ("SCRAPPED", "LOST", "CONSUMED"):
            raise Refusal(unit.status, f"{unit.serial} is {unit.status.lower()}", None)
        hold = self.active_hold(session, unit)
        if hold is not None:
            raise Refusal("HOLD", f"hold #{hold.id}: {hold.reason}", unit)
        if unit.status == "NOK":
            raise Refusal("NOT_REPAIRED", f"{unit.serial} has open defects", unit)
        if stype.kind == "pack":
            if stype.container == "klt" and not (unit.kind == "UNIT" and unit.status == "OK"):
                raise Refusal("ROUTE", "final assembly is not finished", unit)
            if stype.container == "pallet" and not (unit.kind == "KLT" and unit.status == "CLOSED"):
                raise Refusal("ROUTE", "only closed KLT boxes go on a pallet", unit)
            return
        if job is None:
            return
        material = self.material_of(unit)
        job_material, op, _ = job
        if material != job_material:
            raise Refusal("ROUTE", f"{unit.serial} is {material}, the job builds {job_material}", unit)
        if unit.operation != op:
            routing = [o.op for o in self.model.materials[material].routing]
            moves_on = (unit.operation in routing and op in routing
                        and routing.index(op) == routing.index(unit.operation) + 1
                        and self._operation_complete(material, unit.operation, unit.completed))
            if not moves_on:
                raise Refusal("ROUTE", f"{unit.serial} is in operation {unit.operation}, not {op}", unit)
            unit.operation = op
            unit.completed = []
        order = self.steps(material, op)
        if step in order:
            missing = [s for s in order[:order.index(step)] if s not in (unit.completed or [])]
            if missing:
                raise Refusal("ROUTE", f"{unit.serial} skipped {', '.join(missing)}", unit)

    def _to_quarantine(self, unit: db.Unit, station_id: int, station_code: str, reason: str,
                       when: datetime) -> None:
        if unit.status != "QUARANTINE":
            unit.prev_status = unit.status
        unit.status = "QUARANTINE"
        unit.quarantine_reason = reason[:200]
        unit.location = f"Quarantine ({station_code})"
        unit.station_id = station_id
        unit.updated_at = when

    def _birth_panel(self, session: Session, req: CheckInRequest, job: tuple[str, str, int] | None,
                     station_id: int, step: str) -> dict:
        if job is None:
            raise Refusal("NO_JOB", "a new panel needs an active job")
        material, op, order_id = job
        steps = self.steps(material, op)
        completed = steps[:steps.index(step)] if step in steps else []
        serial = self.next_serial(session, "PANEL")
        common = dict(material_id=self.material_ids[material], work_order_id=order_id, run_id=req.run_id,
                      status="IN_PROCESS", operation=op, station_id=station_id,
                      created_at=req.plant_time, updated_at=req.plant_time)
        panel = db.Unit(serial=serial, kind="PANEL", completed=list(completed),
                        location=req.station, **common)
        pieces = req.pieces or self.model.materials[material].boards_per_panel
        boards = [db.Unit(serial=f"{serial}-{i + 1:02d}", kind="BOARD", completed=list(completed),
                          location=f"In panel {serial}", **common) for i in range(pieces)]
        session.add_all([panel, *boards])
        session.flush()
        session.add_all([db.UnitLink(source_id=panel.id, target_id=b.id, kind="SPLIT",
                                     created_at=req.plant_time) for b in boards])
        data = {"serial": serial, "boards": [b.serial for b in boards]}
        hold = self.active_hold(session, panel)
        if hold is not None:  # its work order is on hold: born straight into quarantine
            raise Refusal("HOLD", f"hold #{hold.id}: {hold.reason}", panel, data)
        return {"ok": True, **data}

    def _start_unit(self, session: Session, req: CheckInRequest, job: tuple[str, str, int] | None,
                    station_id: int) -> dict:
        board = self.unit(session, req.consume)
        if board is None:
            raise Refusal("UNKNOWN", f"board {req.consume} is not known to the MES")
        if job is None:
            raise Refusal("NO_JOB", "a new unit needs an active job")
        product, _, order_id = job
        pcba = self.model.materials[product].pcba
        if board.kind != "BOARD":
            raise Refusal("ROUTE", f"{board.serial} is not a board", board)
        if board.status in ("CONSUMED", "SCRAPPED", "LOST"):
            raise Refusal(board.status, f"{board.serial} is {board.status.lower()}")
        hold = self.active_hold(session, board)
        if hold is not None:
            raise Refusal("HOLD", f"hold #{hold.id}: {hold.reason}", board)
        if board.status == "NOK":
            raise Refusal("NOT_REPAIRED", f"{board.serial} has open defects", board)
        if self.material_of(board) != pcba:
            raise Refusal("ROUTE", f"{board.serial} is {self.material_of(board)}, {product} needs {pcba}",
                          board)
        last_op = self.model.materials[pcba].routing[-1].op
        if not (board.operation == last_op and self._operation_complete(pcba, last_op, board.completed)):
            raise Refusal("ROUTE", f"{board.serial} did not finish SMT", board)
        serial = self.next_serial(session, "UNIT", product, req.plant_time)
        unit = db.Unit(serial=serial, kind="UNIT", material_id=self.material_ids[product],
                       work_order_id=order_id, run_id=req.run_id, status="IN_PROCESS",
                       operation=self.model.materials[product].routing[0].op, completed=[],
                       station_id=station_id, location=req.station,
                       created_at=req.plant_time, updated_at=req.plant_time)
        session.add(unit)
        session.flush()
        session.add(db.UnitLink(source_id=board.id, target_id=unit.id, kind="ASSEMBLY",
                                created_at=req.plant_time))
        board.status = "CONSUMED"
        board.location = f"In unit {serial}"
        board.updated_at = req.plant_time
        return {"ok": True, "serial": serial}

    def _pack(self, session: Session, unit: db.Unit, station_id: int, stype, when: datetime) -> dict:
        klt = stype.container == "klt"
        kind = "KLT" if klt else "PALLET"
        packing = self.model.materials[self.material_of(unit)].packing
        size = packing.klt_qty if klt else packing.klt_per_pallet
        box = session.scalar(select(db.Unit).where(
            db.Unit.kind == kind, db.Unit.status == "OPEN", db.Unit.container_station_id == station_id,
            db.Unit.material_id == unit.material_id).order_by(db.Unit.id.desc()).limit(1))
        if box is None:
            box = db.Unit(serial=self.next_serial(session, kind), kind=kind, material_id=unit.material_id,
                          run_id=unit.run_id, status="OPEN", container_station_id=station_id, qty=0,
                          station_id=station_id, location=self.station_info[station_id][0],
                          completed=[], created_at=when, updated_at=when)
            session.add(box)
            session.flush()
        session.add(db.UnitLink(source_id=unit.id, target_id=box.id, kind="PACK" if klt else "PALLET",
                                created_at=when))
        box.qty += 1
        box.updated_at = when
        unit.status = "PACKED" if klt else "PALLETIZED"
        unit.location = f"In {box.serial}"
        full = box.qty >= size
        if full:
            box.status = "CLOSED"
            box.location = "Waiting for palletizing" if klt else "Warehouse"
        return {"ok": True, "container": box.serial, "full": full}

    # ------------------------------------------------------------------ repair
    def repair(self, session: Session, req: RepairRequest) -> dict:
        self.apply_records(session, req.run_id, req.records)
        station_id, step = self.station(session, req.station)
        unit = self.unit(session, req.serial)
        if unit is None:
            session.commit()
            return {"ok": True, "disposition": "RETURN" if req.success else "SCRAP", "return_to": None}
        defects = session.scalars(select(db.Defect).where(db.Defect.unit_id == unit.id,
                                                         db.Defect.status == "OPEN")).all()
        return_to = None
        if req.success:
            for d in defects:
                d.status, d.closed_at, d.closed_station_id = "REPAIRED", req.plant_time, station_id
            if unit.kind == "BOARD":
                unit.status, unit.location, return_to = "OK", "Supermarket", "SUPERMARKET"
            else:
                order = self.steps(self.material_of(unit), unit.operation)
                failed = [self.station_info[d.station_id][1] for d in defects]
                failed = [s for s in failed if s in order]
                if failed:
                    return_to = min(failed, key=order.index)
                    cut = order.index(return_to)
                    unit.completed = [s for s in (unit.completed or []) if s in order and order.index(s) < cut]
                unit.status = "IN_PROCESS"
                unit.location = f"Back to {return_to or 'line'}"
            result, disposition = "REPAIRED", "RETURN"
        else:
            for d in defects:
                d.status, d.closed_at, d.closed_station_id = "SCRAPPED", req.plant_time, station_id
            unit.status, unit.location = "SCRAPPED", "Scrap"
            result, disposition = "SCRAPPED", "SCRAP"
        session.add(db.OperationRecord(
            unit_id=unit.id, station_id=station_id, run_id=req.run_id, operation=unit.operation,
            step=step, started_at=req.plant_time, ended_at=req.plant_time, result=result,
            action=", ".join(sorted({d.code for d in defects}))[:200] or None, measurements={}))
        unit.station_id = station_id
        unit.updated_at = req.plant_time
        session.commit()
        return {"ok": True, "disposition": disposition, "return_to": return_to}

    # ------------------------------------------------------------------ runs
    def start_run(self, session: Session, run_id: str, initial_stock: dict[str, list[str]],
                  when: datetime) -> None:
        """A new run starts with an empty shop floor: what was in process is lost,
        pending commands are void, and the opening stock of boards is registered."""
        session.execute(update(db.Unit).where(
            (db.Unit.status.in_(OPEN_STATES)) | ((db.Unit.kind == "KLT") & (db.Unit.status == "CLOSED"))
        ).values(status="LOST", location="Lost at simulation restart"))
        session.execute(update(db.SimCommand).where(db.SimCommand.acked_at.is_(None))
                        .values(acked_at=when))
        if not self.material_ids:
            self._load(session)
        rows = []
        for material, serials in initial_stock.items():
            mid = self.material_ids.get(material)
            if mid is None:
                continue
            last = self.model.materials[material].routing[-1]
            for serial in serials:
                rows.append({"serial": serial, "kind": "BOARD", "material_id": mid, "run_id": run_id,
                             "status": "OK", "operation": last.op, "completed": list(last.steps),
                             "location": "Supermarket", "qty": 0, "created_at": when,
                             "updated_at": when})
        if rows:
            session.execute(insert(db.Unit), rows)

    # ------------------------------------------------------------------ shipping
    def apply_shipments(self, session: Session, run_id: str, rows) -> None:
        """A truck left: the pallets on it, the boxes in them and the units in the boxes are shipped.
        Idempotent: a delivery note that is already known is skipped."""
        for row in rows:
            if session.scalar(select(db.Shipment.id).where(db.Shipment.asn_no == row.asn)) is not None:
                continue
            shipment = db.Shipment(asn_no=row.asn, customer=row.customer, plate=row.plate, run_id=run_id,
                                   departed_at=row.departed, pallets=len(row.pallets), klt=row.klt, units=row.units)
            session.add(shipment)
            session.flush()
            seeds = list(session.scalars(select(db.Unit.id).where(db.Unit.serial.in_(row.pallets))))
            if seeds:
                session.execute(text("""
                    WITH RECURSIVE contents(id) AS (
                        SELECT unnest(CAST(:seeds AS bigint[]))
                        UNION
                        SELECT l.source_id FROM unit_link l JOIN contents c ON l.target_id = c.id
                        WHERE l.kind IN ('PACK', 'PALLET')
                    )
                    UPDATE unit SET status = 'SHIPPED', location = :loc, shipment_id = :sid, updated_at = :when
                    WHERE id IN (SELECT id FROM contents)
                      AND status IN ('PACKED', 'PALLETIZED', 'CLOSED', 'OPEN')"""),
                    {"seeds": seeds, "loc": f"Shipped on {row.asn}", "sid": shipment.id, "when": row.departed})

    # ------------------------------------------------------------------ holds and quarantine
    def _affected_ids(self, session: Session, seeds: list[int]) -> list[int]:
        """Seeds, everything packed inside them, and everything made from all of that."""
        if not seeds:
            return []
        sql = text("""
            WITH RECURSIVE contents(id) AS (
                SELECT unnest(CAST(:seeds AS bigint[]))
                UNION
                SELECT l.source_id FROM unit_link l JOIN contents c ON l.target_id = c.id
                WHERE l.kind IN ('PACK', 'PALLET')
            ), downstream(id) AS (
                SELECT id FROM contents
                UNION
                SELECT l.target_id FROM unit_link l JOIN downstream d ON l.source_id = d.id
            )
            SELECT id FROM downstream""")
        return [r[0] for r in session.execute(sql, {"seeds": seeds})]

    def _hold_seeds(self, session: Session, body: HoldCreate, now: datetime) -> tuple[list[int], dict]:
        extra: dict = {}
        if body.kind == "SERIAL":
            unit = self.unit(session, body.target.strip())
            if unit is None:
                raise HTTPException(404, f"serial {body.target} not found")
            return [unit.id], extra
        if body.kind == "WORK_ORDER":
            order = session.scalar(select(db.WorkOrder).where(db.WorkOrder.order_no == body.target.strip()))
            if order is None:
                raise HTTPException(404, f"work order {body.target} not found")
            extra["work_order_id"] = order.id
            ids = list(session.scalars(select(db.Unit.id).where(db.Unit.work_order_id == order.id)))
            return ids, extra
        station_id, _ = self.station(session, body.target.strip())
        start = body.window_from or now - timedelta(hours=1)
        end = body.window_to or now
        if end <= start:
            raise HTTPException(422, "the time window ends before it starts")
        extra.update(station_id=station_id, window_from=start, window_to=end)
        ids = list(session.scalars(select(db.OperationRecord.unit_id).distinct().where(
            db.OperationRecord.station_id == station_id,
            db.OperationRecord.ended_at >= start, db.OperationRecord.ended_at <= end)))
        return ids, extra

    def preview_hold(self, session: Session, body: HoldCreate, now: datetime) -> dict:
        seeds, _ = self._hold_seeds(session, body, now)
        ids = self._affected_ids(session, seeds)
        rows = session.execute(select(db.Unit.kind, db.Unit.status, func.count())
                               .where(db.Unit.id.in_(ids)).group_by(db.Unit.kind, db.Unit.status)).all() \
            if ids else []
        return {"affected": len(ids), "seeds": len(seeds),
                "by_kind": [{"kind": k, "status": s, "count": c} for k, s, c in rows]}

    def create_hold(self, session: Session, body: HoldCreate, user: str, now: datetime) -> db.Hold:
        seeds, extra = self._hold_seeds(session, body, now)
        ids = self._affected_ids(session, seeds)
        hold = db.Hold(kind=body.kind, target=body.target.strip(), reason=body.reason.strip(),
                       status="ACTIVE", affected=len(ids), created_at=now, created_by=user, **extra)
        session.add(hold)
        session.flush()
        if ids:
            session.execute(insert(db.HoldUnit), [{"hold_id": hold.id, "unit_id": i} for i in ids])
        session.commit()
        self.holds_changed()
        log.info("hold #%d on %s %s by %s: %d units", hold.id, body.kind, body.target, user, len(ids))
        return hold

    def release_hold(self, session: Session, hold_id: int, disposition: str, user: str,
                     now: datetime) -> dict:
        hold = session.get(db.Hold, hold_id)
        if hold is None:
            raise HTTPException(404, "hold not found")
        if hold.status != "ACTIVE":
            raise HTTPException(409, "hold is already released")
        hold.status, hold.disposition = "RELEASED", disposition
        hold.released_at, hold.released_by = now, user
        session.flush()
        candidates = select(db.Unit).where(db.Unit.status == "QUARANTINE",
                                           db.Unit.quarantine_reason.like("HOLD%"))
        in_hold = candidates.join(db.HoldUnit, db.HoldUnit.unit_id == db.Unit.id) \
            .where(db.HoldUnit.hold_id == hold.id)
        units = list(session.scalars(in_hold))
        if hold.work_order_id is not None:
            units += list(session.scalars(candidates.where(db.Unit.work_order_id == hold.work_order_id)))
        done = 0
        for unit in {u.id: u for u in units}.values():
            if self.active_hold(session, unit) is None:
                self.dispose(session, unit, disposition, now)
                done += 1
        session.commit()
        self.holds_changed()
        return {"hold": hold.id, "disposed": done}

    def dispose(self, session: Session, unit: db.Unit, action: str, now: datetime) -> None:
        """Decision about a unit in quarantine: back to production, or scrap."""
        if action == "RETURN":
            unit.status = unit.prev_status if unit.prev_status not in (None, "QUARANTINE") else "IN_PROCESS"
            unit.location = "Released from quarantine"
        else:
            unit.status = "SCRAPPED"
            unit.location = "Scrap"
            session.execute(update(db.Defect).where(db.Defect.unit_id == unit.id, db.Defect.status == "OPEN")
                            .values(status="SCRAPPED", closed_at=now))
        unit.quarantine_reason = None
        unit.updated_at = now
        session.add(db.SimCommand(serial=unit.serial, action=action, created_at=now))

    def pending_commands(self, session: Session, acks: list[int], now: datetime) -> list[dict]:
        if acks:
            session.execute(update(db.SimCommand).where(db.SimCommand.id.in_(acks),
                                                        db.SimCommand.acked_at.is_(None))
                            .values(acked_at=now))
        rows = session.scalars(select(db.SimCommand).where(db.SimCommand.acked_at.is_(None))
                               .order_by(db.SimCommand.id).limit(200))
        return [{"id": c.id, "serial": c.serial, "action": c.action} for c in rows]
