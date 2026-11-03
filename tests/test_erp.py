"""The fake ERP and its interface to the MES."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_lab.erp.bus import TO_ERP, TO_MES, Bus, Rejected, post
from mes_lab.erp.system import Erp, daily_rates
from mes_lab.mes import models as db
from mes_lab.model.schema import InterfaceRules
from mes_lab.sim.mes_client import MesClient

START = datetime.fromisoformat("2026-01-05T08:00:00+01:00")


def _start(client, at=START, seed=5):
    run_id = str(uuid.uuid4())
    mes = MesClient(client=client)
    mes.register_run(run_id, seed, None, {}, plant_start=at.isoformat())
    return mes, run_id


def test_the_demand_follows_what_the_lines_can_make(model):
    rates = daily_rates(model)
    assert rates["BCM-200"] > 1.9 * rates["LED-DRV-40"] * 0.9          # two lines make BCM-200, one LED-DRV-40
    assert all(r > 1000 for r in rates.values())


def test_final_assembly_works_on_orders_of_the_erp(app, client):
    mes, run_id = _start(client)
    first = mes.next_job(run_id, "FA-01", START.isoformat())   # the ERP plans; its orders are on the way
    later = (START + timedelta(minutes=1)).isoformat()
    job = first or mes.next_job(run_id, "FA-01", later)
    assert job["material"] == "BCM-200" and job["operation"] == "FA"
    with Session(app.state.engine) as s:
        offs = s.scalars(select(db.ErpCallOff)).all()
        assert {c.material for c in offs} == {"BCM-200", "LED-DRV-40", "BMS-12S", "PDC-8"}
        assert all(len(c.lines) == 10 and c.lines[0].firm and not c.lines[-1].firm for c in offs)
        assert all(l.qty % 24 == 0 for l in offs[0].lines if offs[0].material == "BCM-200")
        wo = s.scalar(select(db.WorkOrder).where(db.WorkOrder.order_no == job["order_no"]))
        assert wo.source == "ERP" and wo.erp_order_no
        # the plan covers two days of demand
        demand = sum(l.qty for c in offs if c.material == "BCM-200" for l in c.lines[:2])
        planned = s.scalar(select(func.sum(db.ErpProductionOrder.qty)).where(db.ErpProductionOrder.material == "BCM-200"))
        assert demand <= planned < demand + 960
    client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": (START + timedelta(minutes=3)).isoformat()})
    with Session(app.state.engine) as s:
        po = s.scalar(select(db.ErpProductionOrder).where(db.ErpProductionOrder.number == wo.erp_order_no))
        assert po.status == "RELEASED" and po.mes_order_no == wo.order_no
    summary = client.get("/api/v1/erp").json()
    bcm = next(p for p in summary["products"] if p["material"] == "BCM-200")
    assert bcm["supply"] == planned and bcm["days"][0]["qty"] > 0
    assert client.get("/erp").status_code == 200
    # a message sent twice (the answer got lost) is booked once
    with Session(app.state.engine) as s:
        msg = s.scalar(select(db.IntegrationMessage).where(db.IntegrationMessage.kind == "PRODUCTION_ORDER")
                       .order_by(db.IntegrationMessage.created_at).limit(1))
        msg.status, msg.next_at = "PENDING", START + timedelta(minutes=3)
        mid = msg.id
        s.commit()
        orders = s.scalar(select(func.count()).select_from(db.WorkOrder))
    client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": (START + timedelta(minutes=5)).isoformat()})
    with Session(app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(db.WorkOrder)) == orders
        assert s.get(db.IntegrationMessage, mid).duplicates == 1


def _bus(app, **rules):
    bus = Bus(InterfaceRules(**{"delay_s": 0, "lost_ack": 0, "retry_min": [1, 5], **rules}))
    seen: list[str] = []

    def handler(session, msg, at):
        if msg.payload.get("bad"):
            raise Rejected("locked")
        seen.append(msg.ref)
        return f"ok {msg.ref}"
    return bus, seen, handler


def test_the_interface_keeps_order_retries_and_parks_refused_messages(app):
    bus, seen, handler = _bus(app)
    down_until = START + timedelta(minutes=10)
    bus.connect("ERP", handler, lambda at: "maintenance" if at < down_until else None)
    with Session(app.state.engine) as s:
        for i, bad in enumerate([False, True, False]):
            post(s, TO_ERP, "CONFIRMATION", f"m{i}", {"bad": bad}, START + timedelta(seconds=i), 0)
        s.flush()
        assert bus.deliver(s, START + timedelta(minutes=1)) == 1           # down: the first waits, the rest behind it
        rows = {m.ref: m for m in s.scalars(select(db.IntegrationMessage))}
        assert rows["m0"].status == "PENDING" and rows["m0"].attempts == 1 and rows["m1"].attempts == 0
        bus.deliver(s, START + timedelta(minutes=11))
        assert seen == ["m0", "m2"]                                         # in order; the refused one does not block
        assert rows["m1"].status == "ERROR" and rows["m1"].error == "locked"
        rows["m1"].payload = {"bad": False}
        bus.reprocess(s, rows["m1"], START + timedelta(minutes=12), "test")
        bus.deliver(s, START + timedelta(minutes=12))
        assert seen == ["m0", "m2", "m1"] and rows["m1"].status == "DONE"
        s.rollback()


def test_a_lost_answer_makes_the_sender_try_again_and_the_receiver_books_once(app):
    bus, seen, handler = _bus(app, lost_ack=0.999)
    bus.connect("MES", handler)
    with Session(app.state.engine) as s:
        post(s, TO_MES, "PRODUCTION_ORDER", "o1", {}, START, 0)
        s.flush()
        bus.deliver(s, START)
        msg = s.scalar(select(db.IntegrationMessage))
        assert msg.status == "PENDING" and "timeout" in msg.error and seen == ["o1"]
        bus.deliver(s, START + timedelta(minutes=2))
        assert msg.status == "DONE" and msg.duplicates == 1 and seen == ["o1"]
        s.rollback()


def test_the_erp_books_confirmations_deliveries_and_the_stock_count(app, model):
    bus = Bus(InterfaceRules(delay_s=0, lost_ack=0, maintenance=[], lock_chance=0))
    erp = Erp(model, bus)
    with Session(app.state.engine) as s:
        order = erp._order(s, model.materials["PDC-8"], 600, START.date(), START)
        def send(kind, payload, minutes):
            post(s, TO_ERP, kind, kind, payload, START + timedelta(minutes=minutes), 0)
            s.flush()
            bus.deliver(s, START + timedelta(minutes=minutes))
        send("ORDER_RECEIVED", {"order": order.number, "work_order": "WO1"}, 1)
        send("CONFIRMATION", {"order": order.number, "good": 300, "scrap": 2, "final": False}, 2)
        assert erp.stock(s)["PDC-8"] == 300 and erp.open_supply(s)["PDC-8"] == 298
        send("DESADV", {"asn": "ASN1", "customer": "NORDAUTO", "items": [{"material": "PDC-8", "qty": 240}]}, 3)
        assert erp.stock(s)["PDC-8"] == 60 and erp.shipped(s)["PDC-8"] == 240
        send("CONFIRMATION", {"order": order.number, "good": 290, "scrap": 8, "final": True}, 4)
        assert order.status == "CLOSED" and order.good == 590 and erp.open_supply(s)["PDC-8"] == 0
        send("STOCK_COUNT", {"stock": {"PDC-8": 330}}, 5)                 # 20 units fewer on the shelves
        assert erp.stock(s)["PDC-8"] == 330
        assert s.scalar(select(db.ErpStockMove.qty).where(db.ErpStockMove.kind == "COUNT")) == -20
        s.rollback()


def test_a_stock_count_lock_sends_a_goods_movement_to_the_error_queue(app, model):
    bus = Bus(InterfaceRules(delay_s=0, lost_ack=0, maintenance=[], lock_chance=1, lock_min=[30, 30]))
    erp = Erp(model, bus)
    start, end = erp.locks("BCM-200", START.date())[0]
    assert erp.locked_until("BCM-200", start + timedelta(minutes=1)) == end
    with Session(app.state.engine) as s:
        order = erp._order(s, model.materials["BCM-200"], 480, START.date(), START)
        post(s, TO_ERP, "CONFIRMATION", order.number, {"order": order.number, "good": 24, "scrap": 0}, start, 0)
        s.flush()
        bus.deliver(s, start + timedelta(minutes=1))
        msg = s.scalar(select(db.IntegrationMessage).where(db.IntegrationMessage.kind == "CONFIRMATION"))
        assert msg.status == "ERROR" and "locked for a stock count" in msg.error and erp.stock(s)["BCM-200"] == 0
        # the stock count of the morning finds the 24 units on the shelf: it waits until the confirmation is booked
        post(s, TO_ERP, "STOCK_COUNT", "COUNT", {"stock": {"BCM-200": 24}}, start + timedelta(minutes=2), 0)
        s.flush()
        bus.deliver(s, start + timedelta(minutes=2))
        count = s.scalar(select(db.IntegrationMessage).where(db.IntegrationMessage.kind == "STOCK_COUNT"))
        assert count.status == "ERROR" and "error queue" in count.error
        assert bus.reprocess_errors(s, end, "key user") == 2
        bus.deliver(s, end)                                    # in their order: the confirmation, then the count
        assert msg.status == count.status == "DONE" and erp.stock(s)["BCM-200"] == 24
        assert count.result == "stock count: the books agree"
        s.rollback()
