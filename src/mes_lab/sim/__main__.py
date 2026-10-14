"""Command line: python -m mes_lab.sim offline|run ..."""

from __future__ import annotations

import argparse
import os
import time

from mes_lab import setup_logging
from mes_lab.model import load_model

from .runner import run_live, run_offline, summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m mes_lab.sim",
                                     description="ACME Plant 1 simulation")
    parser.add_argument("--config", help="config directory (default: ./config)")
    parser.add_argument("--seed", type=int, default=1)
    sub = parser.add_subparsers(dest="cmd", required=True)
    off = sub.add_parser("offline", help="run without the MES as fast as possible")
    off.add_argument("--hours", type=float, default=24)
    live = sub.add_parser("run", help="run in real time against the MES")
    live.add_argument("--mes", default=os.environ.get("MES_URL", "http://127.0.0.1:8070"))
    live.add_argument("--speed", type=float, default=60, help="1 = real time, 600 = 10 min per second")
    live.add_argument("--token", default=os.environ.get("MES_EDGE_TOKEN"))
    args = parser.parse_args(argv)

    setup_logging()
    model = load_model(args.config)
    if args.cmd == "offline":
        t0 = time.perf_counter()
        plant = run_offline(model, args.hours, args.seed)
        print(summary(plant))
        print(f"\n(wall time {time.perf_counter() - t0:.1f} s)")
    else:
        run_live(model, args.mes, args.speed, args.seed, args.token, config_dir=args.config)


if __name__ == "__main__":
    main()
