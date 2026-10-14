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

from datetime import datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    kind: Mapped[str] = mapped_column(String(20))  # SERIAL WORK_ORDER STATION_WINDOW
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


class SimCommand(Base):
    """Instruction for the shop floor (release or scrap an item in quarantine),
    delivered with ingest responses until the plant confirms it."""
    __tablename__ = "sim_command"
    id: Mapped[int] = mapped_column(primary_key=True)
    serial: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(12))  # RETURN SCRAP
    created_at: Mapped[datetime] = mapped_column(TS)
    acked_at: Mapped[datetime | None] = mapped_column(TS, index=True)


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(TS)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)
