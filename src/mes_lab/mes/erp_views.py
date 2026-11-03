"""Read models of the ERP page (/erp): the demand of the customers and how it is covered, the production
orders, the stock in the books of the ERP next to what the MES counts, and the interface monitor."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_lab.erp.system import Erp
from mes_lab.model.schema import PlantModel

from . import models as db
from .erp_link import ErpLink
from .views import Clock

KINDS = ("PRODUCTION_ORDER", "ORDER_RECEIVED", "CONFIRMATION", "DESADV", "STOCK_COUNT")


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


class ErpViews:
    def __init__(self, model: PlantModel, erp: Erp, link: ErpLink):
        self.model = model
        self.erp = erp
        self.link = link

    def summary(self, session: Session) -> dict:
        clock = Clock(session, self.model)
        now = clock.now
        today = self.erp.local(now).date()
        stock, shipped, supply = self.erp.stock(session), self.erp.shipped(session), self.erp.open_supply(session)
        run = session.scalar(select(db.SimRun).order_by(db.SimRun.started_wall.desc()).limit(1))
        counted = self.link.finished_stock(session, run)
        products = []
        for mat in self.model.finished_goods():
            demand = self.erp.demand(session, mat.code, now)
            left = shipped[mat.code]                 # shipped units cover the oldest demand first (FIFO)
            days, late = [], 0
            for due, qty, firm in demand:
                take = min(left, qty)
                left -= take
                open_qty = qty - take
                if due < today:
                    late += open_qty
                if due >= today - timedelta(days=1):
                    days.append({"due": due.isoformat(), "qty": qty, "firm": firm, "shipped": take, "open": open_qty})
            locks = [{"from": _iso(a), "to": _iso(b)} for a, b in self.erp.locks(mat.code, today)]
            products.append({"material": mat.code, "name": mat.name, "customer": mat.customer,
                             "rate": round(self.erp.rates.get(mat.code, 0.0) * self.model.erp.call_offs.load),
                             "days": days[:self.model.erp.call_offs.days + 1], "late": late,
                             "ahead": left, "stock": stock[mat.code], "counted": counted[mat.code],
                             "supply": supply[mat.code], "shipped": shipped[mat.code], "locks": locks,
                             "locked_until": _iso(self.erp.locked_until(mat.code, now))})
        call_offs = session.scalars(select(db.ErpCallOff).order_by(db.ErpCallOff.received_at.desc()).limit(12)).all()
        orders = session.scalars(select(db.ErpProductionOrder).order_by(db.ErpProductionOrder.id.desc()).limit(80)).all()
        counts = {f"{d}|{s}": n for d, s, n in session.execute(
            select(db.IntegrationMessage.direction, db.IntegrationMessage.status, func.count())
            .group_by(db.IntegrationMessage.direction, db.IntegrationMessage.status)).all()}
        totals = session.execute(select(func.coalesce(func.sum(db.IntegrationMessage.duplicates), 0),
                                        func.coalesce(func.sum(func.greatest(db.IntegrationMessage.attempts - 1, 0)), 0))
                                 .where(db.IntegrationMessage.created_at >= now - timedelta(days=1))).one()
        return {
            "clock": clock.as_dict(), "enabled": self.model.erp.enabled, "rules": self.model.erp.model_dump(),
            "down": self.erp.down(now), "today": today.isoformat(), "products": products,
            "call_offs": [{"number": c.number, "customer": c.customer, "material": c.material,
                           "received_at": _iso(c.received_at),
                           "lines": [{"due": l.due.isoformat(), "qty": l.qty, "firm": l.firm} for l in c.lines]}
                          for c in call_offs],
            "orders": [{"number": o.number, "material": o.material, "qty": o.qty, "due": o.due.isoformat(),
                        "status": o.status, "good": o.good, "scrap": o.scrap, "mes_order_no": o.mes_order_no,
                        "created_at": _iso(o.created_at), "received_at": _iso(o.received_at),
                        "closed_at": _iso(o.closed_at), "late": o.status != "CLOSED" and o.due < today}
                       for o in orders],
            "interface": {"counts": counts, "duplicates_24h": int(totals[0]), "retries_24h": int(totals[1])},
        }

    def messages(self, session: Session, status: str | None = None, kind: str | None = None,
                 limit: int = 100) -> dict:
        clock = Clock(session, self.model)
        q = select(db.IntegrationMessage).order_by(db.IntegrationMessage.created_at.desc()).limit(limit)
        if status:
            q = q.where(db.IntegrationMessage.status == status.upper())
        if kind:
            q = q.where(db.IntegrationMessage.kind == kind.upper())
        return {"clock": clock.as_dict(), "messages": [self.message(m) for m in session.scalars(q)]}

    @staticmethod
    def message(m: db.IntegrationMessage) -> dict:
        return {"id": m.id, "direction": m.direction, "kind": m.kind, "ref": m.ref, "status": m.status,
                "attempts": m.attempts, "duplicates": m.duplicates, "created_at": _iso(m.created_at),
                "next_at": _iso(m.next_at), "done_at": _iso(m.done_at), "error": m.error, "result": m.result,
                "payload": m.payload, "log": m.log or []}
