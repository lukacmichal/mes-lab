"""The shop-floor API: what the plant (the simulation) reports and asks."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_lab import __version__

from .. import control
from .. import models as db
from ..context import Mes
from ..ingest import ingest
from ..schemas import (
    CheckInRequest,
    IngestPayload,
    MaterialScanRequest,
    NextJobRequest,
    RepairRequest,
    RosterReport,
    RunRegistration,
    ShipCheck,
    StockReport,
    VerificationReport,
)

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.post("/api/v1/sim/runs", tags=["shop floor"], dependencies=[Depends(m.shop_floor)])
    def register_run(body: RunRegistration, session: Session = Depends(m.get_session)) -> dict:
        run = control.register_run(session, body.run_id, body.seed, body.speed, trace=m.trace,
                                   initial_stock=body.initial_stock, lines=body.lines, knobs=body.knobs,
                                   plant_start=body.plant_start, erp_link=m.erp_link if m.model.erp.enabled else None)
        m.erp_clock.clear()
        run.settings_rev = body.settings_rev
        session.commit()
        if body.plant_start is not None:
            m.sim_control.cancel("the new day")          # a jump of the day before does not carry over
        return {"run_id": run.id, "plant_start": run.plant_start.isoformat()}

    @app.get("/api/v1/sim/scenario", tags=["shop floor"], dependencies=[Depends(m.shop_floor)])
    def sim_scenario() -> dict:
        """The plant the simulation is to build: the knobs of a tuning scenario (None: as configured), the
        settings in force (the revision and its overrides of the config files) and the version of that wish
        (the answer to every report carries it: a new one = build anew)."""
        with m.sessions() as session:
            rev = m.store.active(session)
            return {**m.scenario.state(), "settings": {"revision": rev.number if rev else None,
                                                     "overrides": (rev.overrides or {}) if rev else {}}}

    @app.post("/api/v1/dispatch/next-job", tags=["shop floor"], dependencies=[Depends(m.shop_floor)],
              responses={204: {"description": "nothing to do"}})
    def next_job(body: NextJobRequest, session: Session = Depends(m.get_session)):
        run = session.get(db.SimRun, body.run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        m.erp_tick(session, body.plant_time)
        job = control.next_job(session, m.planner, run, body.line, body.plant_time)
        if job is None:
            return Response(status_code=204)
        return {"id": job.id, "order_no": job.work_order.order_no,
                "material": job.work_order.material.code, "operation": job.operation.code,
                "qty": job.qty}

    @app.post("/api/v1/edge/ingest", tags=["shop floor"], dependencies=[Depends(m.shop_floor)])
    def edge_ingest(payload: IngestPayload, session: Session = Depends(m.get_session)) -> dict:
        adjusted = []
        if payload.acks:                         # stations the plant adjusted after an SPC alarm
            adjusted = list(session.scalars(select(db.SimCommand.serial).where(
                db.SimCommand.id.in_(payload.acks), db.SimCommand.action == "ADJUST", db.SimCommand.acked_at.is_(None))))
        result = ingest(session, m.stations, m.trace, payload)
        for code in adjusted:
            m.spc.adjusted(session, code, payload.plant_time)
        if adjusted:
            session.commit()
        if m.spc.due():
            m.spc.evaluate(session, payload.plant_time)
        m.floor.put(payload.floor)
        result["calls"] = m.calls.take()           # people called from a page: the plant sends them
        result["skills"] = m.qualifications.decisions(session)   # qualifications decided on the web
        m.sim_control.reached(payload.plant_time)
        result.update(m.sim_control.for_plant())   # pause / go to a time
        m.store.due(session, payload.plant_time)   # a revision of the settings whose time has come
        m.erp_tick(session, payload.plant_time)    # the ERP plans, the confirmations and the delivery notes go
        m.uns_tick(session, payload.plant_time)    # the MES's data into the Unified Namespace
        try:                                     # a training scenario: its fault ended (fixed, held, by itself)?
            m.training.tick(session, payload.plant_time, payload.live, session.get(db.SimRun, payload.run_id))
        except Exception:  # noqa: BLE001 - the shop floor must not stop for the training page
            session.rollback()
            log.exception("training tick failed")
        result["scenario"] = m.scenario.version    # another plant asked for (tuning, settings): the plant builds anew
        return result

    @app.post("/api/v1/trace/ship", tags=["shop floor"], dependencies=[Depends(m.shop_floor)])
    def ship_check(body: ShipCheck, session: Session = Depends(m.get_session)) -> dict:
        """The pallet label scanned at the truck: a pallet on hold stays in the plant."""
        return m.trace.ship_check(session, body)

    @app.post("/api/v1/trace/check-in", tags=["shop floor"], dependencies=[Depends(m.shop_floor)])
    def check_in(body: CheckInRequest, session: Session = Depends(m.get_session)) -> dict:
        """May this station work on this unit? Issues serial numbers where needed."""
        return m.trace.check_in(session, body)

    @app.post("/api/v1/sim/people", tags=["shop floor"], dependencies=[Depends(m.shop_floor)])
    def sim_people(body: RosterReport, session: Session = Depends(m.get_session)) -> dict:
        """The plant reports its people and their qualifications (at the start of a run)."""
        return m.trace.roster(session, body)

    @app.post("/api/v1/trace/verification", tags=["shop floor"], dependencies=[Depends(m.shop_floor)])
    def verification(body: VerificationReport, session: Session = Depends(m.get_session)) -> dict:
        """The master sample check of a tester: a passed one lets it test production in this shift."""
        return m.trace.verification(session, body)

    @app.post("/api/v1/trace/material", tags=["shop floor"], dependencies=[Depends(m.shop_floor)])
    def material_scan(body: MaterialScanRequest, session: Session = Depends(m.get_session)) -> dict:
        """A scan of SMT material at a machine (a reel at a feeder, a paste jar at a printer): may it go on?"""
        return m.material.scan(session, body)

    @app.post("/api/v1/sim/reels", tags=["shop floor"], dependencies=[Depends(m.shop_floor)])
    def sim_reels(body: StockReport, session: Session = Depends(m.get_session)) -> dict:
        """The plant reports the reels in its store at goods-in (at the start of a run)."""
        return m.material.stock(session, body)

    @app.post("/api/v1/trace/repair", tags=["shop floor"], dependencies=[Depends(m.shop_floor)])
    def repair(body: RepairRequest, session: Session = Depends(m.get_session)) -> dict:
        """Outcome of a repair; the answer says where the unit goes next."""
        return m.trace.repair(session, body)
