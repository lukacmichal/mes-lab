#!/usr/bin/env python3
"""Keep the VM above the idle line of Oracle Always Free.

Oracle may reclaim an Always Free instance that is idle for 7 days: 95th-percentile CPU under 20 %,
network under 20 % and (A1 shapes) memory under 20 %, all at once. A slow plant (1x) leaves the machine at
about 5 %. Every second this looks at how busy the whole machine was without it and burns what is missing
to KEEPALIVE_TARGET (percent of all cores, default 25) on one core, in short slices. It runs in the idle
scheduling class (see mes-lab-keepalive.service), so it only ever takes CPU time nobody else wants: when
the MES or the plant is busy, it stands back.

Standard library only; Linux (/proc/stat).
"""

import os
import time

TARGET = float(os.environ.get("KEEPALIVE_TARGET", "25"))
NCPU = os.cpu_count() or 1
TICK = os.sysconf("SC_CLK_TCK")
SLICE = 0.1                     # seconds: busy for `duty` of every slice, asleep for the rest


def machine() -> tuple[int, int]:
    """(all ticks, idle ticks) of all cores since boot."""
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:9]]   # user nice system idle iowait irq softirq steal
    return sum(v), v[3] + v[4]


def mine() -> float:
    t = os.times()
    return (t.user + t.system) * TICK


def burn(duty: float, seconds: float = 1.0) -> None:
    end = time.monotonic() + seconds
    while (start := time.monotonic()) < end:
        busy_until = start + SLICE * duty
        while time.monotonic() < busy_until:
            pass
        rest = start + SLICE - time.monotonic()
        if rest > 0:
            time.sleep(rest)


def main() -> None:
    duty = 0.0
    total0, idle0 = machine()
    own0 = mine()
    while True:
        burn(duty)
        total1, idle1 = machine()
        own1 = mine()
        span = total1 - total0
        if span > 0:
            busy = 100.0 * (span - (idle1 - idle0)) / span          # percent of all cores
            others = max(0.0, busy - 100.0 * (own1 - own0) / span)
            duty = min(1.0, max(0.0, TARGET - others) * NCPU / 100.0)   # of one core
        total0, idle0, own0 = total1, idle1, own1


if __name__ == "__main__":
    main()
