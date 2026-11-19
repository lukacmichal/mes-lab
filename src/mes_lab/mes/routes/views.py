"""The read models of the pages: the plant, the lines, the floor, the orders, traceability, shipments."""

from __future__ import annotations

import json
import logging

from fastapi import Depends, FastAPI, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_lab.layout import build_layout

from .. import control
from .. import models as db
from ..context import Mes
from ..views import Clock

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/api/v1/clock", tags=["views"])
    def plant_clock(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        return {"clock": Clock(session, m.model).as_dict()}

    @app.get("/api/v1/plant/overview", tags=["views"])
    def overview(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        return m.views.overview(session)

    @app.get("/api/v1/material", tags=["views"])
    def material_view(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """SMT material: the reels on the feeders, the paste on the printers, the scans, the floor life."""
        clock = Clock(session, m.model)
        lines = clock.run.lines if clock.run is not None else None
        return m.material.view(session, clock.now, clock.as_dict(), lines)

    @app.get("/api/v1/material/lot/{lot}", tags=["views"])
    def material_lot(lot: str, session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """Where a supplier lot went: its reels and everything made with it."""
        clock = Clock(session, m.model)
        reels = session.scalars(select(db.Reel).where(db.Reel.lot == lot).order_by(db.Reel.serial)).all()
        jars = session.scalars(select(db.PasteJar).where(db.PasteJar.lot == lot).order_by(db.PasteJar.serial)).all()
        panels = m.material.lot_units(session, lot)
        affected = m.trace._affected_ids(session, panels)
        rows = session.execute(select(db.Unit.kind, db.Unit.status, func.count()).where(db.Unit.id.in_(affected))
                               .group_by(db.Unit.kind, db.Unit.status)).all() if affected else []
        return {"clock": clock.as_dict(), "lot": lot,
                "reels": [{"serial": r.serial, "part": r.part, "status": r.status, "left": r.left, "qty": r.qty} for r in reels],
                "jars": [{"serial": j.serial, "status": j.status} for j in jars],
                "panels": len(panels), "affected": len(affected),
                "by_kind": [{"kind": k, "status": s, "count": c} for k, s, c in rows]}

    @app.get("/api/v1/andon", tags=["views"])
    def andon(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """The andon: the calls open now with their level, the escalations the plant reported just now,
        and the breakdowns of the last 24 hours long enough to escalate (level by duration)."""
        crew = {}
        if m.floor.body is not None:
            crew = (json.loads(m.floor.body).get("crew") or {})
        return m.views.andon(session, crew, m.model.crew.andon)

    @app.get("/api/v1/boards", tags=["views"])
    def boards(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """The hour-by-hour boards over the lines: plan and output per hour of this shift, the totals of
        this and of the last shift (the screens over the aisles and the shift meeting)."""
        return m.views.hourly.boards(session, Clock(session, m.model))

    @app.get("/api/v1/lines/{code}", tags=["views"])
    def line_view(code: str, session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        data = m.views.line(session, code)
        if data is None:
            raise HTTPException(404, f"unknown line {code}")
        return data

    @app.get("/api/v1/floor", tags=["views"])
    def floor_now(user: str = Depends(m.viewer)) -> Response:
        """Where every piece is right now (plain JSON, straight from memory)."""
        return Response(m.floor.response(), media_type="application/json",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/floor/layout", tags=["views"])
    def floor_layout(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """The floor plan of the live plant: machines, conveyors and warehouses with their coordinates (a
        tuning scenario in the live plant may have fewer lines)."""
        run = control.current_run(session)
        knobs = run.knobs if run is not None else None
        key = json.dumps(knobs, sort_keys=True)
        if key not in m.layouts:
            from mes_lab import tuning
            m.layouts[key] = build_layout(tuning.model_for(knobs, m.config_dir, m.store.in_force(session))).data
        return {**m.layouts[key], "run": run.id if run is not None else None}

    @app.get("/api/v1/kpi", tags=["views"])
    def kpi_view(window: str = "shift", session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """OEE, yields, automation, productivity, reliability, robots and the cost of a good unit over a window
        of plant time: shift (since the start of this shift), 8h, 24h or 7d."""
        return m.kpis.page(session, window)

    @app.get("/api/v1/orders", tags=["views"])
    def orders(status: str | None = None, limit: int = 200, session: Session = Depends(m.get_session),
               user: str = Depends(m.viewer)) -> dict:
        return m.views.orders(session, status, max(1, min(limit, 1000)))

    @app.get("/api/v1/trace/search", tags=["traceability"])
    def trace_search(q: str, session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        return m.trace_views.search(session, q)

    @app.get("/api/v1/trace/units/{serial}", tags=["traceability"])
    def trace_unit(serial: str, session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        return m.trace_views.unit(session, serial)

    @app.get("/api/v1/shipments", tags=["shipping"])
    def shipments(limit: int = 30, session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        return m.trace_views.shipments(session, max(1, min(limit, 200)))

    @app.get("/api/v1/shipments/{asn}", tags=["shipping"])
    def shipment(asn: str, session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        return m.trace_views.shipment(session, asn)
