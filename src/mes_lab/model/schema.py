"""Plant model: the static description of the plant (ISA-95 equipment and
material model), loaded from YAML and shared by the simulation and the MES."""

from __future__ import annotations

from datetime import time
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StationKind(StrEnum):
    LOADER = "loader"
    PROCESS = "process"
    INSPECTION = "inspection"
    OVEN = "oven"
    DEPANEL = "depanel"
    PACK = "pack"
    REPAIR = "repair"


class AreaKind(StrEnum):
    SMT = "SMT"
    FA = "FA"
    PACK = "PACK"
    REPAIR = "REPAIR"
    WAREHOUSE = "WAREHOUSE"


JOB_AREAS = {AreaKind.SMT, AreaKind.FA}


class Microstop(Strict):
    p: float = Field(0.0, ge=0, le=1)
    mean_s: float = Field(30.0, gt=0)


class PreventiveMaintenance(Strict):
    interval_h: float = Field(gt=0)
    duration_min: float = Field(gt=0)


class DefectSpec(Strict):
    p: float = Field(ge=0, le=1)
    self_detect: float = Field(0.0, ge=0, le=1)


class RepairSpec(Strict):
    mean_min: float = Field(gt=0)
    cv: float = Field(0.5, ge=0)
    success: float = Field(ge=0, le=1)


class MeasurementSpec(Strict):
    mean: float
    sd: float = Field(gt=0)
    lsl: float
    usl: float
    unit: str = ""


class StationType(Strict):
    code: str
    name: str
    tag: str
    kind: StationKind
    cycle_cv: float = Field(0.03, ge=0)
    default_cycle_s: float | None = None
    mtbf_h: float | None = None
    mttr_min: float | None = None
    mttr_cv: float = 0.6
    microstop: Microstop = Microstop()
    changeover_min: float = 0.0
    pm: PreventiveMaintenance | None = None
    failures: list[str] = []
    defects: dict[str, DefectSpec] = {}
    detects: dict[str, float] = {}
    false_call_rate: float = Field(0.0, ge=0, le=1)
    verify_s: float = 0.0
    on_reject: Literal["repair", "wash", "bad_mark"] = "repair"
    wash_min: float = 10.0
    transit_s: float = 0.0
    capacity: int = Field(1, ge=1)
    container: Literal["klt", "pallet"] | None = None
    repair: RepairSpec | None = None
    interlock: bool = False  # checks items in with the MES before working on them
    manual: bool = False     # a person does the work (loading, visual inspection, packing, repair)
    measurements: dict[str, MeasurementSpec] = {}

    @field_validator("defects", mode="before")
    @classmethod
    def _short_defects(cls, value):
        # "CODE: 0.001" is shorthand for "CODE: {p: 0.001}"
        if isinstance(value, dict):
            return {k: ({"p": v} if isinstance(v, (int, float)) else v) for k, v in value.items()}
        return value


class Shift(Strict):
    code: str
    name: str
    start: time
    end: time


class Customer(Strict):
    code: str
    name: str
    country: str


class Operation(Strict):
    op: str
    name: str
    steps: dict[str, float]

    @property
    def bottleneck_s(self) -> float:
        return max(self.steps.values())


class Packing(Strict):
    klt_qty: int = Field(gt=0)
    klt_per_pallet: int = Field(gt=0)
    klt_mm: tuple[int, int, int] = (400, 300, 147)     # length, width, height of the KLT box
    pallet_mm: tuple[int, int] = (1200, 800)           # footprint of the pallet
    pallet_type: str = "EUR"


class Planning(Strict):
    safety_stock: int = 0
    initial_stock: int = 0
    min_lot: int = 0
    max_lot: int = 0
    lot_sizes: list[int] = []


class Kit(Strict):
    """FG: the parts final assembly puts on one unit (housing, connector, screws), a set per unit.
    They come in bins to the rack at the loader (see AmrConfig.supply)."""
    name: str
    per_bin: int = Field(gt=0)


