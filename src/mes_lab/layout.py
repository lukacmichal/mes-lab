"""Floor plan of the plant, seen from above.

One description of where everything stands, used twice:

* the web page draws the plant from it (machines, conveyors, warehouses);
* the simulation takes the length of every conveyor from it, so a piece that
  rides from the SMT line to the supermarket needs as long as the picture shows.

Units are "plan units" (u). The flow runs left to right: goods-in warehouse ->
SMT lines -> supermarket -> final assembly lines -> packing -> finished goods
warehouse. Row i of SMT and final assembly share the same height, so the
supermarket can be fed from the left and emptied to the right by straight belts.
The repair centre sits below; robots (AMR) carry pieces there and back along the
aisles (see layout_crew.py and sim/amr.py), next to their depot with the chargers.

Everything is computed from the plant model (lines, cells, buffers), nothing is
hand-placed per line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from mes_lab import layout_crew

from mes_lab.model.schema import AreaKind, Line, PlantModel

H = 34            # depth of a machine
LANE = 22         # distance of the two lanes of a double cell from the spine
SLOT = 12         # one conveyor place of a buffer
BUF_PAD = 8
ROW = 150         # distance between the spines of two rows: an aisle with two walking lanes and a robot lane
TOP = 104         # room for area titles and line labels
SMT_LEFT = 194    # goods-in warehouse -> first SMT machine: robot trunk, team desks, walkway trunk
FA_LEFT = 216     # supermarket -> first final assembly machine: the same
COL_GAP = 60      # end of the longest final assembly line -> collector belt to packing

WIDTH = {
    "LOADER": 36, "LASER_PCB": 36, "PASTE_PRINTER": 48, "SPI": 40, "PNP_CHIP": 54, "PNP_IC": 54,
    "REFLOW_OVEN": 96, "AOI": 42, "UNLOADER": 36, "DEPANELER": 44,
    "FA_LOADER": 40, "PRESS_FIT": 38, "COATER": 44, "UV_CURE": 70, "COATING_AOI": 40,
    "POTTING": 44, "CURING_OVEN": 110, "ICT": 44, "SCREWDRIVER": 44, "FLASHER": 36,
    "EOL_TESTER": 44, "LASER_UNIT": 36, "FINAL_INSPECTION": 44,
    "PACK_SCAN": 44, "BOX_LABEL": 38, "PALLETIZER": 64, "REPAIR_BENCH": 52,
}
DEFAULT_WIDTH = 44

# belt speeds in u/s; the sim needs the time a piece rides, so these are real numbers
SPEED = {"supply": 1.5, "sm": 1.5, "feed": 1.5, "pack": 1.5, "wh": 1.5, "ret": 5.0, "wash": 1.0}
RET_DY = 10       # the board return conveyor of an SMT line runs underneath the machines, this far below the spine
MIN_RIDE_S = 6.0


@dataclass
class Route:
    key: str
    kind: str
    pts: list[tuple[float, float]]
    speed: float
    dwell: tuple[float, float, float] | None = None  # (from, to, at) as fractions of the ride / path
    fixed_s: float | None = None                      # duration decided elsewhere (washing)
    label: str = ""

    @property
    def length(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.pts, self.pts[1:]))

    @property
    def duration(self) -> float:
        if self.fixed_s is not None:
            return self.fixed_s
        return max(MIN_RIDE_S, self.length / self.speed)

    def as_dict(self) -> dict:
        data = {"kind": self.kind, "pts": [[round(x, 1), round(y, 1)] for x, y in self.pts],
                "len": round(self.length, 1), "dur": round(self.duration, 1)}
        if self.dwell:
            data["dwell"] = [round(v, 4) for v in self.dwell]
        if self.label:
            data["label"] = self.label
        return data


class Layout:
    """The computed floor plan. `data` is what the web page receives."""

    def __init__(self, model: PlantModel):
        self.model = model
        self.routes: dict[str, Route] = {}
        self.lines: dict[str, dict] = {}
        self.stations: dict[str, dict] = {}
        self.buildings: list[dict] = []
        self.zones: list[dict] = []
        self.world = {"w": 0, "h": 0}
        # the people side (layout_crew.py)
        self.spots: dict[str, tuple[float, float]] = {}
        self.seats: dict[str, dict] = {}
        self.rooms: list[dict] = []
        self.break_seats = 0
        self.furniture: list[dict] = []
        self.desks: list[dict] = []
        self.terminals: list[dict] = []
        self.bays: list[dict] = []
        self.walkways: list = []
        self.amr_lanes: list = []
        self.board: dict = {}
        self.corner: dict = {}                 # the logistics corner east of the staff building
        self.walk: layout_crew.Walkways | None = None
        # the robots (AMR): their lanes and stops, the NOK totes at the machines, their depot
        self.amr: layout_crew.Walkways | None = None
        self.nok: dict[str, dict] = {}
        self.docks: dict[str, dict] = {}
        self.depot: dict = {}
        # where a walkway passes a conveyor: the conveyor goes over it on a bridge (or a gate at the dock)
        self.crossings: list[dict] = []
        self.handover: dict[str, str] = {}     # home place of a seat -> where the next person waits beside it
        self.racks: dict[str, dict] = {}       # robot stop -> the rack beside it (parts, empty boxes, goods-in)
        self._build()

    # ------------------------------------------------------------------ public
    def duration(self, key: str) -> float:
        return self.routes[key].duration

    @property
    def data(self) -> dict:
        types = {code: {"tag": t.tag, "name": t.name, "kind": t.kind.value,
                        "capacity": t.capacity, "transit_s": t.transit_s,
                        "container": t.container}
                 for code, t in self.model.station_types.items()}
        products = {}
        for mat in self.model.finished_goods():
            pcba = self.model.materials[mat.pcba]
            products[mat.code] = {
                "name": mat.name, "pcba": pcba.code, "boards_per_panel": pcba.boards_per_panel,
                "two_sided": len(pcba.routing) > 1,
                "klt_qty": mat.packing.klt_qty if mat.packing else 0,
                "klt_per_pallet": mat.packing.klt_per_pallet if mat.packing else 0,
                "safety_stock": pcba.planning.safety_stock,
                "customer": self.model.customers[mat.customer].name if mat.customer else "",
                "customer_code": mat.customer,
                "sizes": self._sizes(mat, pcba),
            }
        return {"v": 1, "world": self.world, "zones": self.zones, "buildings": self.buildings,
                "lines": self.lines, "stations": self.stations, "types": types, "products": products,
                "routes": {key: route.as_dict() for key, route in self.routes.items()},
                "geometry": {"H": H, "LANE": LANE, "SLOT": SLOT},
                "rooms": self.rooms, "furniture": self.furniture, "desks": self.desks,
                "terminals": self.terminals, "bays": self.bays, "board": self.board, "corner": self.corner,
                "walkways": self.walkways, "nok": self.nok, "docks": self.docks, "depot": self.depot,
                "amr_lanes": self.amr_lanes, "crossings": self.crossings, "racks": self.racks,
                "amr": {"robots": self.model.amr.robots, "speed": self.model.amr.speed,
                        "capacity": self.model.amr.capacity, "size": [layout_crew.AMR_L, layout_crew.AMR_W]},
                "seats": {key: {k: v for k, v in seat.items()} for key, seat in self.seats.items()},
                "crew": {"walk_speed": self.model.crew.walk_speed,
                         "shifts": {code: s.model_dump() for code, s in self.model.crew.shifts.items()}}}

    @staticmethod
    def _sizes(mat, pcba) -> dict:
        """Display sizes in plan units: panel and boards, the product, the KLT box and the pallet with
        the way the boxes sit on it. Products are scaled so that the biggest still fits a conveyor place."""
        def fit(l: float, w: float, per_u: float, biggest: float = 12.4) -> list[float]:
            scale = min(1 / per_u, biggest / max(l, w))
            return [round(l * scale, 2), round(w * scale, 2)]

        pk = mat.packing
        cols, rows = pcba.panel_grid or (2, math.ceil(pcba.boards_per_panel / 2))
        panel = [round(pcba.panel_mm[0] / 28, 2), round(pcba.panel_mm[1] / 28, 2)]
        out = {"panel": panel, "grid": [cols, rows], "unit": fit(mat.unit_mm[0], mat.unit_mm[1], 12)}
        if pk is not None:
            kl, kw = pk.klt_mm[0], pk.klt_mm[1]
            pl, pw = pk.pallet_mm
            across, down = max(1, pl // kl), max(1, pw // kw)         # boxes per layer, the way they fit best
            alt = (max(1, pl // kw), max(1, pw // kl))
            if alt[0] * alt[1] > across * down:
                across, down = alt
            per_layer = across * down
            out["klt"] = [round(kl / 36, 2), round(kw / 36, 2)]
            out["pallet"] = [round(pl / 60, 2), round(pw / 60, 2)]
            out["pallet_type"] = pk.pallet_type
            out["pallet_grid"] = [across, down, math.ceil(pk.klt_per_pallet / per_layer)]
        return out

    # ------------------------------------------------------------------ building
    def _build(self) -> None:
        m = self.model
        smt = m.lines_of_kind(AreaKind.SMT)
        fa = m.lines_of_kind(AreaKind.FA)
        pack = m.lines_of_kind(AreaKind.PACK)
        repair = m.lines_of_kind(AreaKind.REPAIR)
        rows = max(len(smt), len(fa), 1)
        spine = [TOP + 39 + i * ROW for i in range(rows)]
        bottom = spine[-1] + 39
        y_rooms = spine[-1] + 128            # the repair centre and the staff building, below the bottom aisle

        # --- goods-in warehouse
        gi = {"id": "GI", "kind": "goods_in", "x": 14, "y": TOP - 6, "w": 92, "h": bottom - TOP + 12,
              "name": "Goods-in warehouse", "name_sk": "Sklad materiálu"}
        self.buildings.append(gi)

        # --- SMT lines
        x_smt = gi["x"] + gi["w"] + SMT_LEFT
        smt_end = x_smt
        for i, line in enumerate(smt):
            info = self._line(line, x_smt, spine[i], i)
            smt_end = max(smt_end, info["x2"])
        # --- supermarket
        sm = {"id": "SM", "kind": "supermarket", "x": smt_end + 94, "y": TOP - 6, "w": 94,
              "h": bottom - TOP + 12, "name": "SMT supermarket", "name_sk": "SMT supermarket",
              "racks": self._racks(spine)}
        self.buildings.append(sm)
        # --- final assembly lines
        x_fa = sm["x"] + sm["w"] + FA_LEFT
        fa_end = x_fa
        for i, line in enumerate(fa):
            info = self._line(line, x_fa, spine[i], i)
            fa_end = max(fa_end, info["x2"])
        # --- packing lines and the collector belts in front of them
        x_col = fa_end + COL_GAP
        x_pack = x_col + 40
        pack_rows = self._pack_rows(fa, pack)
        pack_end = x_pack
        for line in pack:
            row = pack_rows[line.code]
            info = self._line(line, x_pack, spine[row], row)
            pack_end = max(pack_end, info["x2"])
        # --- finished goods warehouse
        fgw = {"id": "FGW", "kind": "fg_warehouse", "x": pack_end + 70, "y": TOP - 6, "w": 118,
               "h": bottom - TOP + 12, "name": "Finished goods warehouse", "name_sk": "Sklad hotových výrobkov",
               "racks": self._racks(spine, fg=True)}
        self.buildings.append(fgw)
        # --- repair centre: a room with one door in the north wall and pass-through racks in it
        # (robots put pieces in from the aisle, the repair people take them out inside), the
        # quarantine cage with its own hatch, the scrap bin and the benches
        rpr_x = sm["x"] - 40
        y0 = y_rooms
        for line in repair:
            self._repair_line(line, rpr_x + 20, y0 + 72)
        rpr = {"id": "RPR", "kind": "repair", "x": rpr_x, "y": y0, "w": 402, "h": 138,
               "name": "Repair and analysis", "name_sk": "Opravy a analýza",
               "inrack": {"x": rpr_x + 112, "y": y0 - 6, "w": 120, "h": 30, "cols": 10},
               "outrack": {"x": rpr_x + 20, "y": y0 - 6, "w": 76, "h": 22, "cols": 6},
               "quarantine": {"x": rpr_x + 300, "y": y0 - 6, "w": 92, "h": 52},
               "scrap": {"x": rpr_x + 300, "y": y0 + 92, "w": 92, "h": 34},
               "door": [rpr_x + 258, y0], "door_w": 16}
        self.buildings.append(rpr)

        self.world = {"w": int(fgw["x"] + fgw["w"] + 20), "h": int(rpr["y"] + rpr["h"] + 18)}
        self._zones(smt, fa, pack, gi, sm, fgw, rpr, x_smt, x_fa, x_pack, smt_end, fa_end, pack_end)

        # --- conveyors
        self._material_routes(smt, fa, pack, pack_rows, gi, sm, fgw, x_col, spine)

        # --- people: staff building, desks, walkways, seats, the dock
        layout_crew.build(self, {"spine": spine, "smt": smt, "fa": fa, "pack": pack, "fgw": fgw, "rpr": rpr,
                                 "sm": sm, "gi": gi, "x_col": x_col, "y_rooms": y_rooms})

    # ------------------------------------------------------------------ lines
    def _line(self, line: Line, x0: float, spine: float, row: int) -> dict:
        types = self.model.station_types
        cap = max(1, line.buffer)
        cells = []
        x = x0
        for cell in line.cells:
            w = max(WIDTH.get(cell.type, DEFAULT_WIDTH), 24)
            buf = None
            gap = 0
            if cell.index > 0:
                gap = cap * SLOT + BUF_PAD
                buf = {"x0": x, "x1": x + gap, "y": spine, "slots": cap}
                x += gap
            n = len(cell.stations)
            codes = []
            for k, st in enumerate(cell.stations):
                lane = 0.0 if n == 1 else (-LANE if k == 0 else LANE) if n == 2 else (k - (n - 1) / 2) * (H + 8)
                rect = {"x": x, "y": spine + lane - H / 2, "w": w, "h": H, "cx": x + w / 2,
                        "cy": spine + lane, "line": line.code, "cell": cell.index, "lane": k,
                        "lanes": n, "type": cell.type, "tag": types[cell.type].tag,
                        "kind": types[cell.type].kind.value, "name": st.name}
                self.stations[st.code] = rect
                codes.append(st.code)
            cells.append({"i": cell.index, "type": cell.type, "x": x, "w": w, "stations": codes, "buf": buf})
            x += w
        info = {"code": line.code, "name": line.name, "area": line.area, "kind": line.area_kind.value,
                "row": row, "spine": spine, "x1": x0, "x2": x, "buffer": cap,
                "job_line": line.is_job_line, "materials": list(line.materials), "cells": cells}
        top = min(self.stations[c]["y"] for cell in cells for c in cell["stations"])
        bot = max(self.stations[c]["y"] + H for cell in cells for c in cell["stations"])
        info["y1"], info["y2"] = top, bot
        self.lines[line.code] = info
        return info

    def _repair_line(self, line: Line, x0: float, y: float) -> None:
        """Repair benches stand in a row; items arrive by the rework conveyors."""
        types = self.model.station_types
        cell = line.cells[0]
        pitch = WIDTH["REPAIR_BENCH"] + 20
        codes = []
        for k, st in enumerate(cell.stations):
            rect = {"x": x0 + k * pitch, "y": y - H / 2, "w": WIDTH["REPAIR_BENCH"], "h": H,
                    "cx": x0 + k * pitch + WIDTH["REPAIR_BENCH"] / 2, "cy": y, "line": line.code,
                    "cell": 0, "lane": k, "lanes": len(cell.stations), "type": cell.type,
                    "tag": types[cell.type].tag, "kind": types[cell.type].kind.value, "name": st.name}
            self.stations[st.code] = rect
            codes.append(st.code)
        x2 = x0 + (len(codes) - 1) * pitch + WIDTH["REPAIR_BENCH"]
        self.lines[line.code] = {
            "code": line.code, "name": line.name, "area": line.area, "kind": line.area_kind.value,
            "row": -1, "spine": y, "x1": x0, "x2": x2, "buffer": 0, "job_line": False,
            "materials": [], "cells": [{"i": 0, "type": cell.type, "x": x0, "w": x2 - x0,
                                        "stations": codes, "buf": None}],
            "y1": y - H / 2, "y2": y + H / 2}

    def _pack_rows(self, fa: list[Line], pack: list[Line]) -> dict[str, int]:
        rows: dict[str, int] = {}
        for line in pack:
            feeding = sorted(i for i, f in enumerate(fa) if set(f.materials) & set(line.materials))
            rows[line.code] = feeding[(len(feeding) - 1) // 2] if feeding else 0
        return rows

    def _racks(self, spine: list[float], fg: bool = False) -> list[dict]:
        """Shelves of the supermarket / finished goods warehouse, one band per product,
        as tall as the final assembly rows that build it."""
        m = self.model
        fa = m.lines_of_kind(AreaKind.FA)
        racks = []
        done: dict[str, list[int]] = {}
        for i, line in enumerate(fa):
            for code in line.materials:
                done.setdefault(code, []).append(i)
        for code, idx in done.items():
            lo, hi = min(idx), max(idx)
            racks.append({"product": code, "pcba": m.materials[code].pcba,
                          "y0": spine[lo] - ROW / 2 + 6 if lo else TOP,
                          "y1": spine[hi] + ROW / 2 - 6 if hi < len(spine) - 1 else spine[hi] + 39,
                          "rows": [lo, hi]})
        return racks

    # ------------------------------------------------------------------ zones
    def _zones(self, smt, fa, pack, gi, sm, fgw, rpr, x_smt, x_fa, x_pack, smt_end, fa_end, pack_end):
        top, h = TOP - 60, rpr["y"] - TOP + 52
        def zone(zid, kind, name, name_sk, x0, x1, y0=top, y1=None):
            self.zones.append({"id": zid, "kind": kind, "name": name, "name_sk": name_sk,
                               "x": x0 - 12, "y": y0, "w": x1 - x0 + 24, "h": (y1 or top + h) - y0})
        if smt:
            zone("SMT", "SMT", "Surface mount (SMT)", "Povrchová montáž (SMT)", x_smt, smt_end)
        if fa:
            zone("FA", "FA", "Final assembly", "Finálna montáž", x_fa, fa_end)
        if pack:
            zone("PACK", "PACK", "Packing", "Balenie", x_pack, pack_end)

    # ------------------------------------------------------------------ routes
    def _add(self, key: str, kind: str, pts: list[tuple[float, float]], speed: float,
             **kw) -> Route:
        route = Route(key, kind, pts, speed, **kw)
        self.routes[key] = route
        return route

    def _material_routes(self, smt, fa, pack, pack_rows, gi, sm, fgw, x_col, spine) -> None:
        gi_right = gi["x"] + gi["w"]
        sm_left, sm_right = sm["x"], sm["x"] + sm["w"]
        for line in smt:
            info = self.lines[line.code]
            y = info["spine"]
            self._add(f"supply:{line.code}", "supply", [(gi_right, y), (info["x1"], y)], SPEED["supply"])
            self._add(f"sm:{line.code}", "sm", [(info["x2"], y), (sm_left, y)], SPEED["sm"])
            cells = info["cells"]
            ldr, uld = cells[0], next((c for c in cells if c["type"] == "UNLOADER"), cells[-1])
            cx_l, cx_u = ldr["x"] + ldr["w"] / 2, uld["x"] + uld["w"] / 2
            # panels for the second side go back underneath the machines (a lower conveyor level)
            self._add(f"ret:{line.code}", "ret",
                      [(cx_u, y), (cx_u, y + RET_DY), (cx_l, y + RET_DY), (cx_l, y)], SPEED["ret"])
            spi = next((c for c in cells if c["type"] == "SPI"), None)
            if spi:
                cx_s = spi["x"] + spi["w"] / 2
                x_w = (cx_l + cx_s) / 2
                pts = [(cx_s, y), (cx_s, y - 26), (x_w, y - 26), (x_w, y - 38), (x_w, y - 26),
                       (cx_l, y - 26), (cx_l, y)]
                route = self._add(f"wash:{line.code}", "wash", pts, SPEED["wash"], label="washing",
                                  fixed_s=self.model.station_types["SPI"].wash_min * 60)
                total = route.length
                at = (math.dist(pts[0], pts[1]) + math.dist(pts[1], pts[2]) + math.dist(pts[2], pts[3])) / total
                route.dwell = (0.07, 0.93, at)
        for line in fa:
            info = self.lines[line.code]
            y = info["spine"]
            self._add(f"feed:{line.code}", "feed", [(sm_right, y), (info["x1"], y)], SPEED["feed"])
            target = next((p for p in pack if set(p.materials) & set(line.materials)), None)
            if target is not None:
                py = self.lines[target.code]["spine"]
                px = self.lines[target.code]["x1"]
                self._add(f"pack:{line.code}", "pack",
                          [(info["x2"], y), (x_col, y), (x_col, py), (px, py)], SPEED["pack"])
        for line in pack:
            info = self.lines[line.code]
            y = info["spine"]
            self._add(f"wh:{line.code}", "wh", [(info["x2"], y), (fgw["x"], y)], SPEED["wh"])


def build_layout(model: PlantModel) -> Layout:
    """The floor plan of a model."""
    return Layout(model)
