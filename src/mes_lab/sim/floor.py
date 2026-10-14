"""A picture of the shop floor for the live view: where every piece is right now.

The edge gateway sends it to the MES once a second next to the usual summaries.
Nothing in it is invented for the picture: a piece is where the simulation holds it
(inside a station, in the queue in front of it, on a conveyor), a station reports the
phase it is really in and how far the cycle has got, and what a piece looks like comes
from the stations it has passed (`done`).

Times are simulation seconds (`t`); the page turns them into wall-clock motion with
the simulation speed. Pieces carry a stable `i`, so the page can follow one piece from
station to station.

Pieces that leave a line for repair or quarantine are never in the air: they lie in the NOK tote
at the machine (`boxes`), ride on a robot (`amr[].ld`), lie in a rack of the repair centre (`rack`,
`boxes["rpr:out"]`, `cage`) or are in the hands of a repair person (`people[].h`). Those places say
since when the piece is there (`at`), and the rides that ended a moment ago are listed (`trh`): the
page shows the plant a little late and so knows every move before it draws it.

Compact keys: i id, sn serial number (once the MES has issued it), k kind (P panel, B board, U unit, K KLT box, L pallet, G magazine of
bare panels), p product, n pieces inside, c capacity, d stations passed (tags),
s side (0 single sided, 1 first side, 2 second side), x bad-mark boards of a panel / bad
mark of a board, f defect found (waiting for repair).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mes_lab.model.schema import AreaKind

from .traffic import keep_seconds
from .core import Item

if TYPE_CHECKING:
    from .plant import PlantSim
    from .station import Station

KIND = {"PANEL": "P", "BOARD": "B", "UNIT": "U", "KLT": "K", "PALLET": "L", "MAG": "G", "BIN": "N", "STACK": "S"}
QUEUE_SHOWN = 24   # unbounded queues (repair, packing) list this many pieces, the rest is counted


def describe(plant: PlantSim, item: Item) -> dict:
    kind = item.kind
    d: dict = {"i": item.id, "k": KIND[kind]}
    if kind in ("PANEL", "BOARD"):
        d["p"] = item.material.removeprefix("PCBA-")
    elif kind != "MAG":
        d["p"] = item.material
    if item.serial:
        d["sn"] = item.serial
    if kind == "PANEL":
        d["n"] = len(item.children)
        bad = [i for i, board in enumerate(item.children) if board.bad_mark]
        if bad:
            d["x"] = bad
        d["d"] = sorted(plant.tags[t] for t in item.done)
        job = item.job
        if job is not None and len(job.material.routing) > 1:
            d["s"] = 1 if job.is_first else 2
        if any(board.found for board in item.children):
            d["f"] = 1
    elif kind == "BOARD":
        if item.bad_mark:
            d["x"] = 1
        if item.found:
            d["f"] = 1
    elif kind == "UNIT":
        d["d"] = sorted(plant.tags[t] for t in item.done)
        if item.found or any(c.found for c in item.children):
            d["f"] = 1
    elif kind in ("KLT", "PALLET"):
        packing = plant.model.materials[item.material].packing
        d["n"] = len(item.children)
        d["c"] = (packing.klt_qty if kind == "KLT" else packing.klt_per_pallet) if packing else 0
    elif kind == "MAG":
        d["n"] = item.qty
    elif kind in ("BIN", "STACK"):
        d["p"] = item.material.removeprefix("KIT-")
        d["n"] = item.qty                                    # parts in the bin (0: empty), boxes in the stack
    return d


def _queue(plant: PlantSim, items: list[Item]) -> dict:
    return {"n": len(items), "it": [describe(plant, i) for i in items[:QUEUE_SHOWN]]}


def station_entry(plant: PlantSim, st: Station) -> dict:
    e: dict = {"s": st.state.value, "ph": st.phase}
    if st.reason:
        e["r"] = st.reason
    if st.last_result:
        e["lr"] = st.last_result
    job = st.current_job()
    if job is not None:
        e["jm"] = job.material.code
        e["jo"] = job.operation.op
    if st.item is not None:
        e["it"] = describe(plant, st.item)
    if st.out_items:
        e["out"] = [describe(plant, i) for i in st.out_items]
    if st.phase in ("work", "verify"):
        total, left = st.work_state()
        e["w"] = [round(total, 1), round(left, 1)]
    inside = getattr(st, "inside_items", None)
    if inside:      # how far through the tunnel each piece is (0..1); the belt may stand still
        belt = st.belt_time()
        run = st.type.transit_s
        e["ov"] = [[describe(plant, i), round(min(1.0, (belt - t) / run), 3)] for i, t in inside]
        e["belt"] = 1 if st.belt_since is not None else 0
    if st.call is not None:
        c = st.call
        e["cr"] = {"k": "call", "ph": c.ph, "p": c.person.id if c.person else None, "role": c.role[:3]}
    elif st.attendance is not None:
        a = st.attendance
        e["cr"] = {"k": a.kind, "ph": a.ph, "p": a.person.id if a.person else None}
    box = st.container
    if box is not None:
        packing = plant.model.materials[box.material].packing
        size = (packing.klt_qty if st.type.container == "klt" else packing.klt_per_pallet) if packing else 0
        e["fill"] = [len(box.children), size, box.serial]
    return e


def snapshot(plant: PlantSim) -> dict:
    now = plant.env.now
    stations = {st.code: station_entry(plant, st) for st in plant.all_stations()}

    bufs: dict[str, list[dict]] = {}
    for code, line in plant.lines.items():
        if line.spec.area_kind == AreaKind.REPAIR:          # the pieces lie in the inbound rack (see "rack")
            bufs[code] = [{"n": len(plant.inrack), "it": []}]
            continue
        cells = []
        for cell in line.cells:
            items = list(cell.inbox.items) if cell.inbox is not None else []
            items += line.pending_in.get(cell.index, [])
            cells.append(_queue(plant, items))
        bufs[code] = cells

    keep_s = keep_seconds(plant.speed)
    rides = [{"i": r.id, "r": r.route, "t": round(r.t0, 1), "d": round(r.dur, 1),
              "it": describe(plant, r.item)} for r in plant.logistics.rides.values()]
    # the rides that ended a moment ago (the page draws the plant a little late, see floor-core.js)
    ended = [{"i": r.item.id, "r": r.route, "t": round(r.t0, 1), "d": round(r.dur, 1)} for r in plant.logistics.recent(keep_s)]

    def desc(item: Item) -> dict:
        return describe(plant, item)

    crew = plant.crew.snapshot(desc)
    fleet = plant.fleet.snapshot(desc, keep_s)
    return {
        **crew,
        **fleet,
        "rack": [{**desc(i), "at": round(i.at, 1)} for i in plant.inrack[:QUEUE_SHOWN]], "rack_n": len(plant.inrack),
        "ls": plant.supply.snapshot(),
        **plant.shipping.snapshot(),
        "fgw": {material: [p.serial for p in queue] for material, queue in plant.stock.items()},
        "v": 1, "run": plant.run_id, "t": round(now, 1), "speed": plant.speed, "ctl": plant.control_state(),
        "stations": stations,
        "bufs": bufs,
        "tr": rides,
        "trh": ended,
        "feed": {code: _queue(plant, list(store.items)) for code, store in plant.feed.items()},
        "panels": {code: int(rack.level) for code, rack in plant.panel_rack.items()},
        "sm": {code: len(store.items) for code, store in plant.supermarket.items()},
        "fg": plant.warehouse,
        "quar": len(plant.quarantined),
        "scrap": dict(plant.scrapped),
        "washing": plant.washing,
    }
