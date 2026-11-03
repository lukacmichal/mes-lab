"""Request bodies of the shop-floor API (edge gateway, dispatching, traceability)
and of the web actions (holds, dispositions)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RunRegistration(BaseModel):
    run_id: str = Field(min_length=8, max_length=36)
    seed: int = 1
    speed: float | None = None
    initial_stock: dict[str, list[str]] = {}  # boards already in the supermarket
    lines: list[str] | None = None            # the lines this plant has (a tuning scenario may have fewer)
    knobs: dict | None = None                 # the knobs of the tuning game it was built with (None: as configured)
    plant_start: datetime | None = None       # start at this time (midnight of the real day), not after the last run
    settings_rev: int | None = None           # the revision of the settings it was built with (None: as configured)


class SettingChangeRequest(BaseModel):
    path: str = Field(min_length=3, max_length=200)
    value: Any = None
    reset: bool = False                       # back to the value of the config files


class SettingReleaseRequest(BaseModel):
    reason: str = Field(max_length=300)
    when: Literal["now", "shift"] = "shift"


class SettingScheduledRequest(BaseModel):
    action: Literal["now", "withdraw"]


ROLE = Literal["ADMIN", "ENGINEER", "QUALITY", "SUPERVISOR"]


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]{2,39}$")
    display_name: str = Field("", max_length=80)
    role: ROLE = "SUPERVISOR"


class UserUpdate(BaseModel):
    role: ROLE | None = None
    active: bool | None = None
    display_name: str | None = Field(None, max_length=80)
    new_password: bool = False


class PersonRow(BaseModel):
    id: int
    name: str
    role: str
    shift: str
    sex: str = "m"
    home: str | None = None
    skills: dict[str, datetime | None] = {}


class RosterReport(BaseModel):
    """The people of the plant and their qualifications (shop floor, at the start of a run)."""
    run_id: str
    people: list[PersonRow]


class AttendanceRow(BaseModel):
    shift_start: datetime
    shift: str
    id: int
    status: str
    seat: str | None = None
    covers: int | None = None


class ShipCheck(BaseModel):
    """The pallet label scanned at the truck (shop floor)."""
    run_id: str
    plant_time: datetime
    serial: str = Field(min_length=1, max_length=40)
    records: list[RecordRow] = []


class TrainingStart(BaseModel):
    scenario: Literal["BAD_LOT", "AOI_STORM", "OVEN_FAILURE", "COMPLAINT"]


class TrainingFinish(BaseModel):
    asns: list[str] = []                      # COMPLAINT: the shipments to report to the customer
    abort: bool = False


class SkillDecision(BaseModel):
    """A decision on the web about a qualification of an operator."""
    skill: str = Field(min_length=2, max_length=20)
    action: Literal["GRANT", "RENEW", "REVOKE"]
    until: str | None = None                  # YYYY-MM-DD, the certificate is valid to the end of the day
    reason: str = Field(min_length=3, max_length=300)


class QualificationRow(BaseModel):
    """A certificate renewed (the eye test, the IPC course)."""
    id: int
    skill: str
    until: datetime | None = None


class VerificationReport(BaseModel):
    """The master sample check of a tester (shop floor)."""
    run_id: str
    station: str
    plant_time: datetime
    start: datetime | None = None
    result: Literal["PASS", "FAIL"]
    person: str | None = None
    records: list[RecordRow] = []


class MaterialScanRequest(BaseModel):
    """A scan of SMT material (shop floor): a reel at a feeder, a paste jar at a printer; at goods-in the
    delivery note of a truck, a reel received, a reel out of the store for a line."""
    run_id: str
    station: str | None = None
    place: str | None = None            # goods-in: "GI"
    plant_time: datetime
    kind: Literal["LOAD", "SPLICE", "PASTE", "ARRIVAL", "RECEIPT", "ISSUE"]
    item: str = Field(min_length=1, max_length=24)   # the reel or jar label
    slot: str | None = None             # the feeder: the part the program needs there
    part: str | None = None
    lot: str | None = None
    qty: int | None = None
    left: int | None = None
    msl: str | None = None
    floor_used_s: float | None = None   # floor life used before out_since
    out_since: datetime | None = None   # out of the dry bag since
    out_at: datetime | None = None      # a jar: out of the fridge since
    prints: int | None = None
    job_id: int | None = None
    note: str | None = None             # goods-in: the delivery note
    supplier: str | None = None
    lines: list[dict] = []              # the delivery note: [{part, reels}]
    line: str | None = None             # a reel out of the store: for this line
    received: datetime | None = None
    operator: int | None = None
    records: list[RecordRow] = []


class StockReport(BaseModel):
    """The reels in the store of goods-in at the start of a run (shop floor)."""
    run_id: str
    reels: list[dict] = []              # [{serial, part, lot, qty, msl, supplier, received}]


class MaterialEvent(BaseModel):
    kind: Literal["START", "END", "BAKED", "AT_LINE"]
    item: str
    station: str | None = None
    line: str | None = None
    slot: str | None = None
    at: datetime
    left: int | None = None
    reason: str | None = None
    lot: str | None = None              # a jar: its details
    out_at: datetime | None = None
    opened_at: datetime | None = None


class MaterialLevel(BaseModel):
    id: str
    left: int
    eta_s: float | None = None


class MaterialReport(BaseModel):
    """SMT material since the last flush: reels and jars that started or stopped feeding, levels."""
    events: list[MaterialEvent] = []
    levels: list[MaterialLevel] = []


class ScenarioRequest(BaseModel):
    knobs: dict | None = None                 # the knobs of a tuning scenario; None: the plant as configured


class NextJobRequest(BaseModel):
    run_id: str
    line: str
    plant_time: datetime


class StateTimeRow(BaseModel):
    station: str
    hour: datetime
    state: str
    seconds: float
    occurrences: int


class CountRow(BaseModel):
    station: str
    hour: datetime
    job_id: int
    processed: int
    good: int
    bad: int
    false_calls: int


class DefectRow(BaseModel):
    station: str
    hour: datetime
    code: str
    found: int


class EventRow(BaseModel):
    id: str
    station: str
    state: str
    reason: str | None = None
    job_id: int | None = None
    start: datetime
    end: datetime | None = None


class JobProgress(BaseModel):
    id: int
    status: str
    released: int
    good: int
    scrap: int
    in_repair: int
    held: int = 0
    completed_at: datetime | None = None


class StationSnapshot(BaseModel):
    code: str
    state: str
    reason: str | None = None
    since: datetime
    job_id: int | None = None
    item: str | None = None


class DefectRef(BaseModel):
    serial: str  # the piece: a board of a panel, or the unit itself
    code: str


class RecordRow(BaseModel):
    """Check-out: what one station did to one unit."""
    serial: str
    station: str
    step: str
    job_id: int | None = None
    start: datetime
    end: datetime
    result: Literal["PASS", "FAIL"]
    defects: list[DefectRef] = []
    measurements: dict[str, float] = {}
    action: str | None = None  # WASH, BAD_MARK, REPAIR


class CheckInRequest(BaseModel):
    run_id: str
    station: str
    plant_time: datetime
    serial: str | None = None   # None: the station gives the item its first serial (laser)
    consume: str | None = None  # board that becomes a product unit (final assembly loader)
    job_id: int | None = None
    pieces: int | None = None   # boards on a new panel
    operator: int | None = None # the person logged in at the station (the MES checks the qualification)
    records: list[RecordRow] = []


class RepairRequest(BaseModel):
    run_id: str
    station: str
    plant_time: datetime
    serial: str
    success: bool
    operator: int | None = None
    records: list[RecordRow] = []


class ShipmentRow(BaseModel):
    """A truck that left: its delivery note and the pallets on it."""
    asn: str
    customer: str
    plate: str
    departed: datetime
    pallets: list[str]          # SSCC serial numbers
    klt: int = 0
    units: int = 0


class IngestPayload(BaseModel):
    run_id: str
    plant_time: datetime
    speed: float | None = None
    state_time: list[StateTimeRow] = []
    counts: list[CountRow] = []
    defects: list[DefectRow] = []
    events: list[EventRow] = []
    jobs: list[JobProgress] = []
    stations: list[StationSnapshot] = []
    live: dict = {}
    floor: dict = {}   # where every piece is right now, for the live view (kept in memory only)
    shipments: list[ShipmentRow] = []
    records: list[RecordRow] = []
    acks: list[int] = []  # shop-floor commands carried out
    attendance: list[AttendanceRow] = []  # who came to the shift that starts (once per shift)
    qualifications: list[QualificationRow] = []  # certificates renewed
    material: MaterialReport | None = None       # SMT reels and paste jars


class SpeedRequest(BaseModel):
    speed: float = Field(ge=1, le=600)


class PauseRequest(BaseModel):
    paused: bool


class GotoRequest(BaseModel):
    """Let the plant run as fast as it can until this plant time, then go on at its speed or stay paused."""
    until: datetime
    then: Literal["pause", "run"] = "pause"


class CrewCallRequest(BaseModel):
    """Call somebody of a department to a machine (andon / help request); the machine goes on."""
    station: str = Field(min_length=1, max_length=40)
    role: Literal["MAINTENANCE", "IT", "FOREMAN", "LOGISTICS"]
    minutes: float = Field(10, ge=1, le=120)          # how long the person stays at the machine
    meter: bool = False                               # maintenance brings the measuring instruments


class HoldCreate(BaseModel):
    kind: Literal["SERIAL", "WORK_ORDER", "STATION_WINDOW", "MATERIAL_LOT"]
    target: str = Field(min_length=1, max_length=60)  # serial, order number, station code or supplier lot
    window_from: datetime | None = None
    window_to: datetime | None = None
    reason: str = Field(min_length=3, max_length=2000)


class ReprintRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=200)          # why: damaged, unreadable, lost, ...


class HoldRelease(BaseModel):
    disposition: Literal["RETURN", "SCRAP"] = "RETURN"


class Disposition(BaseModel):
    action: Literal["RETURN", "SCRAP"]
