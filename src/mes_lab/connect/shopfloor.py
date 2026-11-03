"""What the machines show right now, the same for OPC UA and for the Unified Namespace.

The names follow ISA-95: enterprise (acme) / site (plant1) / area (smt, fa, pack, rpr) / line (smt-01) /
station (pp1, the station code without its line). Lower case in the UNS (topic paths), the codes as they are
in OPC UA (browse names).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mes_lab.model.schema import PlantModel
    from mes_lab.sim.plant import PlantSim

ENTERPRISE, SITE = "acme", "plant1"
ROOT = f"{ENTERPRISE}/{SITE}"


def station_tag(station_code: str, line_code: str) -> str:
    return station_code[len(line_code) + 1:] if station_code.startswith(line_code + "-") else station_code


def topic_of(model: PlantModel, line: str, station: str | None = None) -> str:
    """The UNS path of a line or a station (without the attribute)."""
    spec = model.lines[line]
    path = f"{ROOT}/{spec.area.lower()}/{line.lower()}"
    return f"{path}/{station_tag(station, line).lower()}" if station else path


def views(plant: PlantSim) -> dict[str, Any]:
    """{"plant": {...}, "lines": {code: {...}}, "stations": {code: {...}}} - plain values (str, int, float,
    bool, None), the times as ISO strings of plant time."""
    clock, total = plant.clock, plant.edge.total_count
    stations: dict[str, dict] = {}
    for line in plant.lines.values():
        for cell in line.cells:
            for st in cell.stations:
                job = st.current_job()
                counts = total.get(st.code, [0, 0, 0, 0])
                stations[st.code] = {
                    "state": st.state.value, "reason": st.reason, "since": clock.iso(st.since),
                    "serial": st.item_label(), "order": job.order_no if job else None,
                    "material": job.material.code if job else None,
                    "cycles": counts[0], "good": counts[1], "bad": counts[2],
                    "cycle_s": st.last_cycle_s, "result": st.last_result, "values": dict(st.last_values),
                }
    lines = {}
    for code, line in plant.lines.items():
        job = line.active_job or (line.loader.current_job() if line.loader is not None else None)
        lines[code] = {
            "order": job.order_no if job else None, "material": job.material.code if job else None,
            "qty": job.expected if job else None, "released": job.released if job else None,
            "good": job.good if job else None, "wip": len(line.wip) if line.is_job else None,
            "no_order": bool(line.no_order) if line.is_job else False,
        }
    return {
        "plant": {"plant_time": clock.iso(plant.env.now), "speed": plant.speed, "run_id": plant.run_id,
                  "paused": bool(plant.paused)},
        "lines": lines, "stations": stations,
    }


def units(model: PlantModel) -> dict[str, dict[str, str]]:
    """station type -> measurement -> unit."""
    return {code: {name: m.unit for name, m in t.measurements.items()} for code, t in model.station_types.items()}
