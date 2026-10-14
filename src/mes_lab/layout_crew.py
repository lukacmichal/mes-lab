"""The people side of the floor plan: where they sit, where they walk, the robots and the dock.

* the staff building (entrance with the ESD gates and the lockers, toilets, break room, foreman's
  office, IT room, maintenance workshop) and the team desks with their computers. A desk is set up
  the way people use it: the chair pushed in, the monitor at the far side with its screen to the
  person, the keyboard in front of them and the mouse on their right;
* the aisles. Every aisle between two rows of machines has a walkway with two lanes (people keep
  to the right, so two of them pass side by side) next to the machines they serve, and beyond it a
  lane for the robots: people and robots do not share a lane anywhere, they only cross. Each area
  has a trunk walkway and a robot trunk on its left, and one walkway and one robot lane run along
  the bottom of the plant. A walk between two places is the path through them, so the simulation
  knows how long a technician needs to a machine. Rooms (the staff building, the repair centre) are
  entered through their door; inside, a walk goes around the furniture. Nobody stands on a lane:
  the places where people stand (machines, desks, waiting places, the place next to a seat where
  the next person waits for the hand-over) are beside them;
* the seats of the operators (see config/crew.yaml) and the machine each one looks after;
* the robots (AMR): every cell of a line drops its rejected pieces into a red NOK tote at its exit,
  in the gap before the next cell, and a robot docks in that gap (off the walkway) to take them and to
  bring pieces back. At the loader of a final assembly line stands the rack with the bins of parts
  (housings, connectors, screws) and at the scan station of packing the stack of empty boxes; robots
  bring full bins and stacks from the goods-in warehouse and take the empty bins back. And their
  depot with the chargers;
* the crossings: where a walkway or a robot lane passes a conveyor, the conveyor goes over it on
  a bridge (at the dock: a lift gate), so nobody walks through a belt;
* the dock: three bays below the finished goods warehouse and the belts the trucks follow.

Everything is computed from the plant model and the lines placed by layout.py.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from mes_lab.model.schema import AreaKind, PlantModel

SPOT = 8          # distance of a standing person from the machine
CORR_OFF = 64     # the walkway of an aisle runs this far below the spine of the row above it ...
KEEP = 6          # ... with its two lanes this far either side of its middle (people keep to the right)
AMR_OFF = 88      # the robot lane of an aisle: beyond the walkway, clear of the people at both rows
RESERVE_DY = 46   # waiting places (relief people) below the spine of their line, clear of the walkway
DESK_W, DESK_D = 40, 22       # team desk at a line
OFFICE_D = 24                 # depth of an office desk (about 65 cm)
SIT = 4                       # a seated person is this far from the edge of the desk: the chair is pushed in
AMR_L, AMR_W = 32, 21         # a robot from above (about 0.9 x 0.6 m)
NOK_W, NOK_H = 6, 9           # the red tote for rejected pieces, upright at the exit of a cell
GAP_DOCK_DY = {"SMT": 32.0}   # a robot docked in the gap of a line: its middle this far below the spine
GAP_DOCK_DY_DEFAULT = 23.0    # (SMT lines have their return belt under the machines, the others not)
CHARGERS_PITCH = 36
HANDOVER_DX = 14              # the next person waits this far beside a seat until it is handed over
PERSON_GAP = 9.5              # inside a room a walk keeps this far from the middle of somebody standing

STAFF_H = 150
BAY_PITCH = 40
TRAILER_W, TRAILER_L, CAB_L = 36, 110, 30
YARD_LEN = 260    # how far below the parking place a truck starts and ends

# the entrance room: ESD gates for the way in, a free turnstile for the way out, all in one railing
GATES_X = (22, 52)
EXIT_X = 80
GATE_Y0, GATE_Y1 = 62, 80     # the posts of the gates, across the room
GATE_HALF = 11                # half the width of a gate (posts included); the passage between the posts is 16

BELTS = ("supply", "sm", "feed", "pack", "wh", "ret", "wash", "ship")

# the toilets: a room for the men ("m") and one for the women ("w"), each with its cabins
WC_ROOMS = {"wcm": "m", "wcw": "w"}
CABINS = 2
CABIN_W, CABIN_D, CABIN_DOOR = 21, 36, 13
CABIN_PAD = 1.4               # how close a person passes the thin walls of a cabin (through its door)

# the shift meeting stands in a row in front of the andon board of the production office
MEETING_X = (16, 31, 46, 61, 76, 91, 106)

# the corner of the logistics workers east of the staff building: where they wait for the next truck,
# and the table they sit down at when there is nothing to do for a while
LOUNGE_SEATS = 6


def path_length(pts: list[tuple[float, float]]) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _angle(dx: float, dy: float) -> int:
    return round(math.degrees(math.atan2(dy, dx)))


@dataclass
class Place:
    key: str
    x: float
    y: float
    corr: str                       # corridor it connects to
    ax: float                       # where along that corridor
    stub: list[tuple[float, float]] = field(default_factory=list)   # via points from the place to the corridor
    room: str | None = None         # a place inside a room walks to the door of the room first
    look: tuple[float, float] | None = None   # a person standing here faces this point (the monitor, the table)
    pc: bool = False                # a person standing here works at a computer
    face: float | None = None       # a robot stands here in this direction (degrees)


class Room:
    """A room: furniture is in the way, the door is the only way out.
    Routes inside are found on a coarse grid around the furniture."""

    CELL = 2.0
    PAD = 3.6               # how close the middle of a person comes to furniture (shoulders brush past, no more)

    def __init__(self, rid: str, x: float, y: float, w: float, h: float):
        self.id, self.x, self.y, self.w, self.h = rid, x, y, w, h
        self.exits: dict[str, tuple[float, float]] = {}
        self.obstacles: list[tuple[float, float, float, float, float]] = []
        self._blocked: set[tuple[int, int]] | None = None
        self._flat: bytearray | None = None
        self._cache: dict[tuple, list[tuple[float, float]]] = {}

    def solid(self, x: float, y: float, w: float, h: float, pad: float | None = None) -> None:
        """Furniture in the way; `pad` is how close a person passes (narrow passages like a gate: less)."""
        self.obstacles.append((x, y, w, h, self.PAD if pad is None else pad))
        self._blocked = None
        self._flat = None
        self._cache.clear()

    def _cell(self, p: tuple[float, float]) -> tuple[int, int]:
        return (min(max(int((p[0] - self.x) // self.CELL), 0), int(self.w // self.CELL) - 1),
                min(max(int((p[1] - self.y) // self.CELL), 0), int(self.h // self.CELL) - 1))

    def _centre(self, c: tuple[int, int]) -> tuple[float, float]:
        return self.x + (c[0] + 0.5) * self.CELL, self.y + (c[1] + 0.5) * self.CELL

    def blocked(self) -> set[tuple[int, int]]:
        if self._blocked is None:
            out: set[tuple[int, int]] = set()
            nx, ny = int(self.w // self.CELL), int(self.h // self.CELL)
            for ox, oy, ow, oh, pad in self.obstacles:
                i0 = max(0, int((ox - pad - self.x) // self.CELL))
                i1 = min(nx - 1, int((ox + ow + pad - self.x) // self.CELL))
                j0 = max(0, int((oy - pad - self.y) // self.CELL))
                j1 = min(ny - 1, int((oy + oh + pad - self.y) // self.CELL))
                for i in range(i0, i1 + 1):
                    for j in range(j0, j1 + 1):
                        cx, cy = self._centre((i, j))
                        if ox - pad < cx < ox + ow + pad and oy - pad < cy < oy + oh + pad:
                            out.add((i, j))
            self._blocked = out
        return self._blocked

    def _grid(self) -> bytearray:
        """The blocked cells as a flat array (1 = furniture), row by row."""
        if self._flat is None:
            nx, ny = int(self.w // self.CELL), int(self.h // self.CELL)
            flat = bytearray(nx * ny)
            for i, j in self.blocked():
                flat[j * nx + i] = 1
            self._flat = flat
        return self._flat

    def route(self, a: tuple[float, float], b: tuple[float, float],
              people: list[tuple[float, float]] | None = None) -> list[tuple[float, float]]:
        """A way from a to b around the furniture (and around the `people` standing in the room)."""
        key = (round(a[0], 1), round(a[1], 1), round(b[0], 1), round(b[1], 1))
        if not people and key in self._cache:
            return self._cache[key]
        cell = self.CELL
        nx, ny = int(self.w // cell), int(self.h // cell)
        grid = bytearray(self._grid())
        for px, py in people or ():
            r = PERSON_GAP
            for i in range(max(0, int((px - r - self.x) // cell)), min(nx - 1, int((px + r - self.x) // cell)) + 1):
                cx = self.x + (i + 0.5) * cell
                for j in range(max(0, int((py - r - self.y) // cell)), min(ny - 1, int((py + r - self.y) // cell)) + 1):
                    cy = self.y + (j + 0.5) * cell
                    if (cx - px) ** 2 + (cy - py) ** 2 < r * r:
                        grid[j * nx + i] = 1
        start, goal = self._cell(a), self._cell(b)
        for c in (start, goal):                                        # the ends stand where they stand
            for di in range(-2, 3):
                for dj in range(-2, 3):
                    i, j = c[0] + di, c[1] + dj
                    if 0 <= i < nx and 0 <= j < ny:
                        grid[j * nx + i] = 0
        x0, y0 = self.x, self.y

        def seen(p: tuple[float, float], q: tuple[float, float]) -> bool:      # a straight line free of furniture
            n = max(1, int(math.dist(p, q) / 1.0))
            dx, dy = (q[0] - p[0]) / n, (q[1] - p[1]) / n
            for t in range(n + 1):
                i = int((p[0] + dx * t - x0) // cell)
                j = int((p[1] + dy * t - y0) // cell)
                i = 0 if i < 0 else nx - 1 if i >= nx else i
                j = 0 if j < 0 else ny - 1 if j >= ny else j
                if grid[j * nx + i]:
                    return False
            return True

        if seen(a, b):
            path = [a, b]
        else:
            s0, g = start[1] * nx + start[0], goal[1] * nx + goal[0]
            gi, gj = goal
            best = {s0: 0.0}
            came: dict[int, int] = {}
            heap = [(0.0, s0)]
            found = False
            steps = ((-1, -1, 1.414), (0, -1, 1.0), (1, -1, 1.414), (-1, 0, 1.0), (1, 0, 1.0),
                     (-1, 1, 1.414), (0, 1, 1.0), (1, 1, 1.414))
            while heap:
                f, cur = heapq.heappop(heap)
                if cur == g:
                    found = True
                    break
                ci, cj = cur % nx, cur // nx
                base = best[cur]
                for di, dj, w in steps:
                    ni, nj = ci + di, cj + dj
                    if ni < 0 or nj < 0 or ni >= nx or nj >= ny:
                        continue
                    nxt = nj * nx + ni
                    if grid[nxt]:
                        continue
                    if di and dj and (grid[cj * nx + ni] or grid[nj * nx + ci]):
                        continue                                    # no squeezing between two corners
                    cost = base + w
                    if cost < best.get(nxt, 1e9):
                        best[nxt] = cost
                        came[nxt] = cur
                        ddx, ddy = abs(ni - gi), abs(nj - gj)
                        h = (ddx + ddy) + (1.414 - 2) * min(ddx, ddy)          # octile distance
                        heapq.heappush(heap, (cost + h, nxt))
            if not found:
                path = [a, b]
            else:
                cells = [g]
                while cells[-1] != s0:
                    cells.append(came[cells[-1]])
                cells.reverse()
                pts = [a] + [(x0 + (c % nx + 0.5) * cell, y0 + (c // nx + 0.5) * cell) for c in cells[1:-1]] + [b]
                path = [pts[0]]                                         # pull the string tight
                i = 0
                while i < len(pts) - 1:
                    j = len(pts) - 1
                    while j > i + 1 and not seen(pts[i], pts[j]):
                        j -= 1
                    path.append(pts[j])
                    i = j
        if not people:
            self._cache[key] = path
        return path


class Walkways:
    """Corridors (horizontal) hang on trunks (vertical) that meet the bottom corridor.
    The people have one network, the robots another (their lanes run beyond the walkways).

    `keep`: on the corridors and trunks of this network people keep to the right, so two who meet
    walk past each other side by side (KEEP from the middle each), never through each other."""

    def __init__(self, y_bottom: float, speed: float, keep: float = 0.0):
        self.y_bottom = y_bottom
        self.speed = speed
        self.keep = keep
        self.corridors: dict[str, dict] = {}
        self.trunks: dict[str, dict] = {}
        self.places: dict[str, Place] = {}
        self.rooms: dict[str, Room] = {}
        self.direct: set[tuple[str, str]] = set()          # straight through (an ESD gate, a turnstile)
        self._cache: dict[tuple[str, str], list[tuple[float, float]]] = {}

    def trunk(self, area: str, x: float, y0: float, y1: float | None = None) -> None:
        self.trunks[area] = {"x": x, "y0": y0, "y1": self.y_bottom if y1 is None else y1}

    def corridor(self, cid: str, y: float, x0: float, x1: float, area: str | None) -> None:
        self.corridors[cid] = {"y": y, "x0": x0, "x1": x1, "area": area}

    def room(self, rid: str, x: float, y: float, w: float, h: float, door: tuple[float, float]) -> Room:
        room = Room(rid, x, y, w, h)
        room.exits["door"] = door
        self.rooms[rid] = room
        return room

    def place(self, key: str, x: float, y: float, corr: str, ax: float | None = None,
              stub: list[tuple[float, float]] | None = None, room: str | None = None,
              look: tuple[float, float] | None = None, pc: bool = False, face: float | None = None) -> Place:
        c = self.corridors[corr]
        if room is not None:
            ax = self.rooms[room].exits["door"][0]
        ax = x if ax is None else ax
        p = Place(key, x, y, corr, min(max(ax, c["x0"]), c["x1"]), list(stub or []), room, look, pc, face)
        self.places[key] = p
        return p

    # ------------------------------------------------------------------ paths
    def path(self, a: str, b: str) -> list[tuple[float, float]]:
        key = (a, b)
        if key not in self._cache:
            pa, pb = self.places[a], self.places[b]
            if key in self.direct:
                self._cache[key] = [(pa.x, pa.y), (pb.x, pb.y)]
            else:
                self._cache[key] = self._keep_right(self._path(pa, pb)) if self.keep else self._path(pa, pb)
        return self._cache[key]

    def duration(self, a: str, b: str) -> float:
        return path_length(self.path(a, b)) / self.speed

    def rooms_of(self, a: str, b: str) -> set[str]:
        return {r for r in (self.places[a].room, self.places[b].room) if r is not None}

    def path_around(self, a: str, b: str, people: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Like path(), but the parts inside rooms go around the people who stand there (not cached)."""
        pa, pb = self.places[a], self.places[b]
        if (a, b) in self.direct:
            return [(pa.x, pa.y), (pb.x, pb.y)]
        pts = self._path(pa, pb, people)
        return self._keep_right(pts) if self.keep else pts

    def _leg(self, p: Place, people=None) -> list[tuple[float, float]]:
        """From a place to its corridor: inside a room through its door."""
        cy = self.corridors[p.corr]["y"]
        if p.room is not None:
            room = self.rooms[p.room]
            return room.route((p.x, p.y), room.exits["door"], people) + [(p.ax, cy)]
        return [(p.x, p.y), *p.stub, (p.ax, cy)]

    def outside_leg(self, p: Place) -> list[tuple[float, float]]:
        """The part of a place's leg that runs in the hall (for a place in a room: from its door)."""
        cy = self.corridors[p.corr]["y"]
        if p.room is not None:
            return [self.rooms[p.room].exits["door"], (p.ax, cy)]
        return [(p.x, p.y), *p.stub, (p.ax, cy)]

    def _trunk_of(self, c: dict) -> dict | None:
        """The trunk a corridor hangs on; a corridor that runs along the bottom line needs none."""
        if c["area"] is None or abs(c["y"] - self.y_bottom) < 0.5:
            return None
        return self.trunks.get(c["area"])

    def _path(self, a: Place, b: Place, people=None) -> list[tuple[float, float]]:
        if a.room is not None and a.room == b.room:
            return self.rooms[a.room].route((a.x, a.y), (b.x, b.y), people)
        ca, cb = self.corridors[a.corr], self.corridors[b.corr]
        pts = self._leg(a, people)
        tail = list(reversed(self._leg(b, people)))
        mid: list[tuple[float, float]] = []
        on_bottom = abs(ca["y"] - self.y_bottom) < 0.5 and abs(cb["y"] - self.y_bottom) < 0.5
        if a.corr != b.corr and not on_bottom:
            ta, tb = self._trunk_of(ca), self._trunk_of(cb)
            if ta is not None:
                mid.append((ta["x"], ca["y"]))
            if ta is not None and tb is not None and ca["area"] == cb["area"]:
                mid.append((ta["x"], cb["y"]))                     # same area: along the trunk only
            else:
                if ta is not None:
                    mid.append((ta["x"], self.y_bottom))
                if tb is not None:
                    mid.append((tb["x"], self.y_bottom))
                    mid.append((tb["x"], cb["y"]))
        out: list[tuple[float, float]] = []
        for p in pts + mid + tail:
            if not out or math.dist(out[-1], p) > 0.01:
                out.append(p)
        return _straighten(out)

    # ------------------------------------------------------------------ keeping to the right
    def _on_corridor(self, a, b) -> bool:
        lo, hi = min(a[0], b[0]), max(a[0], b[0])
        return any(abs(c["y"] - a[1]) < 0.01 and lo >= c["x0"] - 0.5 and hi <= c["x1"] + 0.5
                   for c in self.corridors.values()) or abs(a[1] - self.y_bottom) < 0.01

    def _on_trunk(self, a, b) -> bool:
        lo, hi = min(a[1], b[1]), max(a[1], b[1])
        return any(abs(t["x"] - a[0]) < 0.01 and lo >= t["y0"] - 0.5 and hi <= t["y1"] + 0.5
                   for t in self.trunks.values())

    def _keep_right(self, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Shift the parts of a path that run along a corridor or a trunk to the right-hand lane."""
        if len(pts) < 3:
            return pts
        segs = list(zip(pts, pts[1:]))
        offs = []
        for a, b in segs:
            dx, dy = b[0] - a[0], b[1] - a[1]
            off = (0.0, 0.0)
            if abs(dy) < 1e-6 and abs(dx) > 1e-6 and self._on_corridor(a, b):
                off = (0.0, math.copysign(self.keep, dx))           # walking east, the right is south
            elif abs(dx) < 1e-6 and abs(dy) > 1e-6 and self._on_trunk(a, b):
                off = (-math.copysign(self.keep, dy), 0.0)          # walking south, the right is west
            offs.append(off)
        if not any(offs):
            return pts
        out = [pts[0]]
        for i in range(len(segs) - 1):
            (a, b), (c, d) = segs[i], segs[i + 1]
            o1, o2 = offs[i], offs[i + 1]
            if o1 == (0.0, 0.0) and o2 == (0.0, 0.0):
                out.append(b)
                continue
            h1, h2 = abs(b[1] - a[1]) < 1e-6, abs(d[1] - c[1]) < 1e-6
            v1, v2 = abs(b[0] - a[0]) < 1e-6, abs(d[0] - c[0]) < 1e-6
            if h1 and v2:
                out.append((c[0] + o2[0], a[1] + o1[1]))
            elif v1 and h2:
                out.append((a[0] + o1[0], c[1] + o2[1]))
            else:
                p1 = (b[0] + o1[0], b[1] + o1[1])
                p2 = (b[0] + o2[0], b[1] + o2[1])
                out.append(p1)
                if math.dist(p1, p2) > 0.01:
                    out.append(p2)
        out.append(pts[-1])
        clean: list[tuple[float, float]] = []
        for p in out:
            if not clean or math.dist(clean[-1], p) > 0.01:
                clean.append((round(p[0], 2), round(p[1], 2)))
        return _straighten(clean)

    def segments(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """Every straight piece of this network in the hall: corridors, trunks and the legs of the places."""
        out = [((c["x0"], c["y"]), (c["x1"], c["y"])) for c in self.corridors.values()]
        out += [((t["x"], t["y0"]), (t["x"], t["y1"])) for t in self.trunks.values()]
        for p in self.places.values():
            leg = self.outside_leg(p)
            out += [(a, b) for a, b in zip(leg, leg[1:]) if math.dist(a, b) > 0.01]
        return out


def _straighten(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop the middle point of three in a row on one straight line (and a walk back and forth on it)."""
    out: list[tuple[float, float]] = []
    for p in pts:
        while len(out) >= 2:
            a, b = out[-2], out[-1]
            cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
            if abs(cross) > 1e-6:
                break
            dot = (b[0] - a[0]) * (p[0] - b[0]) + (b[1] - a[1]) * (p[1] - b[1])
            if dot < 0:                                            # it turns back: keep the turning point
                break
            out.pop()
        out.append(p)
    return out


def _desk_kit(x: float, y: float, w: float, d: float, facing: str) -> dict:
    """What stands on a desk, seen from above, for a person who sits at its north side and looks
    south (`facing` "S") or sits at its south side and looks north ("N"). The monitor stands at the
    far side with its screen to the person, the keyboard in front of them, the mouse on their right."""
    cx = x + w / 2
    if facing == "S":
        near, sgn, right = y, 1, -1           # looking south, the right hand is in the west
    else:
        near, sgn, right = y + d, -1, 1
    return {"chair": (cx, near - sgn * SIT),
            "monitor": (cx, near + sgn * (d - 6)), "screen": _angle(0, -sgn),     # the screen faces the person
            "kb": (cx, near + sgn * 4), "mouse": (cx + right * 9, near + sgn * 4.5),
            "laptop": (cx - right * 13.5, near + sgn * 7),
            "look": (cx, near + sgn * (d - 6)), "sit": _angle(0, sgn)}


def build(layout, ctx: dict) -> None:
    """Add the people side to a Layout (called from Layout._build)."""
    model: PlantModel = layout.model
    spine: list[float] = ctx["spine"]
    rows = len(spine)
    smt, fa, pack = ctx["smt"], ctx["fa"], ctx["pack"]
    fgw, rpr, sm, gi = ctx["fgw"], ctx["rpr"], ctx["sm"], ctx["gi"]
    gi_right, sm_right = gi["x"] + gi["w"], sm["x"] + sm["w"]
    y_b = spine[-1] + CORR_OFF                # walkway along the bottom of the plant (the bottom aisle's)
    y_rb = spine[-1] + AMR_OFF                # robot lane along the bottom
    staff_y = ctx["y_rooms"]
    park_y = fgw["y"] + fgw["h"] + 8          # rear of a parked truck (just below the warehouse)
    walk = Walkways(y_b, model.crew.walk_speed, keep=KEEP)
    layout.walk = walk
    amr = Walkways(y_rb, model.amr.speed)
    layout.amr = amr

    smt_end = max((layout.lines[l.code]["x2"] for l in smt), default=0)
    x_fa0 = min((layout.lines[l.code]["x1"] for l in fa), default=0)
    x_col = ctx["x_col"]
    # the left side of an area: robot trunk, team desks (with the hand-over place and the waiting places
    # of the relief people), then the walkway trunk, then the machines
    zones = {"SMT": {"robot": gi_right + 26, "desk": gi_right + 60, "walk": gi_right + 138},
             "FA": {"robot": sm_right + 56, "desk": sm_right + 84, "walk": sm_right + 162}}
    walk_top = spine[0] - CORR_OFF

    def row_end(lines, row: int) -> float:
        ends = [layout.lines[l.code]["x2"] for l in lines if layout.lines[l.code]["row"] == row]
        return max(ends, default=0)

    # --- the walkways (people): a corridor above the first row and one in every aisle below a row
    lane = lambda k: walk_top if k < 0 else spine[k] + CORR_OFF                 # noqa: E731
    walk.trunk("SMT", zones["SMT"]["walk"], walk_top)
    for k in range(-1, rows):
        walk.corridor(f"SMT:{k}", lane(k), zones["SMT"]["desk"] - 10 - KEEP, smt_end + 20, "SMT")
    if fa:
        walk.trunk("FA", zones["FA"]["walk"], walk_top)
        for k in range(-1, rows):
            # a corridor serves the machines below it and the top lanes of the row under it
            end = max(row_end(fa, k), row_end(fa, k + 1)) + 20
            walk.corridor(f"FA:{k}", lane(k), zones["FA"]["desk"] - 10 - KEEP, end, "FA")
    if pack:
        walk.trunk("PACK", x_col - 14, walk_top)
        for line in pack:
            row = layout.lines[line.code]["row"]
            for k in (row - 1, row):
                walk.corridor(f"PACK:{k}", lane(k), x_col - 14, layout.lines[line.code]["x2"] + 20, "PACK")
    x_dockwait = fgw["x"] - 8
    walk.corridor("bottom", y_b, zones["SMT"]["walk"], x_dockwait, None)

    # --- the robot lanes: one in every aisle below a row, beyond the walkway; the trunks on the left
    # of SMT and final assembly; packing is reached along the final assembly lanes
    pack_rows = {layout.lines[l.code]["row"]: layout.lines[l.code]["x2"] for l in pack}
    amr.trunk("SMT", zones["SMT"]["robot"], spine[0] + AMR_OFF)
    for k in range(rows):
        amr.corridor(f"SMT:{k}", spine[k] + AMR_OFF, zones["SMT"]["robot"], row_end(smt, k) + 40, "SMT")
    if fa:
        amr.trunk("FA", zones["FA"]["robot"], spine[0] + AMR_OFF)
        for k in range(rows):
            end = max(row_end(fa, k) + 40, pack_rows.get(k, 0) + 40)
            amr.corridor(f"FA:{k}", spine[k] + AMR_OFF, sm_right + 18, end, "FA")
    amr.corridor("bottom", y_rb, gi["x"] + gi["w"] / 2, max(rpr["x"] + rpr["w"] + 20, zones["FA"]["robot"]), None)

    # --- the staff building
    sx0 = rpr["x"] + rpr["w"] + 30                      # right of the repair centre
    rooms = [("lockers", "Entrance, ESD gates, lockers", "Vstup, ESD brány, šatne", 124, 30),
             ("wcm", "WC men", "WC muži", 48, 24),
             ("wcw", "WC women", "WC ženy", 48, 24),
             ("break", "Break room", "Jedáleň a oddych", 336, 168),
             ("office", "Production office", "Kancelária výroby", 156, 132),
             ("it", "IT room", "IT miestnosť", 150, 75),
             ("workshop", "Maintenance workshop", "Dielňa údržby", 212, 106)]
    x = sx0
    layout.rooms = []
    room_at: dict[str, dict] = {}
    for rid, name, name_sk, w, door_dx in rooms:
        room = {"id": rid, "name": name, "name_sk": name_sk, "x": x, "y": staff_y, "w": w, "h": STAFF_H,
                "door": [x + door_dx, staff_y]}
        if rid == "lockers":
            room["entrance"] = [x + 104, staff_y + STAFF_H]          # from the street, on the south side
        if rid in WC_ROOMS:
            room["label_dy"] = 74                                     # the cabins stand along the south wall
            room["sign"] = WC_ROOMS[rid]
        layout.rooms.append(room)
        room_at[rid] = room
        walk.room(rid, x, staff_y, w, STAFF_H, (x + door_dx, staff_y + 4))
        x += w
    staff_end = x
    staff = {"id": "STAFF", "kind": "staff", "x": sx0, "y": staff_y, "w": x - sx0, "h": STAFF_H,
             "name": "Staff building", "name_sk": "Budova pre zamestnancov"}
    layout.buildings.append(staff)

    SOLID = {"table", "locker", "rack", "desk", "shelf", "sink", "vending", "coffee", "printer", "andon", "cabinet",
             "bench", "inrack", "outrack", "cage", "scrapbin", "railing"}
    layout.furniture = []

    def furniture(kind: str, rid: str, dx: float, dy: float, w: float, h: float, **extra) -> None:
        r = room_at[rid]
        layout.furniture.append({"kind": kind, "x": r["x"] + dx, "y": r["y"] + dy, "w": w, "h": h, "room": rid, **extra})
        if kind in SOLID:
            walk.rooms[rid].solid(r["x"] + dx, r["y"] + dy, w, h)

    def room_place(key: str, rid: str, dx: float, dy: float, look: tuple[float, float] | None = None, pc: bool = False) -> None:
        r = room_at[rid]
        walk.place(key, r["x"] + dx, r["y"] + dy, "bottom", room=rid,
                   look=None if look is None else (r["x"] + look[0], r["y"] + look[1]), pc=pc)

    def chair(rid: str, dx: float, dy: float, face: float) -> None:
        furniture("chair", rid, dx - 4.5, dy - 4.5, 9, 9, face=face)

    # the entrance room. A railing runs across it: on the street side the entrance and the queues, on
    # the production side the door to the plant and the lockers. In through an ESD gate (the test opens
    # it, one person at a time), out through a free turnstile; both are walked straight through
    room_place("door", "lockers", 104, STAFF_H - 10, look=(104, 0))
    lockers = walk.rooms["lockers"]
    lx, ly = room_at["lockers"]["x"], room_at["lockers"]["y"]
    lockers.solid(lx, ly + GATE_Y0, 124, GATE_Y1 - GATE_Y0)          # nobody routes across the railing
    for g, gx in enumerate(GATES_X[:model.crew.esd.gates]):
        layout.furniture.append({"kind": "esd", "x": lx + gx - GATE_HALF, "y": ly + GATE_Y0, "w": 2 * GATE_HALF,
                                 "h": GATE_Y1 - GATE_Y0, "room": "lockers", "gate": g})
        room_place(f"esd:{g}", "lockers", gx, GATE_Y1 + 10, look=(gx, GATE_Y0))     # where the gate tests a person
        room_place(f"esdx:{g}", "lockers", gx, GATE_Y0 - 8, look=(gx, 0))          # through, on the production side
        walk.direct.add((f"esd:{g}", f"esdx:{g}"))
        for i in range(5):                                                         # the queue in front of the gate
            room_place(f"esdq{g}:{i}", "lockers", gx, GATE_Y1 + 21 + 10 * i, look=(gx, GATE_Y0))
    layout.furniture.append({"kind": "turnstile", "x": lx + EXIT_X - GATE_HALF, "y": ly + GATE_Y0, "w": 2 * GATE_HALF,
                             "h": GATE_Y1 - GATE_Y0, "room": "lockers"})
    room_place("exitn", "lockers", EXIT_X, GATE_Y0 - 8, look=(EXIT_X, STAFF_H))
    room_place("exits", "lockers", EXIT_X, GATE_Y1 + 10, look=(104, STAFF_H))
    walk.direct.add(("exitn", "exits"))
    exitq = [(EXIT_X, GATE_Y0 - 18 - 10 * i) for i in range(4)] + [(EXIT_X - 11, 14), (EXIT_X - 22, 14)]
    for i, (qx, qy) in enumerate(exitq):                                         # the queue to the turnstile
        room_place(f"exitq:{i}", "lockers", qx, qy, look=(EXIT_X, STAFF_H))
    edges = sorted([0.0, 124.0] + [gx - GATE_HALF for gx in GATES_X] + [gx + GATE_HALF for gx in GATES_X]
                   + [EXIT_X - GATE_HALF, EXIT_X + GATE_HALF])
    for a, b in zip(edges[::2], edges[1::2]):                                     # the railing between the gates
        if b - a > 0.5:
            layout.furniture.append({"kind": "railing", "x": lx + a, "y": ly + (GATE_Y0 + GATE_Y1) / 2 - 1.2,
                                     "w": b - a, "h": 2.4, "room": "lockers"})
    for i in range(4):
        furniture("locker", "lockers", 106, 6 + i * 12, 14, 11)
        room_place(f"locker:{i}", "lockers", 96, 11.5 + i * 12, look=(110, 11.5 + i * 12))    # in front of the lockers: the ESD coat
    # toilets: one room for the men, one for the women, each with two cabins along the south wall and two
    # wash basins by the west wall. A cabin is three thin walls and a door: you walk in, the door closes
    # behind you, and from outside nobody sees you (the page draws the cabin roofed)
    for rid, sex in WC_ROOMS.items():
        r = room_at[rid]
        for i in range(CABINS):
            x0 = 2 + i * (CABIN_W + 2)
            cx = x0 + CABIN_W / 2
            y0 = STAFF_H - 2 - CABIN_D
            walls = [(x0, y0, cx - CABIN_DOOR / 2, y0), (cx + CABIN_DOOR / 2, y0, x0 + CABIN_W, y0),   # the front, the door between
                     (x0, y0, x0, y0 + CABIN_D), (x0 + CABIN_W, y0, x0 + CABIN_W, y0 + CABIN_D)]
            for ax, ay, bx, by in walls:
                walk.rooms[rid].solid(r["x"] + min(ax, bx) - 0.6, r["y"] + min(ay, by) - 0.6,
                                      abs(bx - ax) + 1.2, abs(by - ay) + 1.2, pad=CABIN_PAD)
            layout.furniture.append({"kind": "cabin", "x": r["x"] + x0, "y": r["y"] + y0, "w": CABIN_W, "h": CABIN_D,
                                     "room": rid, "sex": sex, "door": [r["x"] + cx, r["y"] + y0], "door_w": CABIN_DOOR,
                                     "walls": [[r["x"] + ax, r["y"] + ay, r["x"] + bx, r["y"] + by] for ax, ay, bx, by in walls]})
            room_place(f"wc{sex}:{i}", rid, cx, y0 + CABIN_D / 2 + 2, look=(cx, y0 + CABIN_D))
        furniture("sink", rid, 2, 30, 8, 40)
        for i in range(2):
            room_place(f"sink{sex}:{i}", rid, 16, 40 + 20 * i, look=(4, 40 + 20 * i))
    # break room: tables with chairs, a coffee corner
    chairs = []
    for t in range(3):
        for r, ty in enumerate((40, 96)):
            tx = 14 + t * 86
            furniture("table", "break", tx, ty, 56, 26)
            for c in range(3):
                chairs.append((tx + 10 + c * 18, ty - 8, (tx + 10 + c * 18, ty + 13)))
                chairs.append((tx + 10 + c * 18, ty + 34, (tx + 10 + c * 18, ty + 13)))
    for i, (dx, dy, table) in enumerate(chairs):
        chair("break", dx, dy, _angle(table[0] - dx, table[1] - dy))
        room_place(f"break:{i}", "break", dx, dy, look=table)
    machines = [262, 288]
    for mx in machines:
        furniture("coffee", "break", mx, 8, 22, 14, name="coffee")
    furniture("vending", "break", 312, 8, 22, 14)
    k = 0
    for mx in machines:
        for dy in (34, 45):
            for dx in (-6, 6):
                room_place(f"coffee:{k}", "break", mx + 11 + dx, dy, look=(mx + 11, 15))
                k += 1
    layout.break_seats = len(chairs)
    # workstations: a desk with a computer for every person of the three shifts
    shifts = list(model.crew.shifts)
    n_maint = sum(model.crew.shifts[c].maintenance for c in shifts)
    n_it = sum(model.crew.shifts[c].it for c in shifts)
    n_for = sum(model.crew.shifts[c].foreman for c in shifts)
    n_log = sum(model.crew.shifts[c].logistics for c in shifts)
    n_man = sum(model.crew.shifts[c].manager for c in shifts)
    if n_maint > 8 or n_it > 3 or n_for > 3 or n_log > 8 or n_man > 1:
        raise ValueError("the staff building has room for 8 maintenance technicians, 3 IT, 3 foremen, "
                         "8 logistics workers and one production manager")

    def workstation(prefix: str, rid: str, idx: int, dx: float, dy: float, w: float, facing: str,
                    laptop: bool = False) -> None:
        """A desk (dx, dy) with its computer and its chair, pushed in; `facing` see _desk_kit."""
        r = room_at[rid]
        kit = _desk_kit(r["x"] + dx, r["y"] + dy, w, OFFICE_D, facing)
        furniture("desk", rid, dx, dy, w, OFFICE_D)
        seat = [round(v, 1) for v in kit["chair"]]
        layout.furniture.append({"kind": "chair", "x": seat[0] - 4.5, "y": seat[1] - 4.5, "w": 9, "h": 9, "room": rid,
                                 "face": kit["sit"]})
        layout.furniture.append({"kind": "computer", "x": kit["monitor"][0], "y": kit["monitor"][1], "w": 18, "h": 2,
                                 "face": kit["screen"], "kb": list(kit["kb"]), "mouse": list(kit["mouse"]),
                                 "chair": seat, "room": rid})
        if laptop:
            layout.furniture.append({"kind": "laptop", "x": kit["laptop"][0], "y": kit["laptop"][1], "w": 11, "h": 8,
                                     "face": kit["screen"], "chair": seat, "room": rid})
        walk.place(f"{prefix}:{idx}", seat[0], seat[1], "bottom", room=rid, look=kit["look"], pc=True)

    # the production office: the andon board with the open calls (the shift meeting stands in front of it),
    # a meeting table, a desk for every foreman by the south wall and the desk of the production manager
    furniture("andon", "office", 8, 8, 104, 26)
    furniture("table", "office", 8, 62, 56, 18)
    for i in range(n_for):
        workstation("office", "office", i, 6 + i * 50, STAFF_H - 4 - OFFICE_D, 44, "S")
    if n_man:
        workstation("manager", "office", 0, 98, 62, 46, "N")
    for i, mx in enumerate(MEETING_X):                                     # the shift meeting: a row before the board
        room_place(f"meet:{i}", "office", mx, 46, look=(60, 21))
    # IT room: racks, a printer, a desk with a computer and a laptop for every IT technician
    furniture("rack", "it", 10, 8, 18, 36)
    furniture("rack", "it", 32, 8, 18, 36)
    furniture("printer", "it", 116, 10, 24, 16)
    for i in range(n_it):
        workstation("it", "it", i, 4 + i * 48, STAFF_H - 4 - OFFICE_D, 44, "S", laptop=True)
    # maintenance workshop: desks against the north wall and a row back to back with them, the
    # cabinet with the measuring instruments and the shelves with spare parts by the south wall
    cols = [4, 50, 120, 166]
    for i in range(n_maint):
        col, row = i % 4, i // 4
        if row == 0:
            workstation("workshop", "workshop", i, cols[col], 4, 42, "N")
        else:
            workstation("workshop", "workshop", i, cols[col], 66, 42, "S")
    furniture("cabinet", "workshop", 84, 120, 44, 22, name="instruments")
    furniture("shelf", "workshop", 4, 120, 60, 22)
    furniture("shelf", "workshop", 140, 120, 60, 22)
    room_place("instr", "workshop", 106, 110, look=(106, 130))
    # the logistics corner east of the building (the warehouse and the dock are outside the ESD area, they
    # come and go by their own gate from the street): they wait for the next truck in a row by the dock,
    # and after a while with nothing to do they sit down at the table (cards, or a book alone). Inside the
    # corner a walk goes around the table and the people standing there, out of it towards the dock
    cx0, cx1 = staff_end + 6, fgw["x"] - 14
    corner = walk.room("dock", cx0, staff_y, cx1 - cx0, STAFF_H - 6, (cx1 - 46, staff_y + 4))
    layout.corner = {"x": cx0, "y": staff_y, "w": cx1 - cx0, "h": STAFF_H - 6,
                     "name": "Logistics corner", "name_sk": "Kútik skladníkov"}
    for k in range(n_log):
        col, row = divmod(k, 6)
        walk.place(f"dockwait:{k}", cx1 - 12 - 20 * col, staff_y + 24 + 19 * row, "bottom", room="dock",
                   look=(fgw["x"] + 40, staff_y + 24 + 19 * row))
    tx, ty, tw, th = cx0 + 22, staff_y + 50, 50, 26
    layout.furniture.append({"kind": "table", "x": tx, "y": ty, "w": tw, "h": th, "room": "dock", "cards": True})
    corner.solid(tx, ty, tw, th)
    middle = (tx + tw / 2, ty + th / 2)
    seats = [(tx + 14, ty - 8), (tx + 14, ty + th + 8), (tx - 8, ty + th / 2), (tx + tw + 8, ty + th / 2),
             (tx + tw - 14, ty - 8), (tx + tw - 14, ty + th + 8)][:LOUNGE_SEATS]
    for i, (sx, sy) in enumerate(seats):
        layout.furniture.append({"kind": "chair", "x": sx - 4.5, "y": sy - 4.5, "w": 9, "h": 9, "room": "dock",
                                 "face": _angle(middle[0] - sx, middle[1] - sy)})
        walk.place(f"lounge:{i}", sx, sy, "bottom", room="dock", look=middle)
    walk.place("street:log", cx1 - 50, staff_y + STAFF_H + 8, "bottom", look=(cx1 - 50, staff_y))

    # --- the repair centre: a room with its door in the north wall
    rx, ry = rpr["x"], rpr["y"]
    door = rpr["door"]
    rroom = walk.room("rpr", rx, ry, rpr["w"], rpr["h"], (door[0], ry + 4))
    for key in ("inrack", "outrack", "quarantine", "scrap"):
        r = rpr[key]
        top = max(r["y"], ry)                              # the part inside the room
        rroom.solid(r["x"], top, r["w"], r["y"] + r["h"] - top)
    benches = [c for line in layout.lines.values() if line["kind"] == "REPAIR" for cell in line["cells"] for c in cell["stations"]]
    for code in benches:
        st = layout.stations[code]
        rroom.solid(st["x"], st["y"], st["w"], st["h"])
    inr, outr, scr = rpr["inrack"], rpr["outrack"], rpr["scrap"]
    for k in range(len(benches)):
        # every bench has its own place at the racks and the scrap bin, so nobody stands in a colleague
        walk.place(f"rprin:{k}", inr["x"] + 15 + 30 * k, inr["y"] + inr["h"] + 5, "bottom", room="rpr",
                   look=(inr["x"] + 15 + 30 * k, inr["y"] + inr["h"] / 2))
        walk.place(f"rprout:{k}", outr["x"] + 10 + 18.5 * k, outr["y"] + outr["h"] + 5, "bottom", room="rpr",
                   look=(outr["x"] + 10 + 18.5 * k, outr["y"] + outr["h"] / 2))
        walk.place(f"scrap:{k}", scr["x"] + 12 + 22 * k, scr["y"] - 6, "bottom", room="rpr",
                   look=(scr["x"] + 12 + 22 * k, scr["y"] + scr["h"] / 2))

    # --- the board with the head count of the shifts, in the quiet corner right of the last final
    # assembly line (above the bottom walkway, clear of the robot lanes)
    bx0 = max(sx0 + 270, row_end(fa, rows - 1) + 40 + AMR_L)
    layout.board = {"x": bx0, "y": y_b - 74, "w": 420, "h": 54}

    # --- team desks (for the seats whose home is the desk of their line): the operator sits south of
    # the desk and looks north, at the screen with the state of the line. Beside the desk the place
    # where the next person waits for the hand-over, below it the waiting places of the relief people
    layout.desks = []
    layout.terminals = []
    layout.handover = {}
    seat_rules = model.crew.seats
    n_shifts = len(model.crew.shifts)
    for line in smt + fa:
        rules = seat_rules.get(line.area_kind.value, [])
        if not any(r.home == "desk" for r in rules):
            continue
        info = layout.lines[line.code]
        row = info["row"]
        x0 = zones[line.area_kind.value]["desk"]
        dy = spine[row] + 10
        kit = _desk_kit(x0, dy, DESK_W, DESK_D, "N")
        corr = f"{line.area_kind.value}:{row}"
        layout.desks.append({"id": f"desk:{line.code}", "line": line.code, "x": x0, "y": dy, "w": DESK_W, "h": DESK_D,
                             "chair": [round(v, 1) for v in kit["chair"]], "face": kit["sit"]})
        layout.terminals.append({"id": f"pc:{line.code}", "x": kit["monitor"][0], "y": kit["monitor"][1], "w": 16,
                                 "face": kit["screen"], "kb": list(kit["kb"]), "mouse": list(kit["mouse"]),
                                 "desk": line.code})
        walk.place(f"desk:{line.code}", kit["chair"][0], kit["chair"][1], corr, look=kit["look"], pc=True)
        walk.place(f"hand:desk:{line.code}", x0 - 10, kit["chair"][1], corr, look=kit["look"])
        layout.handover[f"desk:{line.code}"] = f"hand:desk:{line.code}"
        # one waiting place per shift (no two stand on one spot at a hand-over), left and right of the
        # way from the chair to the walkway
        for ord_, dx in zip(range(n_shifts), (1, DESK_W - 1, DESK_W + 17)):
            walk.place(f"reserve:{line.code}:{ord_}", x0 + dx, spine[row] + RESERVE_DY, corr, look=kit["look"])

    # --- a standing place at every machine, and beside it the place for the next person
    for code, st in layout.stations.items():
        info = layout.lines[st["line"]]
        kind = info["kind"]
        row = info["row"]
        top_lane = st["lanes"] >= 2 and st["lane"] == 0 and kind != "REPAIR"
        centre = (st["cx"], st["y"] + st["h"] / 2)
        # three places in front of a machine: the operator in the middle, beside him on the right the
        # next person waiting for the hand-over, on the left a technician or the foreman
        if kind == "REPAIR":
            y = st["y"] - SPOT
            walk.place(f"spot:{code}", st["cx"], y, "bottom", room="rpr", look=centre)
            walk.place(f"hand:spot:{code}", st["cx"] + HANDOVER_DX + 2, y, "bottom", room="rpr", look=centre)
            walk.place(f"tech:{code}", st["cx"] - HANDOVER_DX - 2, y, "bottom", room="rpr", look=centre)
        else:
            y = st["y"] - SPOT if top_lane else st["y"] + st["h"] + SPOT
            corr = f"{kind}:{row - 1}" if top_lane else f"{kind}:{row}"
            walk.place(f"spot:{code}", st["cx"], y, corr, look=centre)
            walk.place(f"hand:spot:{code}", st["cx"] + HANDOVER_DX, y, corr, look=centre)
            walk.place(f"tech:{code}", st["cx"] - HANDOVER_DX, y, corr, look=centre)
        layout.spots[code] = (st["cx"], y)
        layout.handover[f"spot:{code}"] = f"hand:spot:{code}"

    # --- waiting places of the relief people at packing (in front of the line) and in the repair centre
    for line in pack:
        info = layout.lines[line.code]
        for ord_ in range(n_shifts):
            walk.place(f"reserve:{line.code}:{ord_}", info["x1"] + 6 + 18 * ord_, info["spine"] + RESERVE_DY,
                       f"PACK:{info['row']}", look=(info["x1"] + 22, info["spine"]))
    for code, info in layout.lines.items():
        if info["kind"] != "REPAIR":
            continue
        for ord_ in range(n_shifts):
            walk.place(f"reserve:{code}:{ord_}", rx + 40 + 22 * ord_, ry + 120, "bottom", room="rpr",
                       look=(rx + 40 + 22 * ord_, ry + 72))

    # --- seats
    layout.seats = {}
    for line in model.lines.values():
        rules = seat_rules.get(line.area_kind.value, [])
        claimed: set[str] = set()
        for rule in rules:
            if "*" in rule.attends:
                continue
            for st in line.stations:
                if st.type in rule.attends:
                    claimed.add(st.code)
        for rule in rules:
            if rule.per_station:
                owners = [(f"{st.code}:{rule.code}", [st.code], st.code) for st in line.stations if st.type == rule.home]
            else:
                if "*" in rule.attends:
                    attends = [st.code for st in line.stations if st.code not in claimed]
                else:
                    attends = [st.code for st in line.stations if st.type in rule.attends]
                if rule.home == "desk":
                    home = None
                else:
                    home = next((st.code for st in line.stations if st.type == rule.home), None)
                    if home is None:
                        continue
                owners = [(f"{line.code}:{rule.code}", attends, home)]
            for key, attends, home_station in owners:
                if rule.per_station:
                    home_place = f"spot:{home_station}"
                    home_xy = layout.spots[home_station]
                else:
                    home_place = f"desk:{line.code}" if rule.home == "desk" else f"spot:{home_station}"
                    home_xy = (walk.places[home_place].x, walk.places[home_place].y)
                if not attends:
                    continue
                layout.seats[key] = {"line": line.code, "code": rule.code, "area": line.area_kind.value,
                                     "home": home_place, "handover": layout.handover[home_place],
                                     "home_station": home_station if rule.home != "desk" else None,
                                     "fixed": rule.fixed, "attends": attends}
                if rule.home != "desk" and home_station:   # his computer stands at the machine: a panel PC on an arm
                    st = layout.stations[home_station]
                    above = home_xy[1] < st["y"]
                    ty = st["y"] - 3 if above else st["y"] + st["h"] + 3
                    layout.terminals.append({"id": f"pc:{key}", "x": st["cx"] + 10, "y": ty, "w": 9,
                                             "face": 270 if above else 90, "station": home_station})

    # --- the dock: three bays under the finished goods warehouse
    layout.bays = []
    bay_x0 = fgw["x"] + 20
    by_bay = {t.bay: t.customer for t in model.shipping.trucks}
    for bay in sorted(by_bay):
        bx = bay_x0 + bay * BAY_PITCH
        layout.bays.append({"bay": bay, "customer": by_bay[bay], "x": bx, "rear_y": park_y,
                            "trailer_w": TRAILER_W, "trailer_l": TRAILER_L, "cab_l": CAB_L})
        walk.place(f"dock:{bay}", bx, park_y + 4, "bottom", x_dockwait, [(bx, park_y - 4), (x_dockwait, park_y - 4)],
                   look=(bx, park_y + 70))
        layout._add(f"truck_in:{bay}", "truck", [(bx, park_y + YARD_LEN), (bx, park_y)], model.shipping.yard_speed)
        layout._add(f"truck_out:{bay}", "truck", [(bx, park_y), (bx, park_y + YARD_LEN)], model.shipping.yard_speed)
        layout._add(f"ship:{bay}", "ship", [(bx, fgw["y"] + fgw["h"] - 22), (bx, park_y + 14)], 6.0)
    layout.world["h"] = max(layout.world["h"], int(park_y + TRAILER_L + CAB_L + 52), int(staff_y + STAFF_H + 30))
    layout.walkways = [[[c["x0"], c["y"]], [c["x1"], c["y"]]] for c in walk.corridors.values()] + \
                      [[[t["x"], t["y0"]], [t["x"], t["y1"]]] for t in walk.trunks.values()]

    _robots(layout, ctx, amr, spine, y_rb)
    layout.crossings = _crossings(layout)


# ---------------------------------------------------------------------- the robots
def _robots(layout, ctx: dict, amr: Walkways, spine: list[float], y_rb: float) -> None:
    """Where robots stop: in the gap after every cell of a line (its NOK tote, pieces back to the next
    cell), left of the first machine (pieces back to the loader; at final assembly the rack of parts, at
    packing the stack of empty boxes), at the supermarket, at the goods-in warehouse, at the racks of the
    repair centre, and their depot with one charger per robot. A robot stops off the walkways: it shifts
    sideways from its lane (omnidirectional wheels) into the gap between two machines."""
    model: PlantModel = layout.model
    rpr, sm, gi = ctx["rpr"], ctx["sm"], ctx["gi"]
    half = AMR_W / 2

    def dock(key: str, x: float, y: float, corr: str, face: float = 0, label: str = "") -> None:
        amr.place(key, x, y, corr, face=face)
        layout.docks[key] = {"x": round(x, 1), "y": round(y, 1), "f": face, "label": label}

    layout.racks = {}
    for code, info in layout.lines.items():
        if info["kind"] == "REPAIR":
            continue
        corr = f"{'SMT' if info['kind'] == 'SMT' else 'FA'}:{info['row']}"
        dy = GAP_DOCK_DY.get(info["kind"], GAP_DOCK_DY_DEFAULT)
        y = info["spine"] + dy
        cells = info["cells"]
        for i, cell in enumerate(cells):
            x0 = cell["x"] + cell["w"]                                    # the exit of the cell
            dock(f"gap:{code}:{i}", x0 + 8 + half, y, corr, face=90, label=f"{code} · {cell['type']}")
            layout.nok[f"{code}:{i}"] = {"x": round(x0 + 1.5, 1), "y": round(y - 12, 1), "w": NOK_W, "h": NOK_H,
                                         "line": code, "cell": i}
        x1 = info["x1"]
        dock(f"drop:{code}:0", x1 - 2 - half, y, corr, face=90, label=f"{code} · {cells[0]['type']}")
        if info["kind"] == "FA":
            layout.racks[f"drop:{code}:0"] = {"kind": "kit", "x": x1 + 0.5, "y": info["spine"] + 19, "w": 12, "h": 14}
        elif info["kind"] == "PACK":
            layout.racks[f"drop:{code}:0"] = {"kind": "klt", "x": x1 + 0.5, "y": info["spine"] + 19, "w": 13, "h": 16}
    # the supermarket (repaired boards): at its east wall, at the end of the robot lane of the middle aisle
    mid = len(spine) // 2
    dock("drop:SM", sm["x"] + sm["w"] + 2 + AMR_L / 2, spine[mid] + AMR_OFF, f"FA:{mid}", label="SM")
    # the goods-in warehouse: full bins of parts and stacks of empty boxes wait in its rack at the south wall
    gx = gi["x"] + gi["w"] / 2
    dock("gi", gx, gi["y"] + gi["h"] + 2 + AMR_L / 2, "bottom", face=90, label="GI")
    layout.racks["gi"] = {"kind": "gi", "x": gi["x"] + 8, "y": gi["y"] + gi["h"] - 18, "w": gi["w"] - 16, "h": 14}
    # the repair centre: inbound rack, outbound rack, the hatch of the quarantine cage
    for key, rack in (("rpr:in", rpr["inrack"]), ("rpr:out", rpr["outrack"]), ("rpr:quar", rpr["quarantine"])):
        dock(key, rack["x"] + rack["w"] / 2, rack["y"] - 1 - half, "bottom", label=key)
    # the depot: a charger for every robot, left of the repair centre; a robot parks nose first
    n = model.amr.robots
    x1 = rpr["x"] - 16
    x0 = x1 - n * CHARGERS_PITCH
    layout.depot = {"x": round(x0, 1), "y": round(y_rb + 12, 1), "w": round(x1 - x0, 1), "h": 70, "chargers": []}
    for i in range(n):
        cx = x0 + CHARGERS_PITCH / 2 + i * CHARGERS_PITCH
        cy = layout.depot["y"] + 6 + AMR_L / 2
        layout.depot["chargers"].append({"x": round(cx, 1), "y": round(cy + AMR_L / 2 + 3, 1)})
        dock(f"charge:{i}", cx, cy, "bottom", face=90, label=f"charger {i + 1}")
    layout.amr_lanes = [[[c["x0"], c["y"]], [c["x1"], c["y"]]] for c in amr.corridors.values()] + \
                       [[[t["x"], t["y0"]], [t["x"], t["y1"]]] for t in amr.trunks.values()]


# ---------------------------------------------------------------------- crossings
def _cross(a, b, c, d) -> tuple[float, float] | None:
    """Where segment ab crosses segment cd (touching at an end does not count)."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-9:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
    u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
    if 1e-6 < t < 1 - 1e-6 and 1e-6 < u < 1 - 1e-6:
        return a[0] + t * r[0], a[1] + t * r[1]
    return None


def _crossings(layout) -> list[dict]:
    """Every point where a walkway or an AMR lane passes a conveyor. The conveyor goes over the aisle
    on a bridge there (the belts to the trucks have a lift gate instead)."""
    walks = layout.walk.segments() + layout.amr.segments()
    out: dict[tuple[int, int], dict] = {}
    for key, route in layout.routes.items():
        if route.kind not in BELTS:
            continue
        for c, d in zip(route.pts, route.pts[1:]):
            length = math.dist(c, d)
            if length < 0.01:
                continue
            for a, b in walks:
                hit = _cross(a, b, c, d)
                if hit is None:
                    continue
                spot = (round(hit[0]), round(hit[1]))
                if spot in out:
                    continue
                out[spot] = {"x": round(hit[0], 1), "y": round(hit[1], 1), "route": key, "kind": "gate" if route.kind == "ship" else "bridge",
                             "dx": round((d[0] - c[0]) / length, 3), "dy": round((d[1] - c[1]) / length, 3),
                             "wide": route.kind in ("wh", "ship")}
    # two aisles side by side under one belt (a walkway and an AMR lane): one bridge over both
    merged: list[dict] = []
    for c in sorted(out.values(), key=lambda v: (v["route"], v["x"], v["y"])):
        last = merged[-1] if merged else None
        if last and last["route"] == c["route"] and (last["dx"], last["dy"]) == (c["dx"], c["dy"])                 and math.dist((last["x"], last["y"]), (c["x"], c["y"])) < 24:
            last["len"] = round(last["len"] + math.dist((last["x"], last["y"]), (c["x"], c["y"])), 1)
            last["x"], last["y"] = round((last["x"] + c["x"]) / 2, 1), round((last["y"] + c["y"]) / 2, 1)
            continue
        merged.append({**c, "len": 22})
    return sorted(merged, key=lambda v: (v["x"], v["y"]))
