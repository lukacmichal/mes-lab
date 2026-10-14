"""Traffic on the shop floor: nobody walks through anybody.

Every walk of a person and every drive of a robot is planned before it starts: a polyline at
constant speed (see layout_crew.Walkways). Before a mover sets off, its plan is checked against
everybody else's: where they walk, where they will stand when they get there, where they stand
now. If the two would come closer than their bodies allow, the mover waits where it is and tries
a little later, the way people let a colleague pass at a door or a robot waits at its stop until
the aisle is free. The layout keeps the flows apart (people keep to the right, robots have their
own lane, nobody stands on a walkway), so most plans go at once; the waiting is for the crossings.

Bodies: a person is a circle (two people keep their middles CLEAR_PP apart), a robot a rectangle of
32 x 21 that lies along its way (on a stop: the way the stop faces). A robot and anybody else must
not touch: the test is exact for straight motions along the axes, which is how robots drive.

A robot standing at a stop is in the way until it leaves, so a person whose way it blocks waits and
asks again (`plan` answers None). A person who stands still somewhere stands off the walkways by
design; a walk that would pass one anyway goes (and is counted), because waiting would not help.
A way that stays busy for longer than the longest wait is asked for again later as well.

Positions are plan units, times simulation seconds. Only the plan is checked, so this costs a
few calculations per walk, not a step-by-step simulation of bodies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

PERSON, ROBOT = "P", "R"
CLEAR_PP = 9.0                     # two people: their middles at least this far apart
PERSON_HALF = 5.5                  # a person next to a robot: half the shoulders (a square around the middle)
ROBOT_HALF = (16.0, 10.5)          # half the length and half the width of a robot (32 x 21)
MARGIN = 1.0
WAITS = (0.0, 0.6, 1.2, 2.0, 3.0, 4.0, 5.5, 7.0, 9.0, 12.0, 15.0, 19.0, 24.0, 30.0)
REACH = 2 * ROBOT_HALF[0] + MARGIN  # nothing farther than this apart can touch


def keep_seconds(speed: float | None) -> float:
    """How far back the live view needs the walks, drives and rides that already ended: the page asks once
    a wall second and shows the plant a little more than a second late (floor-core.js), so that it knows
    every move before it draws it."""
    return max(30.0, 4.0 * (speed or 10.0))


@dataclass
class Plan:
    """Where somebody is over time: at `pts[0]` until `t0`, along the legs, at `pts[-1]` from `t1` on.
    `faces`: for a robot, how it lies when standing at the start and at the end (degrees, 0 = along x)."""
    kind: str
    pts: list[tuple[float, float]]
    t0: float
    t1: float
    legs: list[tuple[float, float, float, float, float, float]] = field(default_factory=list)  # (ta, tb, ax, ay, bx, by)
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    faces: tuple[float, float] = (0.0, 0.0)
    _parts: list | None = None

    def parts(self) -> list[tuple]:
        """All of the plan as pieces with their bounding boxes, from its start into the far future:
        (ta, tb, ax, ay, bx, by, face, x0, y0, x1, y1). Cached."""
        if self._parts is None:
            out = []
            for m in self.pieces(-math.inf, math.inf):
                out.append((*m, min(m[2], m[4]), min(m[3], m[5]), max(m[2], m[4]), max(m[3], m[5])))
            self._parts = out
        return self._parts

    @staticmethod
    def make(kind: str, pts: list[tuple[float, float]], t0: float, speed: float,
             faces: tuple[float | None, float | None] = (None, None)) -> Plan:
        legs = []
        t = t0
        for a, b in zip(pts, pts[1:]):
            d = math.dist(a, b)
            if d < 1e-9:
                continue
            dt = d / speed
            legs.append((t, t + dt, a[0], a[1], b[0], b[1]))
            t += dt
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        f0, f1 = faces
        if f0 is None:
            f0 = _heading(legs[0]) if legs else 0.0
        if f1 is None:
            f1 = _heading(legs[-1]) if legs else f0
        return Plan(kind, list(pts), t0, t, legs, (min(xs), min(ys), max(xs), max(ys)), (f0, f1))

    @staticmethod
    def still(kind: str, x: float, y: float, face: float = 0.0) -> Plan:
        return Plan(kind, [(x, y)], -math.inf, -math.inf, [], (x, y, x, y), (face, face))

    def shifted(self, dt: float) -> Plan:
        return Plan(self.kind, self.pts, self.t0 + dt, self.t1 + dt,
                    [(ta + dt, tb + dt, ax, ay, bx, by) for ta, tb, ax, ay, bx, by in self.legs], self.box, self.faces)

    @property
    def end(self) -> tuple[float, float]:
        return self.pts[-1]

    @property
    def start(self) -> tuple[float, float]:
        return self.pts[0]

    def pieces(self, t_from: float, t_to: float):
        """The plan between two times as straight motions (ta, tb, ax, ay, bx, by, face), standing included."""
        if t_to <= t_from:
            return
        if t_from < self.t0:
            x, y = self.start
            yield (t_from, min(t_to, self.t0), x, y, x, y, self.faces[0])
        for leg in self.legs:
            if leg[1] <= t_from or leg[0] >= t_to:
                continue
            yield (*leg, _heading(leg))
        if t_to > self.t1:
            x, y = self.end
            yield (max(t_from, self.t1), t_to, x, y, x, y, self.faces[1])


def _heading(leg) -> float:
    return 0.0 if abs(leg[4] - leg[2]) >= abs(leg[5] - leg[3]) else 90.0


def _half(kind: str, face: float) -> tuple[float, float]:
    if kind == PERSON:
        return PERSON_HALF, PERSON_HALF
    along_x = abs(math.cos(math.radians(face))) > 0.7
    return ROBOT_HALF if along_x else (ROBOT_HALF[1], ROBOT_HALF[0])


def _state(m, t):
    span = m[1] - m[0]
    if span <= 1e-12 or not math.isfinite(span):
        return m[2], m[3], 0.0, 0.0
    vx, vy = (m[4] - m[2]) / span, (m[5] - m[3]) / span
    return m[2] + vx * (t - m[0]), m[3] + vy * (t - m[0]), vx, vy


def _closest(p: tuple, q: tuple, lo: float = -math.inf, hi: float = math.inf) -> float:
    """Smallest distance of two straight motions over the time they share (inf when they do not)."""
    ta, tb = max(p[0], q[0], lo), min(p[1], q[1], hi)
    if tb < ta:
        return math.inf
    px, py, pvx, pvy = _state(p, ta)
    qx, qy, qvx, qvy = _state(q, ta)
    dx, dy = px - qx, py - qy
    vx, vy = pvx - qvx, pvy - qvy
    vv = vx * vx + vy * vy
    s = 0.0
    if vv > 1e-12:
        s = max(0.0, min(tb - ta, -(dx * vx + dy * vy) / vv))
    return math.hypot(dx + vx * s, dy + vy * s)


def _boxes_touch(p: tuple, q: tuple, hx: float, hy: float, lo: float = -math.inf, hi: float = math.inf) -> bool:
    """Do two straight motions come closer than hx along x and hy along y at the same moment?"""
    ta, tb = max(p[0], q[0], lo), min(p[1], q[1], hi)
    if tb < ta:
        return False
    px, py, pvx, pvy = _state(p, ta)
    qx, qy, qvx, qvy = _state(q, ta)
    span = tb - ta

    def window(d: float, v: float, h: float):
        if abs(v) < 1e-12:
            return (0.0, span) if abs(d) < h else None
        t1, t2 = (-h - d) / v, (h - d) / v
        if t1 > t2:
            t1, t2 = t2, t1
        lo, hi = max(t1, 0.0), min(t2, span)
        return (lo, hi) if lo < hi or (lo == hi == 0.0 and span == 0.0) else None

    wx = window(px - qx, pvx - qvx, hx)
    if wx is None:
        return False
    wy = window(py - qy, pvy - qvy, hy)
    if wy is None:
        return False
    return max(wx[0], wy[0]) <= min(wx[1], wy[1])


def touch(kind_a: str, a: tuple, kind_b: str, b: tuple, lo: float = -math.inf, hi: float = math.inf) -> bool:
    """Do two bodies on these motions (with their faces, see Plan.pieces) touch between lo and hi?"""
    if kind_a == PERSON and kind_b == PERSON:
        return _closest(a, b, lo, hi) < CLEAR_PP
    ha, hb = _half(kind_a, a[6]), _half(kind_b, b[6])
    return _boxes_touch(a, b, ha[0] + hb[0] + MARGIN, ha[1] + hb[1] + MARGIN, lo, hi)


class Traffic:
    def __init__(self, env):
        self.env = env
        self.plans: dict[object, Plan] = {}
        self.waited = 0.0              # seconds all movers waited for each other
        self.waits = 0                 # how many walks had to wait
        self.forced = 0                # walks that went although somebody stood in the way
        self.forced_go = 0             # walks that went after waiting long, although the way was not free
        self.forced_robots = 0

    # ------------------------------------------------------------------ who is where
    def stand(self, who, kind: str, x: float, y: float, face: float = 0.0) -> None:
        self.plans[who] = Plan.still(kind, x, y, face)

    def gone(self, who) -> None:
        self.plans.pop(who, None)

    @staticmethod
    def _near(a: Plan, b: Plan, pad: float) -> bool:
        return not (a.box[2] + pad < b.box[0] or b.box[2] + pad < a.box[0]
                    or a.box[3] + pad < b.box[1] or b.box[3] + pad < a.box[1])

    def _meets(self, who, mine: Plan, now: float) -> tuple[bool, bool, bool]:
        """(meets a mover or a robot, blocked by a standing robot, passes a standing person)."""
        moving = blocked = person = False
        walk = [m for m in mine.parts() if m[1] > now and (m[0] <= mine.t1 + 0.01)]
        for other, theirs in self.plans.items():
            if other == who:
                continue
            pad = CLEAR_PP if mine.kind == PERSON and theirs.kind == PERSON else REACH
            if not self._near(mine, theirs, pad):
                continue
            if not math.isfinite(theirs.t1) or theirs.t1 < now:
                # standing there (until they plan a walk): only my own walk and where I stop matter
                x, y = theirs.end
                still = (now, math.inf, x, y, x, y, theirs.faces[1])
                for leg in walk:
                    if leg[7] - pad > x or leg[9] + pad < x or leg[8] - pad > y or leg[10] + pad < y:
                        continue
                    if leg[0] > mine.t1 + 0.01:
                        continue
                    if touch(mine.kind, leg, theirs.kind, still, now, mine.t1 + 0.01):
                        if theirs.kind == ROBOT:
                            blocked = True
                        else:
                            person = True
                        break
                continue
            t_end = max(mine.t1, theirs.t1) + 1.0
            hit = False
            for leg in walk:
                if leg[0] >= t_end:
                    continue
                for o in theirs.parts():
                    if o[1] <= now or o[0] >= t_end or o[1] < leg[0] or o[0] > leg[1]:
                        continue
                    if o[7] - pad > leg[9] or o[9] + pad < leg[7] or o[8] - pad > leg[10] or o[10] + pad < leg[8]:
                        continue
                    if touch(mine.kind, leg, theirs.kind, o, now, t_end):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                moving = True
        return moving, blocked, person

    def plan(self, who, kind: str, pts: list[tuple[float, float]], speed: float,
             max_wait: float | None = None, faces: tuple[float | None, float | None] = (None, None)
             ) -> tuple[float | None, Plan]:
        """The earliest start (seconds from now) at which this walk meets nobody, and its plan.
        None: a robot stands in the way or the way is busy for long; ask again in a while (the plan is
        the walk now, for `go`)."""
        now = self.env.now
        base = Plan.make(kind, pts, now, speed, faces)
        limit = WAITS if max_wait is None else tuple(w for w in WAITS if w <= max_wait)
        for wait in limit:
            mine = base.shifted(wait) if wait else base
            moving, blocked, person = self._meets(who, mine, now)
            if blocked:
                break
            if not moving:
                if person:
                    self.forced += 1
                self.commit(who, mine)
                if wait:
                    self.waits += 1
                    self.waited += wait
                return wait, mine
        # a robot stands in the way, or the way is busy for longer than the longest wait: ask again later
        return None, base

    def commit(self, who, plan: Plan) -> None:
        self.plans[who] = plan

    def go(self, who, plan: Plan) -> None:
        """Waited long enough: go now anyway (counted, the page may show two of them brush past)."""
        self.forced_go += 1
        if plan.kind == ROBOT:
            self.forced_robots += 1
        self.commit(who, plan)

    def standing_people(self, box: tuple[float, float, float, float], but) -> list[tuple[float, float]]:
        """Where people stand still inside a rectangle (x0, y0, x1, y1), apart from `but`."""
        now = self.env.now
        out = []
        for who, p in self.plans.items():
            if who == but or p.kind != PERSON:
                continue
            if p.legs and p.t0 <= now <= p.t1:
                continue                               # walking
            x, y = p.end if (not p.legs or p.t1 < now) else p.start
            if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                out.append((x, y))
        return out

    def prune(self) -> None:
        """Walks that ended long ago are only standing now (keeps the checks short)."""
        now = self.env.now
        for who, p in list(self.plans.items()):
            if p.legs and p.t1 < now - 5:
                self.plans[who] = Plan.still(p.kind, *p.end, p.faces[1])
