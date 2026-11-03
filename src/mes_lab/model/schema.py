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
    spc: bool = False              # the MES keeps a control chart of it (see mes/spc.py)
    drift_per_h: float = 0.0       # the process wanders off this much per operating hour (wear, clogging),
                                   # until a repair, the planned maintenance or an adjustment sets it back


class Verification(Strict):
    """A tester checks itself with a master sample (a known good unit) before it may test production:
    at the start of every shift, the operator puts the sample in; the MES lets the tester work only after
    a passed check of this shift."""
    duration_s: float = Field(120.0, gt=0)
    fail_p: float = Field(0.03, ge=0, le=1)   # the check fails (fixture, probes): maintenance, then again


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
    verification: Verification | None = None

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
    city: str = ""
    supplier_no: str = ""          # our number in the customer's books (the V field of the box label)
    dock: str = ""                 # where the customer unloads our goods (unloading point / dock)


class Operation(Strict):
    op: str
    name: str
    steps: dict[str, float]
    components: dict[str, int] = {}      # SMT: part -> pieces per board placed in this pass

    @property
    def bottleneck_s(self) -> float:
        return max(self.steps.values())


class Packing(Strict):
    klt_qty: int = Field(gt=0)
    klt_per_pallet: int = Field(gt=0)
    klt_mm: tuple[int, int, int] = (400, 300, 147)     # length, width, height of the KLT box
    pallet_mm: tuple[int, int] = (1200, 800)           # footprint of the pallet
    pallet_type: str = "EUR"
    unit_g: float = Field(150.0, gt=0)                 # one unit, for the net weight on the labels
    klt_g: float = Field(1500.0, ge=0)                 # the empty KLT box (tare)

    @property
    def pallet_kg(self) -> float:
        """The empty pallet: EUR about 25 kg, CP1 (chemical industry pallet) about 15 kg."""
        return {"EUR": 25.0, "CP1": 15.0}.get(self.pallet_type, 20.0)


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
    customer_part: str = ""                            # FG: the customer's part number (labels, call-offs)
    change_level: str = ""                             # FG: the drawing index the customer released
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
    added: bool = False                                # made on the settings page of the MES (not in config/)


class SeatRule(Strict):
    code: str
    home: str                      # "desk" or a station type
    attends: list[str]
    skill: str | None = None       # the qualification a person needs for this seat (default: the area)
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
    material: int = Field(0, ge=0)          # material handlers: goods-in of the reels and the milk run to SMT


class WcRule(Strict):
    trips: int = Field(2, ge=0)    # per person and shift
    minutes: float = Field(3.0, gt=0)
    wash_s: float = Field(10.0, ge=0)   # washing the hands at the basin afterwards


class AndonRule(Strict):
    foreman_min: float = Field(15.0, gt=0)      # a breakdown open this long: the foreman comes (level 2)
    manager_min: float = Field(60.0, gt=0)      # ... and this long: the production manager (level 3)
    meeting_at_min: float = Field(10.0, ge=0)   # the shift meeting, minutes after the start of the shift
    meeting_min: float = Field(5.0, gt=0)


class SkillRules(Strict):
    certified: list[str] = []          # skills with a certificate that runs out (eye test, IPC-7711/7721)
    valid_days: float = Field(365.0, gt=0)
    expiring_share: float = Field(0.04, ge=0, le=1)   # certificates that run out within days of the start
    course_days: float = Field(3.0, ge=0)   # a lapsed certificate (not of the own seat) is renewed this much later


class AbsenceRules(Strict):
    sick: float = Field(0.03, ge=0, le=1)      # per person and shift
    holiday: float = Field(0.05, ge=0, le=1)


class LoungeRule(Strict):
    after_min: float = Field(5.0, ge=0)  # logistics: idle at the dock this long, then they sit down at the table


class RoundsRule(Strict):
    """Rounds between the calls: an operator of a desk seat (and a relief person) checks a machine of the line,
    a maintenance technician walks an inspection round. 0 minutes: no rounds."""
    operator_min: float = Field(25.0, ge=0)      # how often an operator checks a machine
    check_min: float = Field(2.0, gt=0)          # how long a check takes
    maintenance_min: float = Field(60.0, ge=0)   # how often a technician makes an inspection round
    stops: int = Field(3, ge=1)                  # machines of an inspection round
    inspect_min: float = Field(4.0, gt=0)        # at each


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
    rounds: RoundsRule = RoundsRule()
    andon: AndonRule = AndonRule()
    skills: SkillRules = SkillRules()
    absence: AbsenceRules = AbsenceRules()


class SupplyConfig(Strict):
    """Line-side material that the robots bring (two-bin kanban): when a bin runs empty, the next one
    is opened and a full one is ordered from the goods-in warehouse."""
    kit_bins: int = Field(3, ge=2, le=6)        # bins of parts in the rack at the loader of a final assembly line
    box_stacks: int = Field(2, ge=1, le=4)      # stacks of empty KLT boxes at the scan station of packing
    boxes_per_stack: int = Field(8, ge=1)


class SmtPart(Strict):
    """A component the SMT lines place (config/smt_material.yaml)."""
    code: str
    name: str
    machine: str                         # the placer type that places it
    reel: int = Field(gt=0)              # pieces on a full reel
    msl: str | None = None               # moisture sensitivity level (J-STD-033)
    supplier: str | None = None
    per_day: float = Field(5.0, gt=0)    # planned use, reels a day (purchasing orders from it)


