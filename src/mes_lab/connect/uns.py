"""The Unified Namespace (UNS) in MQTT: one tree of topics for the whole plant, every value at one place, its
present value retained by the broker.

    acme/plant1/clock                              plant time, speed, paused
    acme/plant1/edge/status                        {"online": true}; the broker sets false when the edge drops (last will)
    acme/plant1/<area>/<line>/job                  the order the line works on, how far it is
    acme/plant1/<area>/<line>/<station>/state      RUNNING, DOWN, ... with the reason and since when
    acme/plant1/<area>/<line>/<station>/piece      the serial number in the station, its order and product
    acme/plant1/<area>/<line>/<station>/counts     cycles (panels on SMT, units in assembly), good and bad pieces (this run)
    acme/plant1/<area>/<line>/<station>/cycle      the last cycle in seconds and its result
    acme/plant1/<area>/<line>/<station>/values/<name>  the last process value (paste volume, torque, ...)
    acme/plant1/<area>/<line>/mes/orders           (the MES) the order in work and the queue of the line
    acme/plant1/mes/status                         (the MES) online / offline

A payload is JSON with the plant time `t` of the change. Only changes are published, so a value that stays
the same costs nothing; a newcomer still gets everything at once from the retained messages.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .shopfloor import ROOT, station_tag, topic_of, units, views

if TYPE_CHECKING:
    from mes_lab.model.schema import PlantModel
    from mes_lab.sim.plant import PlantSim

log = logging.getLogger("mes_lab.uns")


def _client(client_id: str):
    import paho.mqtt.client as mqtt

    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)


def broker_of(url: str) -> tuple[str, int]:
    parsed = urlparse(url if "://" in url else f"mqtt://{url}")
    return parsed.hostname or "127.0.0.1", parsed.port or 1883


def _dump(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def plant_topics(model: PlantModel, v: dict[str, Any], offline: set[str] = frozenset()) -> dict[str, dict]:
    """Topic -> payload (without the time of the change) of everything the machines show."""
    out: dict[str, dict] = {f"{ROOT}/clock": v["plant"]}
    unit_of = units(model)
    for code, line in v["lines"].items():
        out[f"{topic_of(model, code)}/job"] = line
    for code, st in v["stations"].items():
        spec = model.stations[code]
        base = topic_of(model, spec.line, code)
        out[f"{base}/state"] = {"value": st["state"], "reason": st["reason"], "since": st["since"]}
        out[f"{base}/piece"] = {"serial": st["serial"], "order": st["order"], "material": st["material"]}
        out[f"{base}/counts"] = {"cycles": st["cycles"], "good": st["good"], "bad": st["bad"]}
        if st["cycle_s"] is not None:
            out[f"{base}/cycle"] = {"seconds": st["cycle_s"], "result": st["result"]}
        for name, value in st["values"].items():
            out[f"{base}/values/{name}"] = {"value": value, "unit": unit_of.get(spec.type, {}).get(name, "")}
    for code in offline:                             # stations of the model a tuning plant does not have
        spec = model.stations[code]
        out[f"{topic_of(model, spec.line, code)}/state"] = {"value": "OFFLINE", "reason": "not in this plant", "since": None}
    return out


class _Connection:
    """An MQTT connection that comes back by itself; `status_topic` is online while connected (last will)."""

    def __init__(self, url: str, client_id: str, status_topic: str, subscribe: str | None = None):
        self.url = url
        self.host, self.port = broker_of(url)
        self.status_topic = status_topic
        self.subscribe = subscribe
        self.connected = False
        self.last: dict[str, str] = {}                  # topic -> what was sent (without the time)
        self.sent = 0
        self.client = _client(client_id)
        self.client.will_set(status_topic, _dump({"online": False}), qos=1, retain=True)
        self.client.reconnect_delay_set(1, 30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason, properties=None):
        if getattr(reason, "is_failure", False):
            log.warning("MQTT %s:%s refused: %s", self.host, self.port, reason)
            return
        self.connected = True
        self.last.clear()                                # after a reconnect everything is sent again
        client.publish(self.status_topic, _dump({"online": True}), qos=1, retain=True)
        if self.subscribe:
            client.subscribe(self.subscribe, qos=0)
        log.info("MQTT connected to %s:%s", self.host, self.port)

    def _on_disconnect(self, client, userdata, flags, reason, properties=None):
        self.connected = False

    def publish_changes(self, topics: dict[str, dict], t: str | None) -> int:
        if not self.connected:
            return 0
        n = 0
        for topic, payload in topics.items():
            key = _dump(payload)
            if self.last.get(topic) == key:
                continue
            self.client.publish(topic, _dump({**payload, "t": t}), qos=0, retain=True)
            self.last[topic] = key
            n += 1
        self.sent += n
        return n

    def close(self) -> None:
        try:
            self.client.publish(self.status_topic, _dump({"online": False}), qos=1, retain=True).wait_for_publish(2)
        except Exception:  # noqa: BLE001 - going down anyway
            pass
        self.client.disconnect()
        self.client.loop_stop()


class UnsPublisher:
    """The edge gateway's side: every wall second what changed on the machines goes to the broker."""

    def __init__(self, url: str, client_id: str = "acme-plant1-edge"):
        self.conn = _Connection(url, client_id, f"{ROOT}/edge/status")

    def publish(self, plant: PlantSim) -> int:
        v = views(plant)
        offline = {code for code in plant.model.stations if code not in v["stations"]}
        return self.conn.publish_changes(plant_topics(plant.model, v, offline), v["plant"]["plant_time"])

    def close(self) -> None:
        self.conn.close()


