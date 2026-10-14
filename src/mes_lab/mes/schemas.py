"""Request bodies of the shop-floor API (edge gateway, dispatching, traceability)
and of the web actions (holds, dispositions)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RunRegistration(BaseModel):
    run_id: str = Field(min_length=8, max_length=36)
    seed: int = 1
    speed: float | None = None
    initial_stock: dict[str, list[str]] = {}  # boards already in the supermarket
    lines: list[str] | None = None            # the lines this plant has (a tuning scenario may have fewer)
    knobs: dict | None = None                 # the knobs of the tuning game it was built with (None: as configured)


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
    records: list[RecordRow] = []


class RepairRequest(BaseModel):
    run_id: str
    station: str
    plant_time: datetime
    serial: str
    success: bool
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
    kind: Literal["SERIAL", "WORK_ORDER", "STATION_WINDOW"]
    target: str = Field(min_length=1, max_length=60)  # serial, order number or station code
    window_from: datetime | None = None
    window_to: datetime | None = None
    reason: str = Field(min_length=3, max_length=2000)


class HoldRelease(BaseModel):
    disposition: Literal["RETURN", "SCRAP"] = "RETURN"


class Disposition(BaseModel):
    action: Literal["RETURN", "SCRAP"]
