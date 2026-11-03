"""Local development: MES and plant simulation in one process.

    python -m mes_lab.dev --speed 60

Starts the MES on http://127.0.0.1:8070 (with a private PostgreSQL in var/pgdata)
and the simulation in a background thread, with an MQTT broker for the UNS (amqtt) and the OPC UA server of
the machines on opc.tcp://127.0.0.1:4840/acme/plant1. Ctrl+C stops both.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import threading
import time
import webbrowser

import httpx
import uvicorn

from mes_lab import setup_logging
from mes_lab.mes.app import create_app
from mes_lab.model import load_model
from mes_lab.sim.runner import run_live

log = logging.getLogger("mes_lab.dev")


MQTT_PORT = 18830                # the broker of local development (amqtt), not the usual 1883
OPCUA_ENDPOINT = "opc.tcp://127.0.0.1:4840/acme/plant1"


def start_connectivity() -> None:
    """A local MQTT broker (amqtt, in a thread) and the OPC UA endpoint, for the MES and the simulation
    (the server uses Mosquitto, see deploy/setup-server.sh). Settings already in the environment win."""
    os.environ.setdefault("MES_OPCUA_ENDPOINT", OPCUA_ENDPOINT)
    os.environ.setdefault("MES_OPCUA_URL", OPCUA_ENDPOINT)
    if os.environ.get("MES_MQTT_URL"):
        return
    try:
        from amqtt.broker import Broker
    except ImportError:
        log.warning("no MQTT broker: pip install amqtt (the UNS stays off)")
        return
    ready = threading.Event()
    for name in ("amqtt", "transitions"):                    # the broker tells every connect at INFO
        logging.getLogger(name).setLevel(logging.WARNING)

    def run() -> None:
        async def main() -> None:
            broker = Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{MQTT_PORT}"}}})
            await broker.start()
            ready.set()
            while True:
                await asyncio.sleep(3600)
        asyncio.run(main())

    threading.Thread(target=run, name="mqtt-broker", daemon=True).start()
    if ready.wait(10):
        os.environ["MES_MQTT_URL"] = f"mqtt://127.0.0.1:{MQTT_PORT}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m mes_lab.dev")
    parser.add_argument("--port", type=int, default=8070)
    parser.add_argument("--speed", type=float, default=60)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--no-sim", action="store_true", help="only the MES")
    parser.add_argument("--open", action="store_true", help="open the browser when ready")
    parser.add_argument("--no-connect", action="store_true", help="no OPC UA server and no MQTT broker")
    args = parser.parse_args(argv)
    setup_logging()
    if not args.no_connect:
        start_connectivity()

    app = create_app()
    url = f"http://127.0.0.1:{args.port}"
    stop = threading.Event()

    def after_start() -> None:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/healthz", timeout=1, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        if args.open:
            webbrowser.open(url)
        if not args.no_sim:
            run_live(load_model(), url, args.speed, args.seed, stop=stop)

    threading.Thread(target=after_start, name="plant-sim", daemon=True).start()
    print(f"MES running on {url}  (speed {args.speed}x, Ctrl+C to stop)")
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        stop.set()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
