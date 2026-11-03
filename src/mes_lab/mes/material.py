"""SMT material in the MES (the plant side is sim/material.py).

* scans    - before a reel goes on a feeder (at a changeover, or spliced to a running reel) the operator
             scans the feeder and the reel: the MES checks that it is the part the placement program of the
             job needs there (WRONG_PART, NOT_IN_PROGRAM) and that a moisture-sensitive part still has floor
             life (MSL_EXPIRED: off to the bake oven). A paste jar is scanned at the printer: it must have
             thawed `thaw_h` hours (PASTE_NOT_THAWED). Every scan is kept with its verdict.
* reports  - with the edge flush the plant says when a reel starts and stops feeding a placer (the time
             windows of the genealogy), when a jar went on and off a printer, how much is left.
* paste    - at every print the check-in of the printer is refused while its jar is open longer than
             `open_h` (PASTE_EXPIRED): the operator changes the jar.
* msl      - a reel on a machine whose floor life ran out gets a command for the plant: take it off.
* goods-in - a supplier truck brings its delivery note (ARRIVAL); every reel received is checked against
             it (NOT_ORDERED) and against holds on its lot (LOT_ON_HOLD) and goes into the store; a reel
             taken out for a line must be the oldest of its part in the store (FIFO).
* genealogy- which reels (part, supplier lot) and which paste jar the boards of a unit got: the placer
             records of their panel overlapping the time a reel fed that placer. And the other way round:
             everything made with a supplier lot (a hold on the lot, see trace.py).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from mes_lab.model.schema import PlantModel

from . import models as db
from .trace import Refusal

PLACERS = ("PNP_CHIP", "PNP_IC")


class Material:
    def __init__(self, model: PlantModel, trace):
        self.model = model
        self.cfg = model.smt_material
        self.trace = trace

    # ------------------------------------------------------------------ scans
    def scan(self, session: Session, req) -> dict:
        """A scan at a machine: may this reel go on this feeder, this jar on this printer?"""
        self.trace.apply_records(session, req.run_id, req.records)
        station_id, step = self.trace.station(session, req.station) if req.station else (None, None)
        now = req.plant_time
        try:
            if req.kind == "PASTE":
                self._paste(session, req, station_id, now)
            elif req.kind in ("ARRIVAL", "RECEIPT", "ISSUE"):
                self._goods(session, req, now)
            else:
                self._reel(session, req, station_id, step, now)
            result = {"ok": True}
        except Refusal as refusal:
            result = {"ok": False, "reason": refusal.reason, "message": refusal.message}
        session.add(db.MaterialScan(at=now, station_id=station_id, place=req.place, kind=req.kind[:8], item=req.item,
                                    part=req.part, slot=req.slot, result=result.get("reason") or "OK",
                                    message=result.get("message"), operator=req.operator))
        session.commit()
        return result

    def _reel(self, session: Session, req, station_id: int, step: str, now: datetime) -> None:
        reel = session.scalar(select(db.Reel).where(db.Reel.serial == req.item))
        if reel is None:
            reel = db.Reel(serial=req.item, part=req.part or "?", lot=req.lot or "?", qty=req.qty or 0,
                           left=req.left or 0, msl=req.msl, status="LINE", updated_at=now)
            session.add(reel)
        reel.left = req.left if req.left is not None else reel.left
        reel.floor_used_s = req.floor_used_s or 0.0
        reel.out_since = req.out_since
        reel.updated_at = now
        if reel.part != req.slot:
            raise Refusal("WRONG_PART", f"{reel.serial} is {reel.part}, the feeder needs {req.slot}")
        hold = session.scalar(select(db.Hold).where(db.Hold.kind == "MATERIAL_LOT", db.Hold.target == reel.lot,
                                                    db.Hold.status == "ACTIVE").limit(1))
        if hold is not None:                           # a held lot never goes on a machine
            reel.status, reel.station_id, reel.slot = "HOLD", None, None
            raise Refusal("LOT_ON_HOLD", f"lot {reel.lot} is on hold #{hold.id}: {hold.reason}")
        program = self.program(session, req.job_id, step)
        if program is not None and req.slot not in program:
            raise Refusal("NOT_IN_PROGRAM", f"{req.slot} is not in the placement program of the job")
        left_h = self.floor_left_h(reel, now)
        if left_h is not None and left_h <= 0:
            reel.status, reel.station_id, reel.slot = "BAKE", None, None
            raise Refusal("MSL_EXPIRED", f"the floor life of {reel.serial} (MSL {reel.msl}) ran out: bake it")
        if req.kind == "SPLICE":                       # spliced to the running reel: it feeds once that one ends
            reel.status, reel.station_id, reel.slot = "NEXT", station_id, req.slot
        else:
            reel.status, reel.station_id, reel.slot = "FEEDER", station_id, req.slot

    def _goods(self, session: Session, req, now: datetime) -> None:
        """Goods-in: the delivery note of a truck, a reel received against it, a reel out of the store."""
        if req.kind == "ARRIVAL":
            if session.scalar(select(db.Delivery).where(db.Delivery.note == req.note)) is None:
                session.add(db.Delivery(note=req.note, supplier=req.supplier or "?", arrived_at=now, received={},
                                        expected={line["part"]: line["reels"] for line in req.lines}, refused=0))
            return
        if req.kind == "RECEIPT":
            note = session.scalar(select(db.Delivery).where(db.Delivery.note == req.note))
            if note is None or note.expected.get(req.part, 0) - note.received.get(req.part, 0) <= 0:
                if note is not None:
                    note.refused += 1
                raise Refusal("NOT_ORDERED", f"{req.part} is not on the delivery note {req.note}: back to the supplier")
            hold = session.scalar(select(db.Hold).where(db.Hold.kind == "MATERIAL_LOT", db.Hold.target == req.lot,
                                                        db.Hold.status == "ACTIVE").limit(1))
            if hold is not None:
                note.refused += 1
                raise Refusal("LOT_ON_HOLD", f"lot {req.lot} is on hold #{hold.id}: {hold.reason}")
            note.received = {**note.received, req.part: note.received.get(req.part, 0) + 1}
            reel = session.scalar(select(db.Reel).where(db.Reel.serial == req.item))
            if reel is None:
                reel = db.Reel(serial=req.item, part=req.part or "?", lot=req.lot or "?", qty=req.qty or 0,
                               left=req.left if req.left is not None else (req.qty or 0), msl=req.msl, updated_at=now)
                session.add(reel)
            reel.status, reel.supplier, reel.note, reel.received_at, reel.updated_at = "STORE", req.supplier, req.note, now, now
            return
        reel = session.scalar(select(db.Reel).where(db.Reel.serial == req.item))      # ISSUE
        if reel is None:
            return                                     # the plant did not report it: nothing to check against
        if reel.status == "STORE" and reel.received_at is not None:
            older = session.scalar(select(db.Reel).where(db.Reel.part == reel.part, db.Reel.status == "STORE",
                                                         db.Reel.received_at < reel.received_at)
                                   .order_by(db.Reel.received_at).limit(1))
            if older is not None:
                raise Refusal("FIFO", f"{older.serial} of {reel.part} came in earlier "
                                      f"({older.received_at:%d.%m. %H:%M}): take it first")
        reel.status, reel.line, reel.updated_at = "TRANSIT", req.line, now

    def stock(self, session: Session, req) -> dict:
        """The reels in the store of goods-in at the start of a run."""
        known = set(session.scalars(select(db.Reel.serial).where(db.Reel.serial.in_([r["serial"] for r in req.reels]))))
        rows = [db.Reel(serial=r["serial"], part=r["part"], lot=r["lot"], qty=r["qty"], left=r["qty"], msl=r.get("msl"),
                        status="STORE", supplier=r.get("supplier"), received_at=r["received"], updated_at=r["received"])
                for r in req.reels if r["serial"] not in known]
        session.add_all(rows)
        session.commit()
        return {"ok": True, "reels": len(rows)}

    def _paste(self, session: Session, req, station_id: int, now: datetime) -> None:
        jar = self._jar(session, req.item, req.lot, req.out_at, req.prints, now)
        thawed_h = (now - jar.out_at).total_seconds() / 3600 if jar.out_at else 0.0
        if thawed_h < self.cfg.paste.thaw_h:
            raise Refusal("PASTE_NOT_THAWED", f"{jar.serial} is out of the fridge {thawed_h:.1f} h, "
                                              f"it needs {self.cfg.paste.thaw_h:g} h")
        for old in session.scalars(select(db.PasteJar).where(db.PasteJar.station_id == station_id,
                                                              db.PasteJar.status == "OPEN",
                                                              db.PasteJar.id != jar.id)):
            old.status, old.ended_at = "EMPTY", old.ended_at or now     # the plant says why (END)
        jar.station_id, jar.opened_at, jar.status = station_id, now, "OPEN"

    def _jar(self, session: Session, serial: str, lot: str | None, out_at: datetime | None,
             prints: int | None, now: datetime) -> db.PasteJar:
        jar = session.scalar(select(db.PasteJar).where(db.PasteJar.serial == serial))
        if jar is None:
            jar = db.PasteJar(serial=serial, lot=lot or "?", out_at=out_at or now, prints=prints or 0, status="THAW")
            session.add(jar)
            session.flush()
        if prints is not None:
            jar.prints = prints
        return jar

    def program(self, session: Session, job_id: int | None, step: str) -> set[str] | None:
        """The parts the placement program of a job places on this placer type."""
        if job_id is None or step not in PLACERS:
            return None
        material, op, _ = self.trace.job_info(session, job_id)
        mat = self.model.materials.get(material)
        if mat is None:
            return None
        parts = self.cfg.parts
        return {p for p in mat.operation(op).components if p in parts and parts[p].machine == step}

    def floor_left_h(self, reel: db.Reel, now: datetime) -> float | None:
        """Hours of floor life left (None: not moisture sensitive)."""
        if reel.msl is None or reel.msl not in self.cfg.msl.floor_h:
            return None
        used = reel.floor_used_s or 0.0
        if reel.out_since is not None:
            used += (now - reel.out_since).total_seconds()
        return self.cfg.msl.floor_h[reel.msl] - used / 3600

    # ------------------------------------------------------------------ the printer: paste open too long
    def paste_ok(self, session: Session, station_id: int, when: datetime) -> None:
        jar = session.scalar(select(db.PasteJar).where(db.PasteJar.station_id == station_id,
                                                        db.PasteJar.status == "OPEN")
                             .order_by(db.PasteJar.opened_at.desc()).limit(1))
        if jar is None or jar.opened_at is None:
            return                                     # the plant did not report its paste (yet)
        open_h = (when - jar.opened_at).total_seconds() / 3600
        if open_h > self.cfg.paste.open_h:
            raise Refusal("PASTE_EXPIRED", f"{jar.serial} is open {open_h:.1f} h on the stencil, "
                                           f"at most {self.cfg.paste.open_h:g} h", None)

    def new_run(self, session: Session, at: datetime) -> None:
        """A new run starts with a new shop floor: what fed the machines of the last one is gone."""
        session.execute(update(db.FeederLoad).where(db.FeederLoad.ended_at.is_(None)).values(ended_at=at, reason="RUN_END"))
        session.execute(update(db.Reel).where(db.Reel.status.in_(("STORE", "TRANSIT", "FEEDER", "NEXT", "LINE", "BAKE")))
                        .values(status="GONE", station_id=None, slot=None, eta_s=None))
        session.execute(update(db.PasteJar).where(db.PasteJar.status.in_(("OPEN", "THAW")))
                        .values(status="GONE", ended_at=at))

    # ------------------------------------------------------------------ what the plant reports
    def ingest(self, session: Session, report, now: datetime) -> None:
        if report is None:
            return
        stations: dict[str, int] = {}

        def sid(code: str | None) -> int | None:
            if code is None:
                return None
            if code not in stations:
                stations[code] = self.trace.station(session, code)[0]
            return stations[code]

        for ev in report.events:
            if ev.kind == "AT_LINE":                   # goods-in put it into the rack of the line
                session.execute(update(db.Reel).where(db.Reel.serial == ev.item)
                                .values(status="LINE", line=ev.line, updated_at=ev.at))
                continue
            if ev.slot == "PASTE":
                jar = self._jar(session, ev.item, ev.lot, ev.out_at, ev.left, ev.at)
                if ev.kind == "START":
                    jar.station_id, jar.status = sid(ev.station), "OPEN"
                    jar.opened_at = ev.opened_at or jar.opened_at or ev.at
                elif ev.kind == "END":
                    jar.status = "EXPIRED" if ev.reason == "EXPIRED" else "EMPTY"
                    jar.ended_at, jar.reason = ev.at, ev.reason
                continue
            reel = session.scalar(select(db.Reel).where(db.Reel.serial == ev.item))
            if reel is None:
                continue                               # never scanned (an older plant): nothing to keep
            if ev.left is not None:
                reel.left = ev.left
            reel.updated_at = ev.at
            if ev.kind == "START":
                session.add(db.FeederLoad(reel_id=reel.id, station_id=sid(ev.station), slot=ev.slot,
                                          started_at=ev.at))
                reel.status, reel.station_id, reel.slot = "FEEDER", sid(ev.station), ev.slot
            elif ev.kind == "END":
                session.execute(update(db.FeederLoad).where(db.FeederLoad.reel_id == reel.id,
                                                            db.FeederLoad.ended_at.is_(None))
                                .values(ended_at=ev.at, reason=ev.reason))
                reel.status = {"EMPTY": "EMPTY", "MSL": "BAKE"}.get(ev.reason or "", "LINE")
                if reel.status != "LINE":
                    reel.station_id, reel.slot = None, None
                reel.eta_s = None
            elif ev.kind == "BAKED":
                reel.floor_used_s, reel.out_since, reel.status = 0.0, None, "LINE"
        if report.levels:
            by_serial = {row.id: row for row in report.levels}
            for reel in session.scalars(select(db.Reel).where(db.Reel.serial.in_(by_serial))):
                row = by_serial[reel.serial]
                reel.left, reel.eta_s, reel.updated_at = row.left, row.eta_s, now
            for jar in session.scalars(select(db.PasteJar).where(db.PasteJar.serial.in_(by_serial))):
                jar.prints = by_serial[jar.serial].left

    def alarms(self, session: Session, now: datetime) -> int:
        """Reels on a machine whose floor life ran out: the plant gets a command to take them off."""
        rows = list(session.scalars(select(db.Reel).where(db.Reel.status.in_(("FEEDER", "NEXT")),
                                                          db.Reel.msl.is_not(None))))
        sent = 0
        for reel in rows:
            left = self.floor_left_h(reel, now)
            if left is None or left > 0:
                continue
            pending = session.scalar(select(func.count()).select_from(db.SimCommand).where(
                db.SimCommand.serial == reel.serial, db.SimCommand.action == "UNLOAD_REEL",
                db.SimCommand.acked_at.is_(None)))
            if not pending:
                session.add(db.SimCommand(serial=reel.serial, action="UNLOAD_REEL", created_at=now))
                sent += 1
        return sent

    # ------------------------------------------------------------------ genealogy
    def components(self, session: Session, unit_ids: list[int]) -> list[dict]:
        """The reels and the paste jar the panels among `unit_ids` got their parts and paste from."""
        if not unit_ids:
            return []
        rec = db.OperationRecord
        q = (select(rec.unit_id, rec.step, db.Station.code, db.Reel.serial, db.Reel.part, db.Reel.lot,
                    db.FeederLoad.started_at, db.FeederLoad.ended_at)
             .join(db.FeederLoad, and_(db.FeederLoad.station_id == rec.station_id,
                                       db.FeederLoad.started_at <= rec.ended_at,
                                       or_(db.FeederLoad.ended_at.is_(None), db.FeederLoad.ended_at >= rec.started_at)))
             .join(db.Reel, db.Reel.id == db.FeederLoad.reel_id)
             .join(db.Station, db.Station.id == rec.station_id)
             .where(rec.unit_id.in_(unit_ids), rec.step.in_(PLACERS))
             .order_by(rec.step, db.Reel.part, db.FeederLoad.started_at))
        names = {code: p.name for code, p in self.cfg.parts.items()}
        out = [{"kind": "REEL", "unit_id": u, "step": step, "station": code, "item": serial, "part": part,
                "name": names.get(part, part), "lot": lot}
               for u, step, code, serial, part, lot, _, _ in session.execute(q)]
        jar = (select(rec.unit_id, db.Station.code, db.PasteJar.serial, db.PasteJar.lot)
               .join(db.PasteJar, and_(db.PasteJar.station_id == rec.station_id,
                                       db.PasteJar.opened_at <= rec.ended_at,
                                       or_(db.PasteJar.ended_at.is_(None), db.PasteJar.ended_at >= rec.started_at)))
               .join(db.Station, db.Station.id == rec.station_id)
               .where(rec.unit_id.in_(unit_ids), rec.step == "PASTE_PRINTER"))
        out += [{"kind": "PASTE", "unit_id": u, "step": "PASTE_PRINTER", "station": code, "item": serial,
                 "part": "PASTE", "name": self.cfg.paste.name, "lot": lot}
                for u, code, serial, lot in session.execute(jar)]
        seen, unique = set(), []
        for row in out:
            key = (row["unit_id"], row["item"])
            if key not in seen:
                seen.add(key)
                unique.append(row)
        return unique

    def lot_units(self, session: Session, lot: str) -> list[int]:
        """The panels that got parts (or paste) of a supplier lot."""
        rec = db.OperationRecord
        reels = (select(rec.unit_id)
                 .join(db.FeederLoad, and_(db.FeederLoad.station_id == rec.station_id,
                                           db.FeederLoad.started_at <= rec.ended_at,
                                           or_(db.FeederLoad.ended_at.is_(None), db.FeederLoad.ended_at >= rec.started_at)))
                 .join(db.Reel, db.Reel.id == db.FeederLoad.reel_id)
                 .where(db.Reel.lot == lot, rec.step.in_(PLACERS)))
        jars = (select(rec.unit_id)
                .join(db.PasteJar, and_(db.PasteJar.station_id == rec.station_id,
                                        db.PasteJar.opened_at <= rec.ended_at,
                                        or_(db.PasteJar.ended_at.is_(None), db.PasteJar.ended_at >= rec.started_at)))
                .where(db.PasteJar.lot == lot, rec.step == "PASTE_PRINTER"))
        return sorted(set(session.scalars(reels)) | set(session.scalars(jars)))

    # ------------------------------------------------------------------ the page
    def view(self, session: Session, now: datetime, clock: dict, run_lines: list[str] | None = None) -> dict:
        lines: dict[str, dict] = {}
        st_rows = session.execute(select(db.Station.id, db.Station.code, db.StationType.code, db.Line.code)
                                  .join(db.StationType, db.StationType.id == db.Station.station_type_id)
                                  .join(db.Line, db.Line.id == db.Station.line_id)
                                  .order_by(db.Station.sort)).all()
        by_id = {}
        for sid, code, stype, line in st_rows:
            if run_lines and line not in run_lines:            # a tuning scenario without this line
                continue
            if stype in PLACERS or stype == "PASTE_PRINTER":
                by_id[sid] = (code, stype, line)
                entry = lines.setdefault(line, {"line": line, "printer": None, "placers": {}})
                if stype == "PASTE_PRINTER":
                    entry["printer"] = {"station": code, "jar": None}
                else:
                    entry["placers"][code] = {"station": code, "type": stype, "feeders": {}}
        parts = self.cfg.parts
        for reel in session.scalars(select(db.Reel).where(db.Reel.status.in_(("FEEDER", "NEXT")),
                                                          db.Reel.station_id.in_(by_id))):
            code, stype, line = by_id[reel.station_id]
            placer = lines[line]["placers"].get(code)
            if placer is None:
                continue
            feeder = placer["feeders"].setdefault(reel.slot or reel.part, {"slot": reel.slot or reel.part,
                                                                         "name": parts[reel.part].name if reel.part in parts else reel.part,
                                                                         "reel": None, "next": None})
            info = {"serial": reel.serial, "lot": reel.lot, "left": reel.left, "qty": reel.qty, "msl": reel.msl,
                    "floor_left_h": _r(self.floor_left_h(reel, now)),
                    "eta_min": round(reel.eta_s / 60) if reel.eta_s is not None else None}
            if reel.status == "FEEDER":
                feeder["reel"] = info
            else:
                feeder["next"] = info
        for jar in session.scalars(select(db.PasteJar).where(db.PasteJar.status == "OPEN",
                                                             db.PasteJar.station_id.in_(by_id))):
            code, stype, line = by_id[jar.station_id]
            open_h = (now - jar.opened_at).total_seconds() / 3600 if jar.opened_at else 0.0
            lines[line]["printer"]["jar"] = {
                "serial": jar.serial, "lot": jar.lot, "prints": jar.prints, "open_h": round(open_h, 2),
                "open_left_h": round(self.cfg.paste.open_h - open_h, 2),
                "thawed_h": round((jar.opened_at - jar.out_at).total_seconds() / 3600, 1) if jar.opened_at else None}
        day = now - timedelta(hours=24)
        scans = session.execute(select(db.MaterialScan, db.Station.code)
                                .outerjoin(db.Station, db.Station.id == db.MaterialScan.station_id)
                                .order_by(db.MaterialScan.at.desc(), db.MaterialScan.id.desc()).limit(40)).all()
        results = Counter({k: n for k, n in session.execute(
            select(db.MaterialScan.result, func.count()).where(db.MaterialScan.at >= day)
            .group_by(db.MaterialScan.result)).all()})
        kinds = Counter({k: n for k, n in session.execute(
            select(db.MaterialScan.kind, func.count()).where(db.MaterialScan.at >= day, db.MaterialScan.result == "OK")
            .group_by(db.MaterialScan.kind)).all()})
        empty = session.scalar(select(func.count()).select_from(db.FeederLoad).where(
            db.FeederLoad.ended_at >= day, db.FeederLoad.reason == "EMPTY")) or 0
        msl = []
        for reel in session.scalars(select(db.Reel).where(db.Reel.msl.is_not(None),
                                                          db.Reel.status.in_(("FEEDER", "NEXT", "LINE", "BAKE")))):
            left = self.floor_left_h(reel, now)
            line = by_id.get(reel.station_id, (None, None, None))[2]
            msl.append({"serial": reel.serial, "part": reel.part, "lot": reel.lot, "msl": reel.msl,
                        "status": reel.status, "line": line, "floor_left_h": _r(left)})
        msl.sort(key=lambda r: (r["status"] == "BAKE", r["floor_left_h"] if r["floor_left_h"] is not None else 1e9))
        counts: dict[str, Counter] = {}
        for part, status, n in session.execute(select(db.Reel.part, db.Reel.status, func.count())
                                               .where(db.Reel.status.in_(("STORE", "TRANSIT", "LINE", "FEEDER", "NEXT")))
                                               .group_by(db.Reel.part, db.Reel.status)):
            counts.setdefault(part, Counter())[status] += n
        sups = self.cfg.suppliers
        inventory = []
        for code, spec in parts.items():
            c = counts.get(code, Counter())
            inventory.append({"part": code, "name": spec.name, "supplier": sups[spec.supplier].name if spec.supplier in sups else None,
                              "per_day": spec.per_day, "store": c["STORE"], "transit": c["TRANSIT"], "line": c["LINE"],
                              "feeder": c["FEEDER"] + c["NEXT"], "days": round(c["STORE"] / spec.per_day, 1)})
        inventory.sort(key=lambda r: r["days"])
        deliveries = [{"note": d.note, "supplier": sups[d.supplier].name if d.supplier in sups else d.supplier,
                       "arrived": d.arrived_at.isoformat(), "expected": sum(d.expected.values()),
                       "received": sum(d.received.values()), "refused": d.refused}
                      for d in session.scalars(select(db.Delivery).where(db.Delivery.arrived_at >= day)
                                               .order_by(db.Delivery.arrived_at.desc()).limit(12))]
        out_lines = []
        for line in sorted(lines):
            entry = lines[line]
            entry["placers"] = [{**p, "feeders": sorted(p["feeders"].values(), key=lambda f: f["slot"])}
                                for p in entry["placers"].values()]
            out_lines.append(entry)
        return {
            "clock": clock, "rules": {"thaw_h": self.cfg.paste.thaw_h, "open_h": self.cfg.paste.open_h,
                                      "splice_warn_min": self.cfg.reels.splice_warn_min,
                                      "floor_h": self.cfg.msl.floor_h, "bake_h": self.cfg.msl.bake_h,
                                      "paste": self.cfg.paste.name},
            "lines": out_lines,
            "day": {"splices": kinds.get("SPLICE", 0), "loads": kinds.get("LOAD", 0), "jars": kinds.get("PASTE", 0),
                    "received": kinds.get("RECEIPT", 0), "issued": kinds.get("ISSUE", 0),
                    "empty": empty, "refused": {k: n for k, n in results.items() if k != "OK"}},
            "inventory": inventory, "deliveries": deliveries,
            "scans": [{"at": s.at.isoformat(), "station": code or s.place, "kind": s.kind, "item": s.item, "part": s.part,
                       "slot": s.slot, "result": s.result, "message": s.message} for s, code in scans],
            "msl": msl[:30],
        }


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 1)