class UnsMirror:
    """The MES's side: it publishes its own data into the tree and keeps a copy of the whole tree (every
    topic, its last payload, when it came) for its web page."""

    def __init__(self, url: str, client_id: str = "acme-plant1-mes"):
        self.lock = threading.Lock()
        self.topics: dict[str, dict] = {}
        self.arrivals: deque[float] = deque(maxlen=5000)
        self.received = 0
        self.conn = _Connection(url, client_id, f"{ROOT}/mes/status", subscribe=f"{ROOT}/#")
        self.conn.client.on_message = self._on_message

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload: Any = json.loads(msg.payload.decode("utf-8")) if msg.payload else None
        except (ValueError, UnicodeDecodeError):
            payload = msg.payload.decode("utf-8", "replace")
        now = time.time()
        with self.lock:
            row = self.topics.get(msg.topic)
            if payload is None and not msg.payload:     # an empty retained message deletes the topic
                self.topics.pop(msg.topic, None)
            else:
                self.topics[msg.topic] = {"payload": payload, "retained": bool(msg.retain), "at": now,
                                          "n": (row["n"] + 1) if row else 1}
            self.arrivals.append(now)
            self.received += 1

    def publish(self, topics: dict[str, dict], t: str | None) -> int:
        return self.conn.publish_changes(topics, t)

    def status(self) -> dict:
        now = time.time()
        with self.lock:
            rate = sum(1 for a in self.arrivals if a > now - 10) / 10
            return {"configured": True, "connected": self.conn.connected, "broker": f"{self.conn.host}:{self.conn.port}",
                    "topics": len(self.topics), "received": self.received, "per_s": round(rate, 1),
                    "published": self.conn.sent}

    def tree(self, prefix: str = "", limit: int = 3000) -> list[dict]:
        now = time.time()
        with self.lock:
            rows = [{"topic": t, "payload": r["payload"], "retained": r["retained"], "age_s": round(now - r["at"], 1),
                     "n": r["n"]} for t, r in self.topics.items() if t.startswith(prefix)]
        rows.sort(key=lambda r: r["topic"])
        return rows[:limit]

    def close(self) -> None:
        self.conn.close()


def mes_topics(model: PlantModel, queues: dict[str, dict]) -> dict[str, dict]:
    """What the MES itself puts into the tree: the order in work and the queue of every job line."""
    return {f"{topic_of(model, line)}/mes/orders": q for line, q in queues.items() if line in model.lines}


__all__ = ["UnsPublisher", "UnsMirror", "plant_topics", "mes_topics", "broker_of", "station_tag"]
