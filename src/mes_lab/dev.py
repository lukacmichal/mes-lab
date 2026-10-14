"""Local development: MES and plant simulation in one process.

    python -m mes_lab.dev --speed 60

Starts the MES on http://127.0.0.1:8070 (with a private PostgreSQL in var/pgdata)
and the simulation in a background thread. Ctrl+C stops both.
"""

from __future__ import annotations

import argparse
import logging
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m mes_lab.dev")
    parser.add_argument("--port", type=int, default=8070)
    parser.add_argument("--speed", type=float, default=60)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--no-sim", action="store_true", help="only the MES")
    parser.add_argument("--open", action="store_true", help="open the browser when ready")
    args = parser.parse_args(argv)
    setup_logging()

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