class Material(Strict):
    code: str
    name: str
    kind: Literal["PCBA", "FG"]
    customer: str | None = None
    pcba: str | None = None
    boards_per_panel: int = 1
    panel_mm: tuple[int, int] = (280, 220)             # PCBA: the panel ...
    panel_grid: tuple[int, int] | None = None          # ... and its boards (across, down)
    unit_mm: tuple[int, int, int] = (120, 90, 35)      # FG: the finished product
    packing: Packing | None = None
    kit: Kit | None = None                             # FG: the parts of a unit, brought to the line in bins
    planning: Planning = Planning()
    routing: list[Operation]

    def operation(self, op: str) -> Operation:
        for operation in self.routing:
            if operation.op == op:
                return operation
        raise KeyError(f"{self.code} has no operation {op}")

    def op_index(self, op: str) -> int:
        return [o.op for o in self.routing].index(op)


class Station(Strict):
    code: str
    name: str
    type: str
    line: str
    cell: int
    slot: int


class Cell(Strict):
    index: int
    type: str
    stations: list[Station]


class Line(Strict):
    code: str
    name: str
    area: str
    area_kind: AreaKind
    buffer: int
    output: str
    materials: list[str] = []
    cells: list[Cell]

    @property
    def is_job_line(self) -> bool:
        return self.area_kind in JOB_AREAS

    @property
    def stations(self) -> list[Station]:
        return [s for cell in self.cells for s in cell.stations]

    def cell_types(self) -> list[str]:
        return [cell.type for cell in self.cells]


class Area(Strict):
    code: str
    name: str
    kind: AreaKind
    lines: list[str]


class CodeEntry(Strict):
    code: str
    name: str
    category: str
    crew: Literal["MAINTENANCE", "IT"] | None = None   # downtime reasons: who comes to fix it
    needs: Literal["meter"] | None = None              # the technician fetches measuring instruments first


class SeatRule(Strict):
    code: str
    home: str                      # "desk" or a station type
    attends: list[str]
    fixed: bool = False
    per_station: bool = False      # one seat for every station of the home type (repair benches)


class BreakRule(Strict):
    name: str
    kind: Literal["coffee", "lunch"] = "lunch"     # coffee: at the coffee machine; lunch: a chair in the break room
    start: int = Field(ge=0)       # minutes after the start of the shift
    length: int = Field(gt=0)      # including the walks


class ShiftStaff(Strict):
    relief: int = Field(ge=0)
    maintenance: int = Field(ge=0)
    it: int = Field(ge=0)
    foreman: int = Field(ge=0)
    logistics: int = Field(ge=0)
    manager: int = Field(0, ge=0, le=1)     # the production manager (day shift): the third level of the andon


class WcRule(Strict):
    trips: int = Field(2, ge=0)    # per person and shift
    minutes: float = Field(3.0, gt=0)
    wash_s: float = Field(10.0, ge=0)   # washing the hands at the basin afterwards


class AndonRule(Strict):
    foreman_min: float = Field(15.0, gt=0)      # a breakdown open this long: the foreman comes (level 2)
    manager_min: float = Field(60.0, gt=0)      # ... and this long: the production manager (level 3)
    meeting_at_min: float = Field(10.0, ge=0)   # the shift meeting, minutes after the start of the shift
    meeting_min: float = Field(5.0, gt=0)


class LoungeRule(Strict):
    after_min: float = Field(5.0, ge=0)  # logistics: idle at the dock this long, then they sit down at the table


class EsdRule(Strict):
    gates: int = Field(2, ge=1)
    check_s: float = Field(6.0, gt=0)   # one person on a gate (wrist strap and footwear test)
    coat_s: float = Field(20.0, ge=0)   # putting on an ESD coat in the lockers


class CrewConfig(Strict):
    walk_speed: float = Field(25.0, gt=0)
    seats: dict[str, list[SeatRule]]
    shifts: dict[str, ShiftStaff]
    breaks: list[BreakRule]
    wc: WcRule = WcRule()
    esd: EsdRule = EsdRule()
    women: dict[str, float] = Field(default_factory=dict)   # share of women per role (OPERATOR, MAINTENANCE, ...)
    lounge: LoungeRule = LoungeRule()
    andon: AndonRule = AndonRule()


