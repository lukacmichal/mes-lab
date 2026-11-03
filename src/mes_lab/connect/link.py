"""The connectivity of the live plant: the OPC UA server of the machines and the edge gateway's publisher
into the UNS, fed once a wall second by the live loop (sim/runner.py)."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mes_lab.model.schema import PlantModel
    from mes_lab.sim.plant import PlantSim

log = logging.getLogger("mes_lab.connect")

EVERY_S = 1.0


class ShopFloorLink:
    def __init__(self, model: PlantModel, mqtt_url: str | None = None, opcua_endpoint: str | None = None):
        self.uns = self.opcua = None
        self.next_at = 0.0
        if mqtt_url:
            from .uns import UnsPublisher

            self.uns = UnsPublisher(mqtt_url)
        if opcua_endpoint:
            from .opcua import OpcUaServer

            self.opcua = OpcUaServer(opcua_endpoint, model)
            if self.opcua.failed:
                log.warning("no OPC UA server: %s", self.opcua.failed)

    def update(self, plant: PlantSim) -> None:
        now = time.monotonic()
        if now < self.next_at:
            return
        self.next_at = now + EVERY_S
        try:
            if self.uns is not None:
                self.uns.publish(plant)
            if self.opcua is not None:
                self.opcua.update(plant)
        except Exception:  # noqa: BLE001 - the plant must not stop for its connectivity
            log.exception("shop-floor connectivity failed")

    def close(self) -> None:
        if self.uns is not None:
            self.uns.close()
        if self.opcua is not None:
            self.opcua.stop()


def from_env(model: PlantModel) -> ShopFloorLink | None:
    mqtt_url = os.environ.get("MES_MQTT_URL") or None
    endpoint = os.environ.get("MES_OPCUA_ENDPOINT") or None
    if not mqtt_url and not endpoint:
        return None
    return ShopFloorLink(model, mqtt_url, endpoint)
