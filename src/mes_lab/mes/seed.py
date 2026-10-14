"""Bring master data in the database in line with the YAML plant model.

Records are matched by their code: new ones are added, existing ones updated.
Nothing is deleted, because production history refers to it; codes missing
from the YAML are only reported.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mes_lab.model.schema import PlantModel

from . import models as db

log = logging.getLogger("mes_lab.seed")


def _upsert(session: Session, cls, key: dict, values: dict):
    obj = session.scalars(select(cls).filter_by(**key)).one_or_none()
    if obj is None:
        obj = cls(**key, **values)
        session.add(obj)
    else:
        for name, value in values.items():
            setattr(obj, name, value)
    session.flush()
    return obj


def sync_master_data(session: Session, model: PlantModel) -> None:
    p = model.plant
    plant = _upsert(session, db.Plant, {"code": p.code},
                    {"name": p.name, "company": p.company, "timezone": p.timezone})

    types = {}
    for t in model.station_types.values():
        types[t.code] = _upsert(session, db.StationType, {"code": t.code},
                                {"name": t.name, "kind": t.kind.value, "tag": t.tag})
    for c in model.downtime_reasons.values():
        _upsert(session, db.DowntimeReason, {"code": c.code}, {"name": c.name, "category": c.category})
    for c in model.defect_codes.values():
        _upsert(session, db.DefectCode, {"code": c.code}, {"name": c.name, "category": c.category})
    customers = {c.code: _upsert(session, db.Customer, {"code": c.code},
                                 {"name": c.name, "country": c.country})
                 for c in model.customers.values()}

    materials = {}
    for mat in sorted(model.materials.values(), key=lambda m: m.kind != "PCBA"):
        materials[mat.code] = _upsert(session, db.Material, {"code": mat.code}, {
            "name": mat.name,
            "kind": mat.kind,
            "customer_id": customers[mat.customer].id if mat.customer else None,
            "pcba_id": materials[mat.pcba].id if mat.pcba else None,
            "boards_per_panel": mat.boards_per_panel,
            "klt_qty": mat.packing.klt_qty if mat.packing else None,
            "klt_per_pallet": mat.packing.klt_per_pallet if mat.packing else None,
            "safety_stock": mat.planning.safety_stock or None,
        })
        for seq, op in enumerate(mat.routing, start=1):
            operation = _upsert(session, db.Operation,
                                {"material_id": materials[mat.code].id, "code": op.op},
                                {"seq": seq, "name": op.name})
            wanted = {types[t].id: cycle for t, cycle in op.steps.items()}
            session.execute(delete(db.OperationStep).where(
                db.OperationStep.operation_id == operation.id,
                db.OperationStep.station_type_id.not_in(wanted)))
            for type_id, cycle in wanted.items():
                _upsert(session, db.OperationStep,
                        {"operation_id": operation.id, "station_type_id": type_id},
                        {"ideal_cycle_s": cycle})

    for area_sort, area in enumerate(model.areas):
        area_row = _upsert(session, db.Area, {"code": area.code},
                           {"plant_id": plant.id, "name": area.name, "kind": area.kind.value,
                            "sort": area_sort})
        for line_sort, line_code in enumerate(area.lines):
            line = model.lines[line_code]
            line_row = _upsert(session, db.Line, {"code": line.code}, {
                "area_id": area_row.id, "name": line.name, "job_line": line.is_job_line,
                "buffer": line.buffer, "output_type_id": types[line.output].id, "sort": line_sort,
            })
            for sort, st in enumerate(line.stations):
                _upsert(session, db.Station, {"code": st.code}, {
                    "line_id": line_row.id, "station_type_id": types[st.type].id,
                    "name": st.name, "cell": st.cell, "slot": st.slot, "sort": sort,
                })
            session.execute(delete(db.LineMaterial).where(db.LineMaterial.line_id == line_row.id))
            for mat_code in line.materials:
                session.add(db.LineMaterial(line_id=line_row.id, material_id=materials[mat_code].id))

    for shift in model.shifts:
        _upsert(session, db.Shift, {"code": shift.code},
                {"name": shift.name, "start": shift.start, "end": shift.end})

    known = set(model.stations)
    stale = [c for c in session.scalars(select(db.Station.code)) if c not in known]
    if stale:
        log.warning("stations in the database but not in the YAML model: %s", ", ".join(stale))
    session.commit()