class SupplyConfig(Strict):
    """Line-side material that the robots bring (two-bin kanban): when a bin runs empty, the next one
    is opened and a full one is ordered from the goods-in warehouse."""
    kit_bins: int = Field(3, ge=2, le=6)        # bins of parts in the rack at the loader of a final assembly line
    box_stacks: int = Field(2, ge=1, le=4)      # stacks of empty KLT boxes at the scan station of packing
    boxes_per_stack: int = Field(8, ge=1)


class AmrConfig(Strict):
    """Autonomous mobile robots: the repair and quarantine loop and the line-side material."""
    robots: int = Field(4, ge=1, le=8)
    speed: float = Field(30.0, gt=0)            # plan units per second (about 0.8 m/s among people)
    capacity: int = Field(4, ge=1)              # totes on the top module
    transfer_s: float = Field(8.0, gt=0)        # docking at a stop and moving the tote
    battery_h: float = Field(6.0, gt=0)         # hours of driving (empty) on a full battery
    load_drain: float = Field(0.25, ge=0)       # driving with all places loaded uses this much more
    idle_drain: float = Field(0.3, ge=0)        # standing (computer, scanners, radio) uses this share of driving
    charge_h: float = Field(1.2, gt=0)          # empty to full on the charger
    low: float = Field(25.0, ge=5, le=80)       # below this (%) a robot charges before it takes the next order
    supply: SupplyConfig = SupplyConfig()


class CostConfig(Strict):
    """What the plant costs and earns (config/costs.yaml): the cost of a good unit, the tuning game."""
    currency: str = "EUR"
    labor_per_h: dict[str, float]                # per role (OPERATOR, MAINTENANCE, IT, FOREMAN, LOGISTICS, MANAGER)
    robot_per_h: float = Field(ge=0)
    line_per_h: dict[str, float]                 # per area kind (SMT, FA, PACK, REPAIR)
    energy_per_h: dict[str, float]               # per area kind, while the machines run
    material: dict[str, float]                   # per finished good
    price: dict[str, float]                      # per finished good
    hold_per_h: float = Field(0.0, ge=0)


class TruckSchedule(Strict):
    customer: str
    bay: int = Field(ge=0)
    capacity: int = Field(gt=0)
    min_pallets: int = Field(1, ge=0)
    at: list[str]                  # local times "HH:MM"


class ShippingConfig(Strict):
    load_s: float = Field(60.0, gt=0)
    yard_speed: float = Field(5.0, gt=0)
    trucks: list[TruckSchedule]


class PlantInfo(Strict):
    code: str
    name: str
    company: str
    timezone: str
    plan_efficiency: float = Field(0.85, gt=0, le=1)    # the hour-by-hour plan: ideal rate of the constraint x this


class PlantModel(Strict):
    plant: PlantInfo
    shifts: list[Shift]
    station_types: dict[str, StationType]
    defect_codes: dict[str, CodeEntry]
    downtime_reasons: dict[str, CodeEntry]
    customers: dict[str, Customer]
    materials: dict[str, Material]
    areas: list[Area]
    lines: dict[str, Line]
    crew: CrewConfig
    shipping: ShippingConfig
    amr: AmrConfig = AmrConfig()
    costs: CostConfig | None = None

    # ---- convenience lookups -------------------------------------------------
    @property
    def stations(self) -> dict[str, Station]:
        return {s.code: s for line in self.lines.values() for s in line.stations}

    def area(self, code: str) -> Area:
        return next(a for a in self.areas if a.code == code)

    def lines_of_kind(self, kind: AreaKind) -> list[Line]:
        return [line for line in self.lines.values() if line.area_kind == kind]

    def pcbas(self) -> list[Material]:
        return [m for m in self.materials.values() if m.kind == "PCBA"]

    def finished_goods(self) -> list[Material]:
        return [m for m in self.materials.values() if m.kind == "FG"]

    def pack_line_for(self, material: str) -> Line:
        for line in self.lines_of_kind(AreaKind.PACK):
            if material in line.materials:
                return line
        raise KeyError(f"no packing line for {material}")

    def fg_using(self, pcba: str) -> list[Material]:
        return [m for m in self.finished_goods() if m.pcba == pcba]
