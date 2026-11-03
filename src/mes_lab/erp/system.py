"""The fake ERP. It plans the finished goods; the MES plans the SMT boards for them itself (the supermarket).

* Every morning each customer sends a call-off per product: daily quantities for the next days, the first
  ones firm, the later ones a forecast that the next call-off may change. The demand of a day is the one
  of the newest call-off received that covers it.
* MRP: the demand due within the horizon (and what is late) minus what was shipped, the stock in the books
  and what the open production orders will still bring = the shortfall; it gets new production orders in
  the lot sizes of the product, sent to the MES.
* The answers of the MES are booked: ORDER_RECEIVED releases the order, CONFIRMATION books good and scrap
  with the goods receipt (the final one closes the order), DESADV the goods issue to the customer,
  STOCK_COUNT corrects the books to what the warehouse really holds.

The ERP knows only its own tables (erp_*) and the messages. Times are plant time.
"""

from __future__ import annotations

import logging
import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mes_lab.mes import models as db
from mes_lab.model.schema import PlantModel

from .bus import TO_ERP, TO_MES, Bus, Rejected

log = logging.getLogger("mes_lab.erp")

ORDER_BASE = 1_000_000                     # production order numbers 1000001, ... (as in many ERPs)


def _hm(text_: str) -> time:
    hh, mm = text_.split(":")
    return time(int(hh), int(mm))


def daily_rates(model: PlantModel) -> dict[str, float]:
    """Finished good -> units a day its final assembly lines make at the ideal rate of their constraint."""
    from mes_lab.mes.hourly import ideal_rates

    rates = ideal_rates(model)
    out: dict[str, float] = {}
    for mat in model.finished_goods():
        out[mat.code] = sum(rates.get((line.code, mat.code), 0.0) * 86400
                            for line in model.lines.values() if mat.code in line.materials
                            and (line.code, mat.code) in rates)
    return out


