"""The web pages (HTML)."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as db
from ..context import Mes

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/robots.txt", include_in_schema=False)
    def robots() -> Response:
        return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request):
        return m.templates.TemplateResponse(request, "login.html", {"active": "login"})

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def floor_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "floor.html", session, active="floor")

    @app.get("/overview", response_class=HTMLResponse, include_in_schema=False)
    def overview_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "overview.html", session, active="overview")

    @app.get("/lines/{code}", response_class=HTMLResponse, include_in_schema=False)
    def line_page(code: str, request: Request, session: Session = Depends(m.get_session)):
        line = session.scalar(select(db.Line).where(db.Line.code == code))
        if line is None:
            raise HTTPException(404, f"unknown line {code}")
        return m.page(request, "line.html", session, active="lines", line=line)

    @app.get("/people", response_class=HTMLResponse, include_in_schema=False)
    def people_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "people.html", session, active="people", absence=m.model.crew.absence)

    @app.get("/material", response_class=HTMLResponse, include_in_schema=False)
    def material_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "material.html", session, active="material", rules=m.model.smt_material)

    @app.get("/spc", response_class=HTMLResponse, include_in_schema=False)
    def spc_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "spc.html", session, active="spc")

    @app.get("/andon", response_class=HTMLResponse, include_in_schema=False)
    def andon_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "andon.html", session, active="andon", rule=m.model.crew.andon)

    @app.get("/kpi", response_class=HTMLResponse, include_in_schema=False)
    def kpi_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "kpi.html", session, active="kpi", costs=m.model.costs)

    @app.get("/tuning", response_class=HTMLResponse, include_in_schema=False)
    def tuning_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "tuning.html", session, active="tuning")

    @app.get("/orders", response_class=HTMLResponse, include_in_schema=False)
    def orders_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "orders.html", session, active="orders")

    @app.get("/connect", response_class=HTMLResponse, include_in_schema=False)
    def connect_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "connect.html", session, active="connect", opcua_url=m.opcua_url,
                    broker=m.uns.status()["broker"] if m.uns is not None else None)

    @app.get("/training", response_class=HTMLResponse, include_in_schema=False)
    def training_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "training.html", session, active="training")

    @app.get("/erp", response_class=HTMLResponse, include_in_schema=False)
    def erp_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "erp.html", session, active="erp", rules=m.model.erp)

    @app.get("/trace", response_class=HTMLResponse, include_in_schema=False)
    def trace_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "trace.html", session, active="trace")

    @app.get("/quality", response_class=HTMLResponse, include_in_schema=False)
    def quality_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "quality.html", session, active="quality")

    @app.get("/holds", response_class=HTMLResponse, include_in_schema=False)
    def holds_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "holds.html", session, active="holds")

    @app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
    def settings_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "settings.html", session, active="settings")

    @app.get("/guide", response_class=HTMLResponse, include_in_schema=False)
    def guide_page(request: Request, session: Session = Depends(m.get_session)):
        return m.page(request, "guide.html", session, active="guide", **m.guide)
