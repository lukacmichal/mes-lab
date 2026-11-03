"""MES database schema (PostgreSQL).

Master data follows the ISA-95 object model: the equipment hierarchy
(plant > area > line > station), materials with their routings (operations and
steps with ideal cycle times), and reference codes. Execution data covers work
orders and jobs, live station status and the condensed production history that
the edge gateway delivers (state time, counts, defects, downtime events).

Timestamps are plant time (timestamptz). The simulation runs faster than the
wall clock, so plant time may be ahead of the real date.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    Time,
    UniqueConstraint,
    true as sa_true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

TS = DateTime(timezone=True)
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


# ============================================================== master data

class Plant(Base):
    __tablename__ = "plant"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    company: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(60))


class Area(Base):
    __tablename__ = "area"
    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id"))
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    lines: Mapped[list[Line]] = relationship(back_populates="area", order_by="Line.sort")


class Line(Base):
    __tablename__ = "line"
    id: Mapped[int] = mapped_column(primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("area.id"))
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    job_line: Mapped[bool] = mapped_column(Boolean)
    buffer: Mapped[int] = mapped_column(Integer)
    output_type_id: Mapped[int] = mapped_column(ForeignKey("station_type.id"))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    area: Mapped[Area] = relationship(back_populates="lines")
    stations: Mapped[list[Station]] = relationship(back_populates="line", order_by="Station.sort")


class StationType(Base):
    __tablename__ = "station_type"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    tag: Mapped[str] = mapped_column(String(10))


class Station(Base):
    __tablename__ = "station"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("line.id"))
    station_type_id: Mapped[int] = mapped_column(ForeignKey("station_type.id"))
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    cell: Mapped[int] = mapped_column(Integer)
    slot: Mapped[int] = mapped_column(Integer)
    sort: Mapped[int] = mapped_column(Integer)
    line: Mapped[Line] = relationship(back_populates="stations")
    station_type: Mapped[StationType] = relationship()


class Customer(Base):
    __tablename__ = "customer"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(2))


class Material(Base):
    __tablename__ = "material"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10))  # FG | PCBA
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"))
    pcba_id: Mapped[int | None] = mapped_column(ForeignKey("material.id"))
    boards_per_panel: Mapped[int] = mapped_column(Integer, default=1)
    klt_qty: Mapped[int | None] = mapped_column(Integer)
    klt_per_pallet: Mapped[int | None] = mapped_column(Integer)
    safety_stock: Mapped[int | None] = mapped_column(Integer)
    operations: Mapped[list[Operation]] = relationship(order_by="Operation.seq")


class Operation(Base):
    """Routing operation: one pass through one line."""
    __tablename__ = "operation"
    __table_args__ = (UniqueConstraint("material_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id"))
    seq: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    steps: Mapped[list[OperationStep]] = relationship(order_by="OperationStep.id")


class OperationStep(Base):
    __tablename__ = "operation_step"
    __table_args__ = (UniqueConstraint("operation_id", "station_type_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operation.id"))
    station_type_id: Mapped[int] = mapped_column(ForeignKey("station_type.id"))
    ideal_cycle_s: Mapped[float] = mapped_column(Float)
    station_type: Mapped[StationType] = relationship()


class LineMaterial(Base):
    """Finished goods a final assembly or packing line is set up for."""
    __tablename__ = "line_material"
    line_id: Mapped[int] = mapped_column(ForeignKey("line.id"), primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id"), primary_key=True)


class Shift(Base):
    __tablename__ = "shift"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)
    name: Mapped[str] = mapped_column(String(40))
    start: Mapped[time] = mapped_column(Time)
    end: Mapped[time] = mapped_column(Time)


class DowntimeReason(Base):
    __tablename__ = "downtime_reason"
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20))


class DefectCode(Base):
    __tablename__ = "defect_code"
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20))


# ============================================================== execution

class SimRun(Base):
    """One run of the plant simulation. A new run starts the shop floor empty."""
    __tablename__ = "sim_run"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    seed: Mapped[int] = mapped_column(Integer)
    speed: Mapped[float | None] = mapped_column(Float)
    desired_speed: Mapped[float | None] = mapped_column(Float)
    plant_start: Mapped[datetime] = mapped_column(TS)
    last_plant_time: Mapped[datetime] = mapped_column(TS)
    started_wall: Mapped[datetime] = mapped_column(TS)
    last_seen_wall: Mapped[datetime] = mapped_column(TS)
    # the plant of this run: its lines and the knobs of the tuning game it was built with (None: as configured)
    lines: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    knobs: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    settings_rev: Mapped[int | None] = mapped_column(Integer)   # the revision of the settings it was built with


class WorkOrder(Base):
    __tablename__ = "work_order"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(20), unique=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id"))
    qty: Mapped[int] = mapped_column(Integer)  # boards (PCBA) or units (FG)
    status: Mapped[str] = mapped_column(String(20), index=True)  # RELEASED, IN_PROGRESS, COMPLETED
    source: Mapped[str] = mapped_column(String(20), default="PLANNER")
    created_at: Mapped[datetime] = mapped_column(TS)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    completed_at: Mapped[datetime | None] = mapped_column(TS)
    qty_good: Mapped[int] = mapped_column(Integer, default=0)
    qty_scrap: Mapped[int] = mapped_column(Integer, default=0)
    erp_order_no: Mapped[str | None] = mapped_column(String(20), unique=True)   # the production order it came from
    erp_good: Mapped[int] = mapped_column(Integer, default=0, server_default="0")    # confirmed to the ERP so far
    erp_scrap: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    erp_final: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    material: Mapped[Material] = relationship()
    jobs: Mapped[list[Job]] = relationship(back_populates="work_order", order_by="Job.seq")


class Job(Base):
    """Execution of one routing operation of a work order on one line."""
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_line_status", "line_id", "status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_order.id"))
    seq: Mapped[int] = mapped_column(Integer)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operation.id"))
    line_id: Mapped[int] = mapped_column(ForeignKey("line.id"))
    status: Mapped[str] = mapped_column(String(20))  # QUEUED, ACTIVE, COMPLETED
    qty: Mapped[int] = mapped_column(Integer)        # handling units: panels or units
    unit: Mapped[str] = mapped_column(String(10))    # PANEL | UNIT
    expected: Mapped[int] = mapped_column(Integer)   # pieces at the end: boards, panels or units
    released: Mapped[int] = mapped_column(Integer, default=0)
    good: Mapped[int] = mapped_column(Integer, default=0)
    scrap: Mapped[int] = mapped_column(Integer, default=0)
    in_repair: Mapped[int] = mapped_column(Integer, default=0)
    held: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("sim_run.id"))
    queued_at: Mapped[datetime] = mapped_column(TS)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    completed_at: Mapped[datetime | None] = mapped_column(TS)
    work_order: Mapped[WorkOrder] = relationship(back_populates="jobs")
    operation: Mapped[Operation] = relationship()
    line: Mapped[Line] = relationship()


class StationStatus(Base):
    """Latest known state of each station (live view)."""
    __tablename__ = "station_status"
    station_id: Mapped[int] = mapped_column(ForeignKey("station.id"), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("sim_run.id"))
    state: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(ForeignKey("downtime_reason.code"))
    since: Mapped[datetime] = mapped_column(TS)
    job_id: Mapped[int | None] = mapped_column(Integer)
    item: Mapped[str | None] = mapped_column(String(60))
    updated_at: Mapped[datetime] = mapped_column(TS)


class PlantLive(Base):
    """Latest snapshot of buffers and stores reported by the edge gateway."""
    __tablename__ = "plant_live"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("sim_run.id"))
    plant_time: Mapped[datetime] = mapped_column(TS)
    payload: Mapped[dict] = mapped_column(JSONType)


class StationStateTime(Base):
    """Seconds a station spent in a state during one hour."""
    __tablename__ = "station_state_time"
    run_id: Mapped[str] = mapped_column(ForeignKey("sim_run.id"), primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("station.id"), primary_key=True)
    hour: Mapped[datetime] = mapped_column(TS, primary_key=True, index=True)
    state: Mapped[str] = mapped_column(String(20), primary_key=True)
    seconds: Mapped[float] = mapped_column(Float)
    occurrences: Mapped[int] = mapped_column(Integer)


class ProductionCount(Base):
    """Items processed by a station during one hour, per job (0 = no job)."""
    __tablename__ = "production_count"
    run_id: Mapped[str] = mapped_column(ForeignKey("sim_run.id"), primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("station.id"), primary_key=True)
    hour: Mapped[datetime] = mapped_column(TS, primary_key=True, index=True)
    job_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    processed: Mapped[int] = mapped_column(Integer)
    good: Mapped[int] = mapped_column(Integer)
    bad: Mapped[int] = mapped_column(Integer)
    false_calls: Mapped[int] = mapped_column(Integer)


class DefectCount(Base):
    __tablename__ = "defect_count"
    run_id: Mapped[str] = mapped_column(ForeignKey("sim_run.id"), primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("station.id"), primary_key=True)
    hour: Mapped[datetime] = mapped_column(TS, primary_key=True, index=True)
    defect_code: Mapped[str] = mapped_column(ForeignKey("defect_code.code"), primary_key=True)
    found: Mapped[int] = mapped_column(Integer)


class DowntimeEvent(Base):
    """Breakdown, changeover or planned maintenance of one station."""
    __tablename__ = "downtime_event"
    __table_args__ = (Index("ix_downtime_station_start", "station_id", "start_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("sim_run.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("station.id"))
    state: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(ForeignKey("downtime_reason.code"))
    job_id: Mapped[int | None] = mapped_column(Integer)
    start_at: Mapped[datetime] = mapped_column(TS, index=True)
    end_at: Mapped[datetime | None] = mapped_column(TS)


# ============================================================== traceability

SERIAL_SEQUENCES = {
    kind: Sequence(f"serial_{kind.lower()}_seq", metadata=Base.metadata)
    for kind in ("PANEL", "UNIT", "KLT", "PALLET")
}


class Unit(Base):
    """Anything with a serial number: panel, board, product unit, KLT box, pallet."""
    __tablename__ = "unit"
    __table_args__ = (Index("ix_unit_status", "status"),
                      Index("ix_unit_open_container", "container_station_id", "status"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    serial: Mapped[str] = mapped_column(String(40), unique=True)
    kind: Mapped[str] = mapped_column(String(10))  # PANEL BOARD UNIT KLT PALLET
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id"))
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_order.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("sim_run.id"))
    status: Mapped[str] = mapped_column(String(20))
    prev_status: Mapped[str | None] = mapped_column(String(20))  # before quarantine
    operation: Mapped[str | None] = mapped_column(String(20))   # current routing operation
    completed: Mapped[list] = mapped_column(JSONType, default=list)  # steps done in it
    station_id: Mapped[int | None] = mapped_column(ForeignKey("station.id"))  # last seen at
    location: Mapped[str | None] = mapped_column(String(60))
    quarantine_reason: Mapped[str | None] = mapped_column(String(200))
    container_station_id: Mapped[int | None] = mapped_column(ForeignKey("station.id"))
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipment.id"), index=True)  # truck it left with
    qty: Mapped[int] = mapped_column(Integer, default=0)  # items inside a container
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS, index=True)
    material: Mapped[Material] = relationship()


class Shipment(Base):
    """A truck that left the plant with pallets: the delivery note (ASN)."""
    __tablename__ = "shipment"
    id: Mapped[int] = mapped_column(primary_key=True)
    asn_no: Mapped[str] = mapped_column(String(30), unique=True)
    customer: Mapped[str] = mapped_column(String(20))
    plate: Mapped[str] = mapped_column(String(20))
    run_id: Mapped[str | None] = mapped_column(String(36))
    departed_at: Mapped[datetime] = mapped_column(TS, index=True)
    pallets: Mapped[int] = mapped_column(Integer)
    klt: Mapped[int] = mapped_column(Integer)
    units: Mapped[int] = mapped_column(Integer)
    erp_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")   # the DESADV is out


class Label(Base):
    """A label the MES printed for a box or pallet: the values and the layout as printed (the template may
    change later), and every print of it ([{at, by, reason}], the first one by the printer)."""
    __tablename__ = "label"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("unit.id", ondelete="CASCADE"), unique=True)
    serial: Mapped[str] = mapped_column(String(40), index=True)
    template: Mapped[str] = mapped_column(String(20))
    settings_rev: Mapped[int | None] = mapped_column(Integer)      # the revision of the settings in force
    printed_at: Mapped[datetime] = mapped_column(TS, index=True)
    station: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSONType)
    layout: Mapped[dict] = mapped_column(JSONType)
    prints: Mapped[list] = mapped_column(JSONType)


class UnitLink(Base):
    """Genealogy edge, from input to output: panel -> board (SPLIT),
    board -> unit (ASSEMBLY), unit -> KLT (PACK), KLT -> pallet (PALLET)."""
    __tablename__ = "unit_link"
    __table_args__ = (UniqueConstraint("source_id", "target_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("unit.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("unit.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(TS)


class OperationRecord(Base):
    """One operation of one unit on one station (check-out)."""
    __tablename__ = "operation_record"
    __table_args__ = (Index("ix_oprec_station_end", "station_id", "ended_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("unit.id", ondelete="CASCADE"), index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("station.id"))
    job_id: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String(36))
    operation: Mapped[str | None] = mapped_column(String(20))
    step: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(TS)
    ended_at: Mapped[datetime] = mapped_column(TS, index=True)
    result: Mapped[str] = mapped_column(String(12))  # PASS FAIL REPAIRED SCRAPPED
    action: Mapped[str | None] = mapped_column(String(200))  # e.g. defect codes of a repair
    measurements: Mapped[dict] = mapped_column(JSONType, default=dict)


class Defect(Base):
    __tablename__ = "defect"
    __table_args__ = (Index("ix_defect_status", "status"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("unit.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(ForeignKey("defect_code.code"))
    station_id: Mapped[int] = mapped_column(ForeignKey("station.id"))
    found_at: Mapped[datetime] = mapped_column(TS, index=True)
    status: Mapped[str] = mapped_column(String(12))  # OPEN REPAIRED REWORKED SCRAPPED
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    closed_station_id: Mapped[int | None] = mapped_column(ForeignKey("station.id"))


class Hold(Base):
    """Containment: units matching the hold are stopped at their next check-in."""
    __tablename__ = "hold"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # SERIAL WORK_ORDER STATION_WINDOW MATERIAL_LOT
    target: Mapped[str] = mapped_column(String(60))
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_order.id"))
    station_id: Mapped[int | None] = mapped_column(ForeignKey("station.id"))
    window_from: Mapped[datetime | None] = mapped_column(TS)
    window_to: Mapped[datetime | None] = mapped_column(TS)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), index=True)  # ACTIVE RELEASED
    disposition: Mapped[str | None] = mapped_column(String(12))
    affected: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS)       # plant time
    created_by: Mapped[str] = mapped_column(String(60))
    released_at: Mapped[datetime | None] = mapped_column(TS)
    released_by: Mapped[str | None] = mapped_column(String(60))


class HoldUnit(Base):
    __tablename__ = "hold_unit"
    hold_id: Mapped[int] = mapped_column(ForeignKey("hold.id", ondelete="CASCADE"), primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("unit.id", ondelete="CASCADE"), primary_key=True,
                                         index=True)


class Employee(Base):
    """A person of the plant as the plant reports its roster (the same seed gives the same people)."""
    __tablename__ = "employee"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20))
    shift: Mapped[str] = mapped_column(String(4))
    sex: Mapped[str] = mapped_column(String(1))
    home: Mapped[str | None] = mapped_column(String(60))          # the seat that is theirs
    updated_at: Mapped[datetime] = mapped_column(TS)


class Qualification(Base):
    """A skill of a person (SMT, FA, INSPECTION, PACK, REPAIR) and until when its certificate is valid."""
    __tablename__ = "qualification"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), primary_key=True)
    skill: Mapped[str] = mapped_column(String(20), primary_key=True)
    valid_until: Mapped[datetime | None] = mapped_column(TS)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")   # taken away on the web
    note: Mapped[str | None] = mapped_column(String(200))                                    # why it was taken away


class QualificationChange(Base):
    """A decision on the web about a qualification of a person (mes/qualifications.py): GRANT, RENEW or
    REVOKE, the validity before and after, from when it holds, why and who."""
    __tablename__ = "qualification_change"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), index=True)
    skill: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(8))
    valid_until: Mapped[datetime | None] = mapped_column(TS)
    before: Mapped[datetime | None] = mapped_column(TS)
    effective_at: Mapped[datetime] = mapped_column(TS)
    reason: Mapped[str] = mapped_column(String(300))
    by: Mapped[str] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(TS, index=True)                                     # plant time


class Attendance(Base):
    """Who came to a shift, who was off (sick, holiday) and which seat they held."""
    __tablename__ = "attendance"
    shift_start: Mapped[datetime] = mapped_column(TS, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), primary_key=True)
    shift: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(10))               # PRESENT SICK HOLIDAY
    seat: Mapped[str | None] = mapped_column(String(60))
    covers: Mapped[int | None] = mapped_column(Integer)           # the absent person whose seat they took


class Reel(Base):
    """A reel of SMT components with the label it got at goods-in: the part, the supplier lot, how much is
    left, and its floor life when the part is moisture sensitive (MSL)."""
    __tablename__ = "reel"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    serial: Mapped[str] = mapped_column(String(24), unique=True)
    part: Mapped[str] = mapped_column(String(30), index=True)
    lot: Mapped[str] = mapped_column(String(24), index=True)
    qty: Mapped[int] = mapped_column(Integer)
    left: Mapped[int] = mapped_column(Integer)
    msl: Mapped[str | None] = mapped_column(String(4))
    floor_used_s: Mapped[float] = mapped_column(Float, default=0.0)   # floor life used before out_since
    out_since: Mapped[datetime | None] = mapped_column(TS)            # out of the dry bag since
    status: Mapped[str] = mapped_column(String(10), index=True)       # STORE TRANSIT LINE FEEDER NEXT EMPTY BAKE GONE
    station_id: Mapped[int | None] = mapped_column(ForeignKey("station.id"))
    slot: Mapped[str | None] = mapped_column(String(30))
    eta_s: Mapped[float | None] = mapped_column(Float)                 # runs out in (at the last report)
    supplier: Mapped[str | None] = mapped_column(String(12))
    note: Mapped[str | None] = mapped_column(String(24))               # the delivery note it came with
    received_at: Mapped[datetime | None] = mapped_column(TS)
    line: Mapped[str | None] = mapped_column(String(20))               # issued to / in the rack of this line
    updated_at: Mapped[datetime] = mapped_column(TS)


class FeederLoad(Base):
    """A reel feeding a placer from `started_at` to `ended_at`: every panel the placer made meanwhile
    got parts of it (the genealogy of the components)."""
    __tablename__ = "feeder_load"
    __table_args__ = (Index("ix_feeder_load_station", "station_id", "started_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reel_id: Mapped[int] = mapped_column(ForeignKey("reel.id", ondelete="CASCADE"), index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("station.id"))
    slot: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(TS)
    ended_at: Mapped[datetime | None] = mapped_column(TS)
    reason: Mapped[str | None] = mapped_column(String(12))            # EMPTY CHANGEOVER MSL


class PasteJar(Base):
    """A jar of solder paste: out of the fridge (thawing), opened on a printer, used up or expired."""
    __tablename__ = "paste_jar"
    id: Mapped[int] = mapped_column(primary_key=True)
    serial: Mapped[str] = mapped_column(String(24), unique=True)
    lot: Mapped[str] = mapped_column(String(24))
    out_at: Mapped[datetime] = mapped_column(TS)                      # out of the fridge
    opened_at: Mapped[datetime | None] = mapped_column(TS)            # on the stencil
    ended_at: Mapped[datetime | None] = mapped_column(TS)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("station.id"), index=True)
    prints: Mapped[int] = mapped_column(Integer)                      # prints left
    status: Mapped[str] = mapped_column(String(10))                   # THAW OPEN EMPTY EXPIRED GONE
    reason: Mapped[str | None] = mapped_column(String(12))


class MaterialScan(Base):
    """A scan of SMT material at a machine and what the MES said to it."""
    __tablename__ = "material_scan"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, index=True)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("station.id"))
    place: Mapped[str | None] = mapped_column(String(20))             # goods-in: GI
    kind: Mapped[str] = mapped_column(String(8))                      # LOAD SPLICE PASTE ARRIVAL RECEIPT ISSUE
    item: Mapped[str] = mapped_column(String(24))
    part: Mapped[str | None] = mapped_column(String(30))
    slot: Mapped[str | None] = mapped_column(String(30))
    result: Mapped[str] = mapped_column(String(20))                   # OK or the reason it was refused
    message: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[int | None] = mapped_column(Integer)


class Delivery(Base):
    """A supplier truck at goods-in: its delivery note and what was received (or refused) of it."""
    __tablename__ = "delivery"
    id: Mapped[int] = mapped_column(primary_key=True)
    note: Mapped[str] = mapped_column(String(24), unique=True)
    supplier: Mapped[str] = mapped_column(String(12))
    arrived_at: Mapped[datetime] = mapped_column(TS, index=True)
    expected: Mapped[dict] = mapped_column(JSONB)                     # part -> reels on the note
    received: Mapped[dict] = mapped_column(JSONB)                     # part -> reels received
    refused: Mapped[int] = mapped_column(Integer, default=0)


class StationVerification(Base):
    """The master sample check of a tester: done at the start of every shift before it may test
    production (the check-in refuses units until a passed check of this shift is on record)."""
    __tablename__ = "station_verification"
    __table_args__ = (Index("ix_verification_station_at", "station_id", "at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(36))
    station_id: Mapped[int] = mapped_column(ForeignKey("station.id"))
    at: Mapped[datetime] = mapped_column(TS)                  # plant time of the result
    started_at: Mapped[datetime | None] = mapped_column(TS)
    shift_start: Mapped[datetime] = mapped_column(TS)         # the shift it counts for
    result: Mapped[str] = mapped_column(String(8))            # PASS FAIL
    person: Mapped[str | None] = mapped_column(String(60))


class SpcAlarm(Base):
    """A rule of a control chart fired: the reaction of the control plan (containment, adjustment,
    disposition by data) is tracked here."""
    __tablename__ = "spc_alarm"
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("station.id"))
    station_code: Mapped[str] = mapped_column(String(40))
    characteristic: Mapped[str] = mapped_column(String(40))
    rule: Mapped[str] = mapped_column(String(4))
    raised_at: Mapped[datetime] = mapped_column(TS, index=True)
    status: Mapped[str] = mapped_column(String(12), index=True)   # ADJUSTING CLOSED
    hold_id: Mapped[int | None] = mapped_column(ForeignKey("hold.id"))
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    note: Mapped[str | None] = mapped_column(String(200))


class SimCommand(Base):
    """Instruction for the shop floor (release or scrap an item in quarantine),
    delivered with ingest responses until the plant confirms it."""
    __tablename__ = "sim_command"
    id: Mapped[int] = mapped_column(primary_key=True)
    serial: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(12))  # RETURN SCRAP UNLOAD_REEL ADJUST HOLD_LOT FAULT_START ...
    created_at: Mapped[datetime] = mapped_column(TS)
    acked_at: Mapped[datetime | None] = mapped_column(TS, index=True)
    data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)     # e.g. the fault of a training scenario


class TrainingRun(Base):
    """A training scenario played in the live plant (mes/training.py): what was done to the plant (params),
    what the trainee answered, and the grade (result)."""
    __tablename__ = "training_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10), index=True)     # RUNNING DONE ABORTED
    run_id: Mapped[str | None] = mapped_column(String(36))          # the run of the plant it began in
    started_at: Mapped[datetime] = mapped_column(TS)                # plant time
    started_by: Mapped[str] = mapped_column(String(60))
    params: Mapped[dict] = mapped_column(JSONType)
    fault_end_at: Mapped[datetime | None] = mapped_column(TS)       # the fault stopped (fixed, held, by itself)
    fault_end_how: Mapped[str | None] = mapped_column(String(80))
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    finished_by: Mapped[str | None] = mapped_column(String(60))
    answer: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(TS)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)
    # ADMIN (everything, users too), ENGINEER (settings), QUALITY (holds, quarantine), SUPERVISOR (the plant:
    # speed, pause, time, calls, scenarios); see mes/auth.py
    role: Mapped[str] = mapped_column(String(16), default="ADMIN", server_default="ADMIN")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())


class AuditEvent(Base):
    """Something an administrator changed outside the plant settings: a user added, a role or a password."""
    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, index=True)
    by: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(12))          # USER
    subject: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(20))        # CREATE ROLE ACTIVE NAME PASSWORD
    detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class SettingRevision(Base):
    """A set of changes of the settings page, like an engineering change in a plant: a DRAFT while people
    edit it, then released with a reason and SCHEDULED for a plant time (now, the start of the next shift),
    ACTIVE from then on (or DISCARDED). A released revision never changes again. `overrides` is everything
    in force with it (all the changes of the revisions before it, see mes_lab.settings)."""
    __tablename__ = "setting_revision"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(12), index=True)   # DRAFT SCHEDULED ACTIVE DISCARDED
    number: Mapped[int | None] = mapped_column(Integer, unique=True)   # Rev 1, 2, ... given at the release
    created_at: Mapped[datetime] = mapped_column(TS)
    created_by: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(300))
    released_at: Mapped[datetime | None] = mapped_column(TS)
    released_by: Mapped[str | None] = mapped_column(String(40))
    effective_at: Mapped[datetime | None] = mapped_column(TS)      # plant time
    activated_at: Mapped[datetime | None] = mapped_column(TS)      # plant time it took effect
    plant: Mapped[bool] = mapped_column(Boolean, default=False)    # the machines need it: the plant builds anew
    overrides: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    check: Mapped[dict | None] = mapped_column(JSONType, nullable=True)   # the trial run before the release
    changes: Mapped[list[SettingChange]] = relationship(back_populates="revision", order_by="SettingChange.id")


class SettingChange(Base):
    """One change of a revision: a path into the config (mes_lab.settings), the value in force before it and
    the new one. SET a value (None takes a key away: a part out of the BOM), RESET back to the config files."""
    __tablename__ = "setting_change"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("setting_revision.id"), index=True)
    path: Mapped[str] = mapped_column(String(200), index=True)
    action: Mapped[str] = mapped_column(String(8))                 # SET RESET
    old: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    new: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    at: Mapped[datetime] = mapped_column(TS)
    by: Mapped[str] = mapped_column(String(40))
    revision: Mapped[SettingRevision] = relationship(back_populates="changes")


# ============================================================== the fake ERP and the interface
# The ERP is another system with its own tables (erp_*). The MES never reads them and the ERP never reads
# the tables of the MES: they only exchange messages (integration_message), see mes_lab.erp.

class IntegrationMessage(Base):
    """One message on the interface between the ERP and the MES. The ID is the idempotency key: the
    sender may deliver the same message more than once, the receiver books it only once."""
    __tablename__ = "integration_message"
    __table_args__ = (Index("ix_integration_message_queue", "direction", "status", "next_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    direction: Mapped[str] = mapped_column(String(10))            # ERP>MES  MES>ERP
    kind: Mapped[str] = mapped_column(String(20))                 # PRODUCTION_ORDER ORDER_RECEIVED CONFIRMATION ...
    ref: Mapped[str] = mapped_column(String(40), index=True)      # the business document: order, ASN, ...
    payload: Mapped[dict] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(10), index=True)   # PENDING DONE ERROR DISCARDED
    attempts: Mapped[int] = mapped_column(Integer, default=0)     # deliveries tried
    duplicates: Mapped[int] = mapped_column(Integer, default=0)   # deliveries the receiver had seen before
    created_at: Mapped[datetime] = mapped_column(TS, index=True)  # plant time
    next_at: Mapped[datetime] = mapped_column(TS)                 # not delivered before this
    done_at: Mapped[datetime | None] = mapped_column(TS)
    error: Mapped[str | None] = mapped_column(String(300))        # the last failure
    result: Mapped[str | None] = mapped_column(String(300))       # what the receiver did
    log: Mapped[list] = mapped_column(JSONType, default=list)     # [plant time, what happened]


class IntegrationInbox(Base):
    """The message IDs a receiver has processed (its idempotency memory)."""
    __tablename__ = "integration_inbox"
    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    receiver: Mapped[str] = mapped_column(String(10))
    processed_at: Mapped[datetime] = mapped_column(TS, index=True)


class ErpCallOff(Base):
    """A call-off of a customer for one product (VDA 4905 / DELFOR): daily quantities for the days ahead.
    The newest one received is in force for the days it covers."""
    __tablename__ = "erp_call_off"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    customer: Mapped[str] = mapped_column(String(20))
    material: Mapped[str] = mapped_column(String(30), index=True)
    received_at: Mapped[datetime] = mapped_column(TS, index=True)
    lines: Mapped[list[ErpCallOffLine]] = relationship(back_populates="call_off", order_by="ErpCallOffLine.due",
                                                       cascade="all, delete-orphan", passive_deletes=True)


class ErpCallOffLine(Base):
    __tablename__ = "erp_call_off_line"
    __table_args__ = (UniqueConstraint("call_off_id", "due"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    call_off_id: Mapped[int] = mapped_column(ForeignKey("erp_call_off.id", ondelete="CASCADE"))
    due: Mapped[date] = mapped_column(Date, index=True)
    qty: Mapped[int] = mapped_column(Integer)
    firm: Mapped[bool] = mapped_column(Boolean)
    call_off: Mapped[ErpCallOff] = relationship(back_populates="lines")


class ErpProductionOrder(Base):
    """A production order of the ERP: CREATED (sent to the MES), RELEASED (the MES took it over),
    CLOSED (the final confirmation came: technically complete)."""
    __tablename__ = "erp_production_order"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    material: Mapped[str] = mapped_column(String(30), index=True)
    qty: Mapped[int] = mapped_column(Integer)
    due: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), index=True)
    created_at: Mapped[datetime] = mapped_column(TS, index=True)
    received_at: Mapped[datetime | None] = mapped_column(TS)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    mes_order_no: Mapped[str | None] = mapped_column(String(20))
    good: Mapped[int] = mapped_column(Integer, default=0)          # sum of the confirmations
    scrap: Mapped[int] = mapped_column(Integer, default=0)


class ErpConfirmation(Base):
    __tablename__ = "erp_confirmation"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("erp_production_order.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str] = mapped_column(String(36))
    good: Mapped[int] = mapped_column(Integer)                     # < 0: a reversal
    scrap: Mapped[int] = mapped_column(Integer)
    final: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = mapped_column(TS, index=True)


class ErpStockMove(Base):
    """A goods movement of a finished good in the books of the ERP; the stock is the sum.
    GR goods receipt from production (101), GR_REV its reversal (102), GI goods issue to a customer (601),
    COUNT the difference found by a stock count (701 / 702)."""
    __tablename__ = "erp_stock_move"
    id: Mapped[int] = mapped_column(primary_key=True)
    material: Mapped[str] = mapped_column(String(30), index=True)
    qty: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8))
    ref: Mapped[str] = mapped_column(String(40))
    customer: Mapped[str | None] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(TS, index=True)
