"""Where do people and robots come too close? Runs the plant offline and looks every second at where
everybody is (the way the page draws them), then lists the pairs that overlap and where.

    python scripts/overlaps.py [hours] [seed]
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from datetime import UTC, datetime

from mes_lab.layout_crew import path_length
from mes_lab.model import load_model
from mes_lab.sim.core import PlantClock
from mes_lab.sim.dispatch import LocalDispatcher
from mes_lab.sim.plant import PlantSim
from mes_lab.sim.traffic import touch

LIMIT = {("P", "P"): 7.5, ("P", "R"): 40.0, ("R", "R"): 40.0}   # robots: the rectangles must touch (see traffic.touch)


def heading(walk, t):
    pts, t0, d = walk
    f = min(1.0, max(0.0, (t - t0) / d)) if d > 0 else 1.0
    target = f * path_length(pts)
    run = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        if run + seg >= target and seg > 0:
            return 0.0 if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else 90.0
        run += seg
    return 0.0


def at(walk, t):
    pts, t0, d = walk
    f = min(1.0, max(0.0, (t - t0) / d)) if d > 0 else 1.0
    target = f * path_length(pts)
    run = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        if run + seg >= target and seg > 0:
            g = (target - run) / seg
            return a[0] + (b[0] - a[0]) * g, a[1] + (b[1] - a[1]) * g, t0 <= t <= t0 + d
        run += seg
    return pts[-1][0], pts[-1][1], False


def positions(plant):
    now = plant.env.now
    out = []
    for p in plant.crew.persons.values():
        if not p.on_site:
            continue
        if p.walk is not None:
            x, y, moving = at(p.walk, now)
        else:
            x, y = plant.crew.xy(p.place)
            moving = False
        out.append(("P", p.id, x, y, moving, p.place if not moving else "walk", p.act, 0.0))
    for r in plant.fleet.robots:
        pl = plant.layout.amr.places[r.place]
        if r.walk is not None:
            x, y, moving = at(r.walk, now)
            face = heading(r.walk, now) if moving else (pl.face or 0.0)
        else:
            x, y, moving, face = pl.x, pl.y, False, pl.face or 0.0
        out.append(("R", r.id, x, y, moving, r.place if not moving else "drive", r.state, face))
    return out


def run(hours: float = 3.0, seed: int = 4, start_h: float = 0.0, step: float = 1.0, quiet: bool = False):
    model = load_model()
    plant = PlantSim(model, PlantClock(datetime(2026, 1, 5, 6, 0, tzinfo=UTC)), LocalDispatcher(model, seed), seed=seed)
    if start_h:
        plant.env.run(until=start_h * 3600)
    hits = Counter()
    where = {}
    t = plant.env.now
    end = t + hours * 3600
    pairs_seen = set()
    while t < end:
        t += step
        plant.env.run(until=t)
        pos = positions(plant)
        for i in range(len(pos)):
            a = pos[i]
            for b in pos[i + 1:]:
                kinds = tuple(sorted((a[0], b[0])))
                lim = LIMIT[kinds]
                if abs(a[2] - b[2]) > lim or abs(a[3] - b[3]) > lim:
                    continue
                d = math.hypot(a[2] - b[2], a[3] - b[3])
                if kinds != ("P", "P"):                    # robots are rectangles along their way
                    fa = a[7] if a[0] == "R" else 0.0
                    fb = b[7] if b[0] == "R" else 0.0
                    ma = (0.0, 1.0, a[2], a[3], a[2], a[3], fa)
                    mb = (0.0, 1.0, b[2], b[3], b[2], b[3], fb)
                    if not touch(a[0], ma, b[0], mb):
                        continue
                    d = 0.0 if d < lim else d
                if d < lim:
                    if not a[4] and not b[4] and a[5] == b[5]:
                        kind = "same place"
                    else:
                        kind = "moving" if a[4] and b[4] else "one moving" if a[4] or b[4] else "standing"
                    key = (kinds, kind, a[5] if not a[4] else b[5] if not b[4] else "walk")
                    pair = (a[0], a[1], b[0], b[1], int(t // 10))
                    if pair in pairs_seen:
                        continue
                    pairs_seen.add(pair)
                    hits[key] += 1
                    where.setdefault(key, (round(t), round(a[2]), round(a[3]), a[6], b[6], round(d, 1)))
    if not quiet:
        print(f"{hours} h from {start_h} h, seed {seed}: traffic waits {plant.traffic.waits} ({plant.traffic.waited:.0f} s), forced {plant.traffic.forced}, went after waiting {plant.traffic.forced_go} (robots {plant.traffic.forced_robots})")
        for key, n in hits.most_common(40):
            print(n, key, where[key])
    return hits, where, plant


if __name__ == "__main__":
    h = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    start = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    run(h, seed, start)