class Erp:
    def __init__(self, model: PlantModel, bus: Bus):
        self.model = model
        self.rules = model.erp
        self.bus = bus
        self.tz = ZoneInfo(model.plant.timezone)
        self.rates = daily_rates(model)
        self.last_mrp: datetime | None = None
        self.key_user_done: set[str] = set()
        bus.connect("ERP", self.handle, self.down)

    # ------------------------------------------------------------------ time
    def local(self, at: datetime) -> datetime:
        return at.astimezone(self.tz)

    def day_start(self, day: date) -> datetime:
        return datetime.combine(day, time(0), self.tz)

    def down(self, at: datetime) -> str | None:
        """Why the ERP does not answer now (its nightly maintenance), or None."""
        window = self.rules.interface.maintenance
        if not window:
            return None
        start, end = (_hm(t) for t in window)
        now = self.local(at).time()
        inside = start <= now < end if start <= end else (now >= start or now < end)
        return f"ERP down for maintenance until {window[1]}" if inside else None

    def locks(self, material: str, day: date) -> list[tuple[datetime, datetime]]:
        """The stock counts of a product that start on a day (each is the same every time it is asked)."""
        rng = random.Random(f"lock:{material}:{day.isoformat()}")
        if rng.random() >= self.rules.interface.lock_chance:
            return []
        start = self.day_start(day) + timedelta(hours=rng.uniform(6, 22))
        return [(start, start + timedelta(minutes=rng.uniform(*self.rules.interface.lock_min)))]

    def locked_until(self, material: str, at: datetime) -> datetime | None:
        day = self.local(at).date()
        for d in (day - timedelta(days=1), day):
            for start, end in self.locks(material, d):
                if start <= at < end:
                    return end
        return None

    # ------------------------------------------------------------------ the clock of the ERP
    def tick(self, session: Session, at: datetime) -> None:
        if not self.rules.enabled or self.down(at):          # in its maintenance the ERP does nothing at all
            return
        self.call_offs(session, at)
        if self.last_mrp is None or at >= self.last_mrp + timedelta(minutes=self.rules.mrp.every_min) \
                or at < self.last_mrp:
            self.mrp(session, at)
            self.last_mrp = at
        self.key_user(session, at)

    def key_user(self, session: Session, at: datetime) -> None:
        """At the times of the day the key user goes through the error queue."""
        local = self.local(at)
        for slot in self.rules.interface.key_user_at:
            start = datetime.combine(local.date(), _hm(slot), self.tz)
            key = f"{local.date().isoformat()} {slot}"
            if start <= local < start + timedelta(hours=2) and key not in self.key_user_done:
                self.key_user_done.add(key)
                n = self.bus.reprocess_errors(session, at, "the key user")
                if n:
                    log.info("key user: %d messages of the error queue processed again", n)

    # ------------------------------------------------------------------ call-offs
    def call_offs(self, session: Session, at: datetime) -> None:
        local = self.local(at)
        today = local.date()
        due_time = _hm(self.rules.call_offs.at)
        for mat in self.model.finished_goods():
            number = f"{mat.code}-{today:%y%m%d}"
            last = session.scalar(select(db.ErpCallOff).where(db.ErpCallOff.material == mat.code,
                                                               db.ErpCallOff.received_at <= at)
                                  .order_by(db.ErpCallOff.received_at.desc()).limit(1))
            if last is not None and (last.number == number or local.time() < due_time):
                continue
            if session.scalar(select(db.ErpCallOff.id).where(db.ErpCallOff.number == number)) is not None:
                continue
            self._call_off(session, mat, number, today, at, last)

    def _call_off(self, session: Session, mat, number: str, today: date, at: datetime,
                  last: db.ErpCallOff | None) -> db.ErpCallOff:
        rules = self.rules.call_offs
        before = {line.due: line.qty for line in last.lines} if last is not None else {}
        klt = mat.packing.klt_qty if mat.packing else 1
        base = self.rates.get(mat.code, 0.0) * rules.load
        call_off = db.ErpCallOff(number=number, customer=mat.customer or "", material=mat.code, received_at=at)
        for i in range(rules.days):
            due = today + timedelta(days=i)
            firm = i < rules.firm_days
            if firm and due in before:
                qty = before[due]                          # firm: the customer does not change it
            else:
                rng = random.Random(f"demand:{mat.code}:{today.isoformat()}:{due.isoformat()}")
                qty = round(base * (1 + rng.uniform(-rules.spread, rules.spread)) / klt) * klt
            call_off.lines.append(db.ErpCallOffLine(due=due, qty=max(qty, 0), firm=firm))
        session.add(call_off)
        session.flush()
        log.info("call-off %s from %s: %s", number, mat.customer, [l.qty for l in call_off.lines])
        return call_off

    def demand(self, session: Session, material: str, at: datetime) -> list[tuple[date, int, bool]]:
        """(due, qty, firm) of every day with a demand, from the newest call-off received that covers it."""
        rows = session.execute(text("""
            SELECT DISTINCT ON (l.due) l.due, l.qty, l.firm
              FROM erp_call_off_line l JOIN erp_call_off c ON c.id = l.call_off_id
             WHERE c.material = :m AND c.received_at <= :at
             ORDER BY l.due, c.received_at DESC"""), {"m": material, "at": at}).all()
        return [(r[0], r[1], r[2]) for r in rows]

    # ------------------------------------------------------------------ books
    def stock(self, session: Session, at: datetime | None = None) -> dict[str, int]:
        """The stock in the books (as of `at`: the moves posted up to then)."""
        q = select(db.ErpStockMove.material, func.sum(db.ErpStockMove.qty)).group_by(db.ErpStockMove.material)
        if at is not None:
            q = q.where(db.ErpStockMove.at <= at)
        rows = dict(session.execute(q).all())
        return {m.code: int(rows.get(m.code) or 0) for m in self.model.finished_goods()}

    def shipped(self, session: Session) -> dict[str, int]:
        rows = dict(session.execute(select(db.ErpStockMove.material, func.sum(db.ErpStockMove.qty))
                                    .where(db.ErpStockMove.kind == "GI").group_by(db.ErpStockMove.material)).all())
        return {m.code: -int(rows.get(m.code) or 0) for m in self.model.finished_goods()}

    def open_supply(self, session: Session) -> dict[str, int]:
        """What the open production orders will still bring (units not yet confirmed good or scrap)."""
        rows = session.execute(select(db.ErpProductionOrder.material,
                                      func.sum(func.greatest(db.ErpProductionOrder.qty - db.ErpProductionOrder.good
                                                             - db.ErpProductionOrder.scrap, 0)))
                               .where(db.ErpProductionOrder.status != "CLOSED")
                               .group_by(db.ErpProductionOrder.material)).all()
        got = dict(rows)
        return {m.code: int(got.get(m.code) or 0) for m in self.model.finished_goods()}

    # ------------------------------------------------------------------ MRP
    def mrp(self, session: Session, at: datetime) -> list[db.ErpProductionOrder]:
        today = self.local(at).date()
        horizon = today + timedelta(days=self.rules.mrp.horizon_days - 1)
        stock, shipped, supply = self.stock(session), self.shipped(session), self.open_supply(session)
        created = []
        for mat in self.model.finished_goods():
            lines = [(due, qty) for due, qty, _ in self.demand(session, mat.code, at) if due <= horizon]
            covered = shipped[mat.code] + max(stock[mat.code], 0) + supply[mat.code]
            lots = sorted(mat.planning.lot_sizes) if mat.planning and mat.planning.lot_sizes else [mat.packing.klt_qty]
            while True:
                cumulative, due_of_gap = 0, None
                for due, qty in lines:
                    cumulative += qty
                    if cumulative > covered:
                        due_of_gap = due
                        break
                need = sum(q for _, q in lines) - covered
                if due_of_gap is None or need <= 0:
                    break
                lot = next((l for l in lots if l >= need), lots[-1])
                created.append(self._order(session, mat, lot, max(due_of_gap, today), at))
                covered += lot
        return created

    def _order(self, session: Session, mat, qty: int, due: date, at: datetime) -> db.ErpProductionOrder:
        order = db.ErpProductionOrder(number="NEW", material=mat.code, qty=qty, due=due, status="CREATED",
                                      created_at=at, good=0, scrap=0)
        session.add(order)
        session.flush()
        order.number = str(ORDER_BASE + order.id)
        self.bus.post(session, TO_MES, "PRODUCTION_ORDER", order.number,
                      {"order": order.number, "material": mat.code, "qty": qty, "due": due.isoformat(),
                       "customer": mat.customer}, at)
        log.info("MRP: production order %s %s x %d due %s", order.number, mat.code, qty, due)
        return order

    # ------------------------------------------------------------------ what the MES sends
    def handle(self, session: Session, msg: db.IntegrationMessage, at: datetime) -> str:
        """Goods movements are posted on the date of the event (when the MES sent it), not when the message
        got through: a confirmation that waited in the error queue still belongs before a later stock count."""
        p = msg.payload
        when = msg.created_at
        if msg.kind == "ORDER_RECEIVED":
            order = self._find(session, p["order"])
            order.mes_order_no = p["work_order"]
            order.received_at = order.received_at or at
            if order.status == "CREATED":
                order.status = "RELEASED"
            return f"order {order.number} released, MES work order {order.mes_order_no}"
        if msg.kind == "CONFIRMATION":
            order = self._find(session, p["order"])
            good, scrap = int(p["good"]), int(p["scrap"])
            if good:
                self._not_locked(order.material, at)
            session.add(db.ErpConfirmation(order_id=order.id, message_id=msg.id, good=good, scrap=scrap,
                                           final=bool(p.get("final")), at=when))
            order.good += good
            order.scrap += scrap
            if good:
                session.add(db.ErpStockMove(material=order.material, qty=good, kind="GR" if good > 0 else "GR_REV",
                                            ref=order.number, at=when))
            done = ""
            if p.get("final") and order.status != "CLOSED":
                order.status = "CLOSED"
                order.closed_at = at
                done = ", order closed"
            what = "reversal" if good < 0 else "goods receipt"
            return f"{order.number}: {good:+d} good, {scrap:+d} scrap ({what}){done}"
        if msg.kind == "DESADV":
            items = p["items"]
            for item in items:
                self._not_locked(item["material"], at)
            for item in items:
                session.add(db.ErpStockMove(material=item["material"], qty=-int(item["qty"]), kind="GI",
                                            ref=p["asn"], customer=p.get("customer"), at=when))
            return f"goods issue {p['asn']}: " + ", ".join(f"{i['qty']} {i['material']}" for i in items)
        if msg.kind == "STOCK_COUNT":
            waiting = session.scalar(select(func.count()).select_from(db.IntegrationMessage).where(
                db.IntegrationMessage.direction == TO_ERP, db.IntegrationMessage.status == "ERROR",
                db.IntegrationMessage.created_at < when))
            if waiting:                    # booked after the count they would be counted twice
                raise Rejected(f"{waiting} older goods movements wait in the error queue: process them first")
            book = self.stock(session, when)
            diffs = []
            for material, counted in p["stock"].items():
                diff = int(counted) - book.get(material, 0)
                if diff:
                    session.add(db.ErpStockMove(material=material, qty=diff, kind="COUNT", ref=msg.ref, at=when))
                    diffs.append(f"{material} {diff:+d}")
            return "stock count: " + (", ".join(diffs) if diffs else "the books agree")
        raise Rejected(f"unknown message type {msg.kind}")

    @staticmethod
    def _find(session: Session, number: str) -> db.ErpProductionOrder:
        order = session.scalar(select(db.ErpProductionOrder).where(db.ErpProductionOrder.number == number))
        if order is None:
            raise Rejected(f"production order {number} does not exist in the ERP")
        return order

    def _not_locked(self, material: str, at: datetime) -> None:
        until = self.locked_until(material, at)
        if until is not None:
            raise Rejected(f"{material} is locked for a stock count until {self.local(until):%H:%M}")

    # ------------------------------------------------------------------ the plant goes back in time
    REWIND = [
        "DELETE FROM integration_inbox WHERE processed_at >= :t",
        "DELETE FROM integration_message WHERE created_at >= :t",
        "DELETE FROM erp_stock_move WHERE at >= :t",
        "DELETE FROM erp_confirmation WHERE at >= :t",
        "DELETE FROM erp_production_order WHERE created_at >= :t",
        "DELETE FROM erp_call_off WHERE received_at >= :t",
        """UPDATE erp_production_order o SET
               good = COALESCE((SELECT sum(c.good) FROM erp_confirmation c WHERE c.order_id = o.id), 0),
               scrap = COALESCE((SELECT sum(c.scrap) FROM erp_confirmation c WHERE c.order_id = o.id), 0),
               status = CASE WHEN o.closed_at >= :t THEN 'RELEASED' ELSE o.status END,
               closed_at = CASE WHEN o.closed_at >= :t THEN NULL ELSE o.closed_at END""",
    ]


def rewind(session: Session, start: datetime) -> None:
    """What the ERP and the interface hold from `start` on did not happen (mes_lab.mes.control.rewind)."""
    for sql in Erp.REWIND:
        session.execute(text(sql), {"t": start})