class ReelRules(Strict):
    splice_warn_min: float = Field(20.0, gt=0)   # the operator is called when a reel has this much left
    splice_s: float = Field(75.0, gt=0)
    wrong_reel_p: float = Field(0.015, ge=0, le=1)
    lot_reels: int = Field(24, ge=1)             # reels of one supplier lot


class PasteRules(Strict):
    name: str = "solder paste"
    thaw_h: float = Field(4.0, ge=0)             # out of the fridge at least this long before it is opened
    open_h: float = Field(12.0, gt=0)            # open on the printer at most this long (the MES blocks it)
    prints: int = Field(700, gt=0)               # prints from one jar
    change_s: float = Field(240.0, gt=0)
    early_p: float = Field(0.02, ge=0, le=1)     # a jar taken before it thawed (the MES refuses it)
    cabinet: int = Field(8, ge=1)                # jars the thaw cabinet holds


class MslRules(Strict):
    floor_h: dict[str, float] = {}               # floor life out of the dry bag, hours per level
    bake_h: float = Field(24.0, gt=0)


class ReceivingRules(Strict):
    unload_box_s: float = Field(35.0, gt=0)      # a box from the truck to the receiving desk
    box_reels: int = Field(8, ge=1)              # reels of one part in a box
    scan_reel_s: float = Field(15.0, gt=0)       # scan, label, into the store
    pick_s: float = Field(12.0, gt=0)            # out of the store for a line, scanned
    drop_s: float = Field(8.0, gt=0)             # into the rack at the line
    milk_run_min: float = Field(30.0, gt=0)
    wrong_part_p: float = Field(0.01, ge=0, le=1)
    fifo_p: float = Field(0.01, ge=0, le=1)
    order_days: float = Field(1.0, gt=0)         # an order covers this many days of use
    safety_days: float = Field(0.5, ge=0)        # stock beyond the use during the lead time
    start_days: float = Field(2.0, ge=0)         # the store at the start of a run


class Supplier(Strict):
    code: str
    name: str
    country: str = "DE"
    lead_h: float = Field(24.0, ge=0)            # from the order to the first truck that may bring it
    trucks: list[time]                           # local time of the trucks, every day


class SmtMaterialConfig(Strict):
    reels: ReelRules = ReelRules()
    receiving: ReceivingRules = ReceivingRules()
    suppliers: dict[str, Supplier] = {}
    paste: PasteRules = PasteRules()
    msl: MslRules = MslRules()
    parts: dict[str, SmtPart] = {}


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


LABEL_KINDS = ("KLT", "PALLET")
BARCODES = ("code39", "code128")


class LabelField(Strict):
    """One box of a label, in millimetres from the top left corner. `value` is text with {placeholders}
    (mes_lab.labels.PLACEHOLDERS); a field with a barcode prints the value as a bar code under the text,
    after its data identifier `di` (VDA 4902: P part number, Q quantity, V supplier, S package, H batch)."""
    title: str = ""
    value: str = ""
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    size: float = Field(3.0, ge=1, le=20)        # height of the text in mm
    bold: bool = False
    barcode: Literal["code39", "code128"] | None = None
    di: str = ""
    show: bool = True


class LabelTemplate(Strict):
    name: str
    kind: Literal["KLT", "PALLET"]
    size_mm: tuple[float, float]
    fields: dict[str, LabelField]


class LabelSupplier(Strict):
    name: str = "ACME Automotive Electronics"
    address: str = ""


class LabelConfig(Strict):
    """config/labels.yaml: who we are on the labels and the label templates (one per container kind)."""
    supplier: LabelSupplier = LabelSupplier()
    templates: dict[str, LabelTemplate] = {}


class CallOffRules(Strict):
    at: str = "06:00"                          # local time the customers send their call-offs
    days: int = Field(10, ge=1, le=30)         # daily quantities this many days ahead
    firm_days: int = Field(2, ge=1)            # a new call-off does not change these (today, tomorrow)
    load: float = Field(0.8, gt=0, le=1.5)     # demand = this share of the ideal rate of the final assembly lines
    spread: float = Field(0.15, ge=0, lt=1)    # day to day +- this share


class MrpRules(Strict):
    every_min: int = Field(60, ge=1)
    horizon_days: int = Field(2, ge=1)         # demand due within this many days (today included) gets orders


class InterfaceRules(Strict):
    delay_s: float = Field(0.0, ge=0)
    lost_ack: float = Field(0.02, ge=0, lt=1)
    maintenance: list[str] = ["01:30", "02:00"]    # from, to (local time); [] = never down
    retry_min: list[float] = [1, 2, 5, 10, 15, 30]
    lock_chance: float = Field(0.25, ge=0, le=1)
    lock_min: list[float] = [20, 60]
    key_user_at: list[str] = ["08:00", "14:00"]


class ErpConfig(Strict):
    """The fake ERP (config/erp.yaml): customer call-offs, MRP, production orders and the interface."""
    enabled: bool = True
    call_offs: CallOffRules = CallOffRules()
    mrp: MrpRules = MrpRules()
    interface: InterfaceRules = InterfaceRules()
    stock_count_at: str = "05:00"


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
    smt_material: SmtMaterialConfig = SmtMaterialConfig()
    erp: ErpConfig = ErpConfig()
    labels: LabelConfig = LabelConfig()

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
