"""Quality: SPC, the master sample checks, defects, holds and the quarantine."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as db
from ..context import Mes
from ..schemas import Disposition, HoldCreate, HoldRelease
from ..views import Clock

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/api/v1/spc", tags=["quality"])
    def spc_overview(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """Every characteristic under SPC with its last point, and the alarms."""
        clock = Clock(session, m.model)
        alarms = session.scalars(select(db.SpcAlarm).order_by(db.SpcAlarm.id.desc()).limit(30)).all()
        return {"clock": clock.as_dict(), "chars": m.spc.overview(session, clock),
                "alarms": [m.spc.alarm_out(a, clock) for a in alarms]}

    @app.get("/api/v1/spc/chart", tags=["quality"])
    def spc_chart(station: str, name: str, session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """The X-bar / R chart of one characteristic of one station: subgroups, limits, the rules that fired."""
        if (station, name) not in {(c, n) for c, _, n in m.spc.chars}:
            raise HTTPException(404, "no such characteristic under SPC")
        return m.spc.chart(session, station, name, Clock(session, m.model))

    @app.get("/api/v1/verifications", tags=["quality"])
    def verifications(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """The master sample checks of the testers: the last one of each, and whether it covers this shift."""
        return m.views.verifications(session, m.trace)

    @app.get("/api/v1/quality", tags=["quality"])
    def quality(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        return m.trace_views.quality(session)

    @app.get("/api/v1/holds", tags=["quality"])
    def holds(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        return m.trace_views.holds(session)

    @app.post("/api/v1/holds/preview", tags=["quality"])
    def hold_preview(body: HoldCreate, session: Session = Depends(m.get_session),
                     user: str = Depends(m.need("quality"))) -> dict:
        return m.trace.preview_hold(session, body, m.plant_now(session))

    @app.post("/api/v1/holds", tags=["quality"])
    def hold_create(body: HoldCreate, session: Session = Depends(m.get_session),
                    user: str = Depends(m.need("quality"))) -> dict:
        hold = m.trace.create_hold(session, body, user, m.plant_now(session))
        return {"id": hold.id, "affected": hold.affected}

    @app.post("/api/v1/holds/{hold_id}/release", tags=["quality"])
    def hold_release(hold_id: int, body: HoldRelease, session: Session = Depends(m.get_session),
                     user: str = Depends(m.need("quality"))) -> dict:
        return m.trace.release_hold(session, hold_id, body.disposition, user, m.plant_now(session))

    @app.post("/api/v1/quarantine/{serial}/disposition", tags=["quality"])
    def disposition(serial: str, body: Disposition, session: Session = Depends(m.get_session),
                    user: str = Depends(m.need("quality"))) -> dict:
        unit = m.trace.unit(session, serial)
        if unit is None or unit.status != "QUARANTINE":
            raise HTTPException(409, f"{serial} is not in quarantine")
        hold = m.trace.active_hold(session, unit)
        if body.action == "RETURN" and hold is not None:
            raise HTTPException(409, f"{serial} is still on hold #{hold.id}; release the hold first")
        m.trace.dispose(session, unit, body.action, m.plant_now(session))
        session.commit()
        return {"serial": serial, "action": body.action}
