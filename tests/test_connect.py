"""Phase 5: the OPC UA server of the machines and the Unified Namespace in MQTT, end to end through the MES."""

import asyncio
import socket
import threading
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from mes_lab.connect.link import ShopFloorLink
from mes_lab.connect.opcua import node_values
from mes_lab.connect.shopfloor import topic_of, views
from mes_lab.connect.uns import plant_topics
from mes_lab.sim.mes_client import MesClient
from mes_lab.sim.runner import run_offline, start_live_plant


@pytest.fixture(scope="module")
def offline_plant(model):
    return run_offline(model, 1.0, 3)


def test_the_names_follow_isa95(model, offline_plant):
    assert topic_of(model, "FA-01", "FA-01-SCR") == "acme/plant1/fa/fa-01/scr"
    topics = plant_topics(model, views(offline_plant))
    pp1 = "acme/plant1/smt/smt-01/pp1"
    assert topics[f"{pp1}/state"]["value"] in ("RUNNING", "STARVED", "BLOCKED", "MINOR_STOP", "CHANGEOVER", "DOWN")
    assert set(topics[f"{pp1}/counts"]) == {"cycles", "good", "bad"}
    assert topics["acme/plant1/clock"]["run_id"] == "offline"
    spi = [t for t in topics if t.startswith("acme/plant1/smt/smt-01/spi/values/")]
    assert spi and topics[spi[0]]["unit"] == "%"
    nodes = node_values(model, views(offline_plant))
    assert nodes["Plant1.SMT.SMT-01.SMT-01-PP1.State"] == topics[f"{pp1}/state"]["value"]
    assert isinstance(nodes["Plant1.PlantTime"], datetime)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def broker():
    amqtt = pytest.importorskip("amqtt.broker")
    port, ready = _free_port(), threading.Event()

    def run() -> None:
        async def main() -> None:
            b = amqtt.Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}}})
            await b.start()
            ready.set()
            while True:
                await asyncio.sleep(3600)
        asyncio.run(main())
    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(10)
    return f"mqtt://127.0.0.1:{port}"


def _until(check, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        result = check()
        if result:
            return result
        time.sleep(0.2)
    return check()


def test_the_plant_speaks_opc_ua_and_mqtt_and_the_mes_sees_both(pg_url, tmp_path, monkeypatch, model, broker):
    from mes_lab.mes.app import create_app

    endpoint = f"opc.tcp://127.0.0.1:{_free_port()}/acme/plant1"
    monkeypatch.setenv("MES_VAR_DIR", str(tmp_path))
    monkeypatch.setenv("MES_MQTT_URL", broker)
    monkeypatch.setenv("MES_OPCUA_URL", endpoint)
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text("drop schema public cascade; create schema public"))
    engine.dispose()
    app = create_app(pg_url, edge_token="", require_login=False)
    link = ShopFloorLink(model, broker, endpoint)
    try:
        with TestClient(app) as client:
            plant = start_live_plant(model, MesClient(client=client), seed=7, speed=None, sim_flush_s=120,
                                     plant_start=datetime.fromisoformat("2026-01-05T08:00:00+01:00"))
            assert _until(lambda: link.uns.conn.connected)
            for minutes in range(10, 61, 10):
                plant.env.run(until=minutes * 60)
                plant.edge.flush()
                link.next_at = 0
                link.update(plant)

            # the UNS, as the MES mirrors it
            def tree():
                rows = {r["topic"]: r["payload"] for r in client.get("/api/v1/uns").json()["rows"]}
                return rows if "acme/plant1/fa/fa-01/mes/orders" in rows and "acme/plant1/smt/smt-01/pp1/state" in rows else None
            rows = _until(tree)
            assert rows, client.get("/api/v1/uns").json()["status"]
            assert rows["acme/plant1/edge/status"] == {"online": True}
            assert rows["acme/plant1/mes/status"] == {"online": True}
            state = rows["acme/plant1/fa/fa-01/scr/state"]
            assert state["value"] and state["t"]
            orders = rows["acme/plant1/fa/fa-01/mes/orders"]
            assert orders["active"] and orders["active"][0]["material"] == "BCM-200"
            status = client.get("/api/v1/uns").json()["status"]
            assert status["connected"] and status["topics"] > 400

            # OPC UA, browsed through the MES
            top = client.get("/api/v1/opcua/browse").json()
            assert [c["name"] for c in top["children"]] == ["Plant1"]
            scr = client.get("/api/v1/opcua/browse?node=Plant1.FA.FA-01.FA-01-SCR").json()
            values = {c["name"]: c.get("value") for c in scr["children"]}
            assert _until(lambda: client.get("/api/v1/opcua/browse?node=Plant1.FA.FA-01.FA-01-SCR").json()["children"]
                          and {c["name"]: c.get("value") for c in client.get("/api/v1/opcua/browse?node=Plant1.FA.FA-01.FA-01-SCR").json()["children"]}["State"] == state["value"])
            assert values["Good"] >= 0 and "Measurements" in values
            meas = client.get("/api/v1/opcua/browse?node=Plant1.FA.FA-01.FA-01-SCR.Measurements").json()["children"]
            assert {m["name"] for m in meas} and all(m["type"] == "Double" for m in meas)
            assert client.get("/api/v1/opcua/browse?node=Nope").status_code == 404
            assert client.get("/connect").status_code == 200

            # the edge goes: the tree says so
            link.close()
            link = None
            assert _until(lambda: {r["topic"]: r["payload"] for r in client.get("/api/v1/uns").json()["rows"]}
                          .get("acme/plant1/edge/status") == {"online": False})
    finally:
        if link is not None:
            link.close()
        app.state.engine.dispose()
