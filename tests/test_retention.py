from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_lab.mes import models as db
from mes_lab.mes.retention import purge

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)


def _unit(s, serial, status, age_days, material_id):
    t = NOW - timedelta(days=age_days)
    u = db.Unit(serial=serial, kind="UNIT", material_id=material_id, status=status, completed=[],
                created_at=t, updated_at=t)
    s.add(u)
    s.flush()
    station = s.scalar(select(db.Station.id).limit(1))
    s.add(db.OperationRecord(unit_id=u.id, station_id=station, step="ICT", started_at=t, ended_at=t,
                             result="PASS", measurements={}))
    return u


def test_old_finished_units_and_history_are_purged(app):
    with Session(app.state.engine) as s:
        mid = s.scalar(select(db.Material.id).where(db.Material.code == "BCM-200"))
        _unit(s, "OLD-PACKED", "PACKED", 40, mid)
        _unit(s, "OLD-IN-PROCESS", "IN_PROCESS", 40, mid)   # not finished: kept
        _unit(s, "NEW-PACKED", "PACKED", 2, mid)
        s.commit()
        result = purge(s, NOW, keep_days=21)
        assert result["units"] == 1 and result["records"] == 2
        left = set(s.scalars(select(db.Unit.serial)))
        assert left == {"OLD-IN-PROCESS", "NEW-PACKED"}
        assert s.scalar(select(func.count()).select_from(db.OperationRecord)) == 1
