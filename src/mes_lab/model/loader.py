"""Load the plant model from the YAML files in config/ and check that every
reference points somewhere (station types, defect codes, materials, lines)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .schema import (
    JOB_AREAS,
    AmrConfig,
    Area,
    AreaKind,
    Cell,
    CodeEntry,
    CostConfig,
    CrewConfig,
    Customer,
    Line,
    Material,
    PlantInfo,
    PlantModel,
    Shift,
    ShippingConfig,
    Station,
    StationKind,
    StationType,
)

CONFIG_FILES = ("plant.yaml", "station_types.yaml", "products.yaml", "codes.yaml", "crew.yaml", "costs.yaml")


class ModelError(ValueError):
    """The plant configuration is inconsistent. Lists every problem found."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Plant model is invalid:\n  - " + "\n  - ".join(problems))


def _read(config_dir: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for name in CONFIG_FILES:
        path = config_dir / name
        with path.open(encoding="utf-8") as fh:
            content = yaml.safe_load(fh) or {}
        overlap = set(content) & set(data)
        if overlap:
            raise ModelError([f"{name}: section(s) {sorted(overlap)} defined twice"])
        data.update(content)
    return data


def _expand_template(line_cfg: dict, template: dict, area: Area,
                     types: dict[str, StationType]) -> Line:
    code = line_cfg["code"]
    cells: list[Cell] = []
    for index, entry in enumerate(template["stations"]):
        if isinstance(entry, str):
            type_code, count = entry, 1
        else:
            type_code, count = entry["type"], int(entry.get("count", 1))
        stype = types.get(type_code)
        tag = stype.tag if stype else type_code[:3]
        name = stype.name if stype else type_code
        stations = []
        for slot in range(1, count + 1):
            suffix = f"{tag}{slot}" if count > 1 else tag
            stations.append(Station(
                code=f"{code}-{suffix}",
                name=f"{name} #{slot}" if count > 1 else name,
                type=type_code, line=code, cell=index, slot=slot,
            ))
        cells.append(Cell(index=index, type=type_code, stations=stations))
    return Line(
        code=code,
        name=line_cfg.get("name", code),
        area=area.code,
        area_kind=area.kind,
        buffer=int(template.get("buffer", 2)),
        output=template["output"],
        materials=list(line_cfg.get("materials", [])),
        cells=cells,
    )


def build_model(data: dict[str, Any]) -> PlantModel:
    problems: list[str] = []

    types = {code: StationType(code=code, **cfg) for code, cfg in data["station_types"].items()}
    defect_codes = {c: CodeEntry(code=c, **v) for c, v in data["defect_codes"].items()}
    reasons = {c: CodeEntry(code=c, **v) for c, v in data["downtime_reasons"].items()}
    customers = {c["code"]: Customer(**c) for c in data["customers"]}
    materials = {m["code"]: Material(**m) for m in data["materials"]}
    templates = data["line_templates"]

    areas: list[Area] = []
    lines: dict[str, Line] = {}
    for area_cfg in data["areas"]:
        area = Area(code=area_cfg["code"], name=area_cfg["name"], kind=area_cfg["kind"],
                    lines=[l["code"] for l in area_cfg.get("lines", [])])
        areas.append(area)
        for line_cfg in area_cfg.get("lines", []):
            template = templates.get(line_cfg["template"])
            if template is None:
                problems.append(f"line {line_cfg['code']}: unknown template {line_cfg['template']}")
                continue
            if line_cfg["code"] in lines:
                problems.append(f"line {line_cfg['code']} defined twice")
            lines[line_cfg["code"]] = _expand_template(line_cfg, template, area, types)

    model = PlantModel(
        plant=PlantInfo(**data["plant"]),
        shifts=[Shift(**s) for s in data["shifts"]],
        station_types=types,
        defect_codes=defect_codes,
        downtime_reasons=reasons,
        customers=customers,
        materials=materials,
        areas=areas,
        lines=lines,
        crew=CrewConfig(**data["crew"]),
        shipping=ShippingConfig(**data["shipping"]),
        amr=AmrConfig(**(data.get("amr") or {})),
        costs=CostConfig(**data["costs"]) if data.get("costs") else None,
    )
    problems += _validate(model)
    if problems:
        raise ModelError(problems)
    return model


def _validate(m: PlantModel) -> list[str]:
    p: list[str] = []

    # station types ---------------------------------------------------------
    for t in m.station_types.values():
        for reason in t.failures:
            if reason not in m.downtime_reasons:
                p.append(f"station type {t.code}: unknown failure reason {reason}")
        for code in list(t.defects) + list(t.detects):
            if code not in m.defect_codes:
                p.append(f"station type {t.code}: unknown defect code {code}")
        if t.mtbf_h and not t.failures:
            p.append(f"station type {t.code}: mtbf_h set but no failure reasons")
        if t.kind == StationKind.OVEN and t.transit_s <= 0:
            p.append(f"station type {t.code}: oven needs transit_s")
        if t.kind == StationKind.PACK and t.container is None:
            p.append(f"station type {t.code}: pack station needs container")
        if t.kind == StationKind.REPAIR and t.repair is None:
            p.append(f"station type {t.code}: repair station needs repair parameters")

    # lines -------------------------------------------------------------------
    seen_stations: set[str] = set()
    for line in m.lines.values():
        for cell in line.cells:
            if cell.type not in m.station_types:
                p.append(f"line {line.code}: unknown station type {cell.type}")
            for st in cell.stations:
                if st.code in seen_stations:
                    p.append(f"station code {st.code} is not unique")
                seen_stations.add(st.code)
        types_in_line = line.cell_types()
        if line.output not in types_in_line:
            p.append(f"line {line.code}: output station type {line.output} is not on the line")
        first = m.station_types.get(types_in_line[0]) if types_in_line else None
        if line.is_job_line:
            if not first or first.kind != StationKind.LOADER:
                p.append(f"line {line.code}: a job line must start with a loader")
        elif any(m.station_types.get(t) and m.station_types[t].kind == StationKind.LOADER
                 for t in types_in_line):
            p.append(f"line {line.code}: flow lines have no loader")
        for mat in line.materials:
            if mat not in m.materials:
                p.append(f"line {line.code}: unknown material {mat}")
            elif m.materials[mat].kind != "FG":
                p.append(f"line {line.code}: {mat} is not a finished good")

    # materials -------------------------------------------------------------------
    smt_lines = m.lines_of_kind(AreaKind.SMT)
    for mat in m.materials.values():
        for op in mat.routing:
            for type_code, cycle in op.steps.items():
                if type_code not in m.station_types:
                    p.append(f"{mat.code}/{op.op}: unknown station type {type_code}")
                if cycle <= 0:
                    p.append(f"{mat.code}/{op.op}: cycle time of {type_code} must be > 0")
            if not any(m.station_types.get(t) and m.station_types[t].kind == StationKind.LOADER
                       for t in op.steps):
                p.append(f"{mat.code}/{op.op}: the first step must be a loader")
        if mat.kind == "PCBA":
            ops = [o.op for o in mat.routing]
            if not ops or not all(o.startswith("SMT_") for o in ops):
                p.append(f"{mat.code}: PCBA routing must consist of SMT_ operations")
            for i, op in enumerate(mat.routing):
                has_depanel = "DEPANELER" in op.steps
                if has_depanel != (i == len(mat.routing) - 1):
                    p.append(f"{mat.code}/{op.op}: only the last SMT pass is depaneled")
            pl = mat.planning
            if not (0 < pl.min_lot <= pl.max_lot):
                p.append(f"{mat.code}: planning needs 0 < min_lot <= max_lot")
            for line in smt_lines:
                missing = set().union(*(o.steps for o in mat.routing)) - set(line.cell_types())
                if missing:
                    p.append(f"{mat.code}: SMT line {line.code} lacks {sorted(missing)}")
        else:
            if mat.customer not in m.customers:
                p.append(f"{mat.code}: unknown customer {mat.customer}")
            if mat.pcba not in m.materials or m.materials[mat.pcba].kind != "PCBA":
                p.append(f"{mat.code}: pcba {mat.pcba} is not a PCBA material")
            if mat.packing is None:
                p.append(f"{mat.code}: finished good needs packing")
            if not mat.planning.lot_sizes:
                p.append(f"{mat.code}: finished good needs planning.lot_sizes")
            if [o.op for o in mat.routing] != ["FA"]:
                p.append(f"{mat.code}: finished good routing must be a single FA operation")
            fa_lines = [l for l in m.lines_of_kind(AreaKind.FA) if mat.code in l.materials]
            if not fa_lines:
                p.append(f"{mat.code}: no final assembly line builds it")
            for line in fa_lines:
                missing = set(mat.routing[0].steps) - set(line.cell_types())
                if missing:
                    p.append(f"{mat.code}: line {line.code} lacks {sorted(missing)}")
            if not any(mat.code in l.materials for l in m.lines_of_kind(AreaKind.PACK)):
                p.append(f"{mat.code}: no packing line packs it")

    if not m.lines_of_kind(AreaKind.REPAIR):
        p.append("the plant needs a repair line")

    # sizes -------------------------------------------------------------------------
    for mat in m.materials.values():
        if mat.panel_grid is not None and mat.panel_grid[0] * mat.panel_grid[1] != mat.boards_per_panel:
            p.append(f"{mat.code}: panel_grid {list(mat.panel_grid)} does not hold {mat.boards_per_panel} boards")

    # people and trucks ---------------------------------------------------------------
    area_kinds = {a.kind.value for a in m.areas if a.lines}
    for kind, seats in m.crew.seats.items():
        if kind not in {k.value for k in AreaKind}:
            p.append(f"crew.seats: unknown area kind {kind}")
        codes = [s.code for s in seats]
        if len(set(codes)) != len(codes):
            p.append(f"crew.seats.{kind}: seat codes are not unique")
        for seat in seats:
            for t in [seat.home] + seat.attends:
                if t not in ("desk", "*") and t not in m.station_types:
                    p.append(f"crew.seats.{kind}.{seat.code}: unknown station type {t}")
    for kind in ("SMT", "FA", "PACK", "REPAIR"):
        if kind in area_kinds and kind not in m.crew.seats:
            p.append(f"crew.seats has no seats for the {kind} lines")
    shift_codes = {s.code for s in m.shifts}
    if set(m.crew.shifts) != shift_codes:
        p.append(f"crew.shifts must cover the shifts {sorted(shift_codes)}")
    for truck in m.shipping.trucks:
        if truck.customer not in m.customers:
            p.append(f"shipping: unknown customer {truck.customer}")
        for at in truck.at:
            try:
                hh, mm = at.split(":")
                assert 0 <= int(hh) < 24 and 0 <= int(mm) < 60
            except (ValueError, AssertionError):
                p.append(f"shipping: {truck.customer}: bad time {at!r}")
    served = {t.customer for t in m.shipping.trucks}
    for mat in m.finished_goods():
        if mat.customer not in served:
            p.append(f"{mat.code}: no truck is scheduled for customer {mat.customer}")
    if len({t.bay for t in m.shipping.trucks}) != len({t.customer for t in m.shipping.trucks}):
        p.append("shipping: every customer has its own bay")
    if m.costs is not None:
        goods = {mat.code for mat in m.finished_goods()}
        for name, table in (("material", m.costs.material), ("price", m.costs.price)):
            for code in table:
                if code not in goods:
                    p.append(f"costs.{name}: {code} is not a finished good")
            for code in goods - set(table):
                p.append(f"costs.{name}: no value for {code}")
        kinds = {a.kind.value for a in m.areas}
        for name, table in (("line_per_h", m.costs.line_per_h), ("energy_per_h", m.costs.energy_per_h)):
            for kind in table:
                if kind not in kinds:
                    p.append(f"costs.{name}: unknown area kind {kind}")
        roles = {"OPERATOR", "MAINTENANCE", "IT", "FOREMAN", "LOGISTICS", "MANAGER"}
        for role in m.costs.labor_per_h:
            if role not in roles:
                p.append(f"costs.labor_per_h: unknown role {role}")
    for mat in m.finished_goods():
        if mat.kit is not None and not any(mat.code in l.materials for l in m.lines_of_kind(AreaKind.FA)):
            p.append(f"{mat.code}: has parts (kit) but no final assembly line")
    return p


def load_model(config_dir: str | Path | None = None) -> PlantModel:
    from mes_lab import DEFAULT_CONFIG_DIR

    return build_model(_read(Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR))
