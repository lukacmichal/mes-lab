"""The MES side of the interface to the ERP (mes_lab.erp.bus).

In:  PRODUCTION_ORDER - a work order for the final assembly line that makes the product (the least loaded
     one), answered with ORDER_RECEIVED. The order number of the ERP is a second guard against booking it twice.
Out: CONFIRMATION every hour for the good and scrap units since the last one (the final one when the work
     order is complete), DESADV when a truck has left, STOCK_COUNT every morning and when a new run of the
     plant starts with an empty shop floor. A work order that starts again from nothing in a new run gets a
     reversal of what was confirmed of it (the units were lost with the shop floor).
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_lab import planning
from mes_lab.erp.bus import TO_ERP, Bus, Rejected
from mes_lab.model.schema import AreaKind, PlantModel

from . import models as db

log = logging.getLogger("mes_lab.erp_link")

CONFIRM_EVERY = timedelta(hours=1)


class ErpLink:
    def __init__(self, model: PlantModel, planner, bus: Bus):
        self.model = model
        self.planner = planner
        self.bus = bus
        self.tz = ZoneInfo(model.plant.timezone)
        self.last_confirm: datetime | None = None
        bus.connect("MES", self.handle)

    # ------------------------------------------------------------------ in
    def handle(self, session: Session, msg: db.IntegrationMessage, at: datetime) -> str:
        if msg.kind != "PRODUCTION_ORDER":
            raise Rejected(f"the MES does not take {msg.kind} messages")
        p = msg.payload
        known = session.scalar(select(db.WorkOrder).where(db.WorkOrder.erp_order_no == p["order"]))
        if known is not None:
            return f"known already as work order {known.order_no}"
        mat = self.model.materials.get(p["material"])
        if mat is None or mat.kind != "FG":
            raise Rejected(f"{p['material']} is not a finished good of the MES")
        line = self._line(session, mat.code)
        if line is None:
            raise Rejected(f"no final assembly line of the plant makes {mat.code}")
        order = self.planner.create_order(session, mat.code, int(p["qty"]), line, at)
        order.source = "ERP"
        order.erp_order_no = p["order"]
        self.bus.post(session, TO_ERP, "ORDER_RECEIVED", p["order"],
                      {"order": p["order"], "work_order": order.order_no, "line": line}, at)
        return f"work order {order.order_no} on {line}"

    def _line(self, session: Session, material: str) -> str | None:
        from .control import current_run, run_lines

        have = run_lines(session, current_run(session))
        lines = [l.code for l in self.model.lines_of_kind(AreaKind.FA)
                 if material in l.materials and (have is None or l.code in have)]
        if not lines and have is not None:          # a tuning plant without it: the line that is there
            lines = [l.code for l in self.model.lines_of_kind(AreaKind.FA) if l.code in have]
        if not lines:
            return None
        workload = {code: 0.0 for code in lines}
        last: dict[str, str | None] = {code: None for code in lines}
        rows = session.execute(select(db.Line.code, db.Material.code, db.Job.qty, db.Job.released)
                               .join(db.Line, db.Job.line_id == db.Line.id)
                               .join(db.WorkOrder, db.Job.work_order_id == db.WorkOrder.id)
                               .join(db.Material, db.WorkOrder.material_id == db.Material.id)
                               .where(db.Line.code.in_(lines), db.Job.status.in_(("QUEUED", "ACTIVE")))
                               .order_by(db.Job.id)).all()
        for line, mat, qty, released in rows:
            workload[line] += (qty - released) * self.model.materials[mat].routing[-1].bottleneck_s
            last[line] = mat
        return planning.pick_line(workload, last, material)

    # ------------------------------------------------------------------ out
    def tick(self, session: Session, at: datetime, run: db.SimRun | None) -> None:
        hourly = self.last_confirm is None or at >= self.last_confirm + CONFIRM_EVERY or at < self.last_confirm
        self.confirm(session, at, partial=hourly)
        if hourly:
            self.last_confirm = at
        self.desadv(session, at)
        self.stock_count(session, at, run)

    def confirm(self, session: Session, at: datetime, partial: bool = True) -> int:
        orders = session.scalars(select(db.WorkOrder).where(db.WorkOrder.erp_order_no.is_not(None),
                                                            db.WorkOrder.erp_final.is_(False))).all()
        sent = 0
        for order in orders:
            final = order.status in ("COMPLETED", "CANCELLED")
            good, scrap = order.qty_good - order.erp_good, order.qty_scrap - order.erp_scrap
            if not final and not (partial and (good or scrap)):
                continue
            self.bus.post(session, TO_ERP, "CONFIRMATION", order.erp_order_no,
                          {"order": order.erp_order_no, "work_order": order.order_no, "good": good, "scrap": scrap,
                           "final": final}, at)
            order.erp_good, order.erp_scrap, order.erp_final = order.qty_good, order.qty_scrap, final
            sent += 1
        return sent

    def reverse(self, session: Session, orders: list[db.WorkOrder], at: datetime) -> None:
        """Work orders that start again from nothing (a new run): what was confirmed of them is reversed."""
        for order in orders:
            if order.erp_order_no and (order.erp_good or order.erp_scrap):
                self.bus.post(session, TO_ERP, "CONFIRMATION", order.erp_order_no,
                              {"order": order.erp_order_no, "work_order": order.order_no, "good": -order.erp_good,
                               "scrap": -order.erp_scrap, "final": False, "reason": "lost at the restart"}, at)
                order.erp_good = order.erp_scrap = 0

    def desadv(self, session: Session, at: datetime) -> int:
        shipments = session.scalars(select(db.Shipment).where(db.Shipment.erp_sent.is_(False))
                                    .order_by(db.Shipment.departed_at)).all()
        for s in shipments:
            items = session.execute(select(db.Material.code, func.count())
                                    .join(db.Unit, db.Unit.material_id == db.Material.id)
                                    .where(db.Unit.shipment_id == s.id, db.Unit.kind == "UNIT")
                                    .group_by(db.Material.code).order_by(db.Material.code)).all()
            pallets = session.scalars(select(db.Unit.serial).where(db.Unit.shipment_id == s.id,
                                                                   db.Unit.kind == "PALLET")).all()
            self.bus.post(session, TO_ERP, "DESADV", s.asn_no,
                          {"asn": s.asn_no, "customer": s.customer, "plate": s.plate,
                           "departed": s.departed_at.isoformat(), "pallets": sorted(pallets),
                           "items": [{"material": m, "qty": n} for m, n in items]}, max(at, s.departed_at))
            s.erp_sent = True
        return len(shipments)

    def finished_stock(self, session: Session, run: db.SimRun | None) -> dict[str, int]:
        """Good finished goods of the run that have not left: waiting for packing, in boxes, on pallets."""
        counts: dict[str, int] = {}
        if run is not None:
            counts = dict(session.execute(
                select(db.Material.code, func.count()).join(db.Unit, db.Unit.material_id == db.Material.id)
                .where(db.Unit.run_id == run.id, db.Unit.kind == "UNIT", db.Unit.status.in_(("OK", "PACKED")))
                .group_by(db.Material.code)).all())
        return {m.code: int(counts.get(m.code) or 0) for m in self.model.finished_goods()}

    def stock_count(self, session: Session, at: datetime, run: db.SimRun | None, force: bool = False) -> bool:
        local = at.astimezone(self.tz)
        hh, mm = (int(x) for x in self.model.erp.stock_count_at.split(":"))
        since = datetime.combine(local.date(), time(hh, mm), self.tz)
        last = session.scalar(select(func.max(db.IntegrationMessage.created_at))
                              .where(db.IntegrationMessage.kind == "STOCK_COUNT"))
        if not force and last is not None and (local < since or last >= since):
            return False
        self.confirm(session, at)                 # what was made till now is booked before the count arrives
        self.desadv(session, at)
        stock = self.finished_stock(session, run)
        self.bus.post(session, TO_ERP, "STOCK_COUNT", f"COUNT-{local:%y%m%d-%H%M%S}",
                      {"counted_at": at.isoformat(), "stock": stock}, at + timedelta(seconds=1))
        return True
