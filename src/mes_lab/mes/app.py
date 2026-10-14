"""MES web application: shop-floor API for the plant and web pages for people."""

from __future__ import annotations

import json
import logging
import os
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from mes_lab import __version__
from mes_lab.layout import build_layout
from mes_lab.model import AreaKind, load_model

from . import auth, control, floor as floor_mod, glossary, health, retention
from .floor import CallQueue, FloorStore, SimControl
from . import models as db
from .db import make_engine, make_sessionmaker, migrate
from .ingest import StationDirectory, ingest
from .kpi_views import KpiViews
from .scenario import ScenarioStore
from .tuning_views import Tuning
from .schemas import (
    CheckInRequest,
    CrewCallRequest,
    Disposition,
    HoldCreate,
    HoldRelease,
    IngestPayload,
    GotoRequest,
    NextJobRequest,
    PauseRequest,
    RepairRequest,
    RunRegistration,
    ScenarioRequest,
    SpeedRequest,
)
from .seed import sync_master_data
from .trace import Trace
from .trace_views import TraceViews
from .views import Clock, Views

log = logging.getLogger("mes_lab.mes")
WEB_DIR = Path(__file__).parent / "web"


class LoginRequest(BaseModel):
    username: str
    password: str


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


LOGIN_MODES = ("off", "actions", "all")


def login_mode(require_login: bool | None = None, login: str | None = None) -> str:
    """off: nobody signs in (local development); actions: anybody may look, changing
    something needs a signed-in user; all: everything needs a signed-in user.
    Taken from MES_LOGIN (or the older MES_REQUIRE_LOGIN=1 = all)."""
    if login is None:
        if require_login is not None:
            login = "all" if require_login else "off"
        else:
            login = os.environ.get("MES_LOGIN") or ("all" if _flag("MES_REQUIRE_LOGIN") else "off")
    login = login.strip().lower()
    if login not in LOGIN_MODES:
        raise ValueError(f"MES_LOGIN must be one of {LOGIN_MODES}, not {login!r}")
    return login


def create_app(database_url: str | None = None, config_dir: str | Path | None = None,
               edge_token: str | None = None, require_login: bool | None = None,
               login: str | None = None) -> FastAPI:
    model = load_model(config_dir)
    engine = make_engine(database_url)
    migrate(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as session:
        sync_master_data(session, model)

    keep_days = int(os.environ.get("MES_RETENTION_DAYS") or 0)
    if keep_days > 0:
        retention.start(sessions, model, keep_days)

    planner = control.Planner(model)
    views = Views(model)
    trace = Trace(model)
    trace_views = TraceViews(model, trace)
    stations = StationDirectory()
    floor = FloorStore()
    calls = CallQueue()
    sim_control = floor_mod.CONTROL = SimControl()
    layout = build_layout(model)
    kpis = KpiViews(model, len(layout.seats))
    var_dir = Path(os.environ.get("MES_VAR_DIR") or "var")
    tuner = Tuning(var_dir / "tuning.json")
    scenario = ScenarioStore(var_dir / "scenario.json")
    throttle = auth.LoginThrottle()
    token = edge_token if edge_token is not None else os.environ.get("MES_EDGE_TOKEN")
    policy = login_mode(require_login, login)
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    templates.env.globals.update(version=__version__, plant=model.plant, login_enabled=policy != "off",
                                 station_help=glossary.STATIONS, state_help=glossary.STATES,
                                 unit_help=glossary.UNIT_STATUS)

    app = FastAPI(title="ACME Plant 1 - MES", version=__version__,
                  description="Manufacturing execution system of a fictional automotive "
                              "electronics plant. Shop-floor API and read models.",
                  docs_url="/docs" if policy == "off" else None, redoc_url=None,
                  openapi_url="/openapi.json" if policy == "off" else None)
    app.state.model = model
    app.state.engine = engine
    app.add_middleware(SessionMiddleware,
                       secret_key=os.environ.get("MES_SECRET_KEY") or secrets.token_urlsafe(32),
                       session_cookie="mes_session", max_age=14 * 24 * 3600, same_site="lax",
                       https_only=_flag("MES_COOKIE_SECURE"))
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.middleware("http")
    async def count_errors(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            health.ERRORS.add(request.url.path)
            raise
        if response.status_code >= 500:
            health.ERRORS.add(request.url.path)
        return response

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    def shop_floor(authorization: str | None = Header(default=None)) -> None:
        """Shop-floor endpoints require MES_EDGE_TOKEN when it is configured."""
        if token and not (authorization and secrets.compare_digest(authorization, f"Bearer {token}")):
            raise HTTPException(401, "edge token required")

    def signed_in(request: Request) -> str | None:
        return "local" if policy == "off" else request.session.get("user")

    def viewer(request: Request) -> str:
        """Who is looking; with MES_LOGIN=all only signed-in users may."""
        user = signed_in(request)
        if policy == "all" and not user:
            raise HTTPException(401, "login required")
        return user or "guest"

    def actor(request: Request) -> str:
        """Who is changing something: always a signed-in user (unless login is off)."""
        user = signed_in(request)
        if not user:
            raise HTTPException(401, "sign in to do this")
        return user

    def plant_now(session: Session) -> datetime:
        return Clock(session, model).now

    # ------------------------------------------------------------------ shop floor
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.post("/api/v1/sim/runs", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def register_run(body: RunRegistration, session: Session = Depends(get_session)) -> dict:
        run = control.register_run(session, body.run_id, body.seed, body.speed, trace=trace,
                                   initial_stock=body.initial_stock, lines=body.lines, knobs=body.knobs)
        return {"run_id": run.id, "plant_start": run.plant_start.isoformat()}

    @app.get("/api/v1/sim/scenario", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def sim_scenario() -> dict:
        """The plant the simulation is to build: the knobs of a tuning scenario (None: as configured) and
        the version of that wish (the answer to every report carries it: a new one = build anew)."""
        return scenario.state()

    @app.post("/api/v1/dispatch/next-job", tags=["shop floor"], dependencies=[Depends(shop_floor)],
              responses={204: {"description": "nothing to do"}})
    def next_job(body: NextJobRequest, session: Session = Depends(get_session)):
        run = session.get(db.SimRun, body.run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        job = control.next_job(session, planner, run, body.line, body.plant_time)
        if job is None:
            return Response(status_code=204)
        return {"id": job.id, "order_no": job.work_order.order_no,
                "material": job.work_order.material.code, "operation": job.operation.code,
                "qty": job.qty}

    @app.post("/api/v1/edge/ingest", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def edge_ingest(payload: IngestPayload, session: Session = Depends(get_session)) -> dict:
        result = ingest(session, stations, trace, payload)
        floor.put(payload.floor)
        result["calls"] = calls.take()           # people called from a page: the plant sends them
        sim_control.reached(payload.plant_time)
        result.update(sim_control.for_plant())   # pause / go to a time
        result["scenario"] = scenario.version    # another plant asked for (tuning): the plant builds anew
        return result

    @app.post("/api/v1/trace/check-in", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def check_in(body: CheckInRequest, session: Session = Depends(get_session)) -> dict:
        """May this station work on this unit? Issues serial numbers where needed."""
        return trace.check_in(session, body)

    @app.post("/api/v1/trace/repair", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def repair(body: RepairRequest, session: Session = Depends(get_session)) -> dict:
        """Outcome of a repair; the answer says where the unit goes next."""
        return trace.repair(session, body)

    # ------------------------------------------------------------------ login
    @app.post("/api/v1/auth/login", tags=["auth"])
    def login(body: LoginRequest, request: Request, session: Session = Depends(get_session)) -> dict:
        client = request.client.host if request.client else "?"
        if throttle.blocked(client):
            raise HTTPException(429, "too many failed attempts, try again in 10 minutes")
        user = auth.authenticate(session, body.username.strip(), body.password)
        if user is None:
            throttle.failed(client)
            raise HTTPException(401, "wrong user name or password")
        throttle.succeeded(client)
        request.session["user"] = user.username
        return {"user": user.username}

    @app.post("/api/v1/auth/logout", tags=["auth"])
    def logout(request: Request) -> dict:
        request.session.clear()
        return {"ok": True}

    # ------------------------------------------------------------------ read models
    @app.get("/api/v1/clock", tags=["views"])
    def plant_clock(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        return {"clock": Clock(session, model).as_dict()}

    @app.get("/api/v1/plant/overview", tags=["views"])
    def overview(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        return views.overview(session)

    @app.get("/api/v1/andon", tags=["views"])
    def andon(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """The andon: the calls open now with their level, the escalations the plant reported just now,
        and the breakdowns of the last 24 hours long enough to escalate (level by duration)."""
        crew = {}
        if floor.body is not None:
            crew = (json.loads(floor.body).get("crew") or {})
        return views.andon(session, crew, model.crew.andon)

    @app.get("/api/v1/boards", tags=["views"])
    def boards(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """The hour-by-hour boards over the lines: plan and output per hour of this shift, the totals of
        this and of the last shift (the screens over the aisles and the shift meeting)."""
        return views.hourly.boards(session, Clock(session, model))

    @app.get("/api/v1/lines/{code}", tags=["views"])
    def line_view(code: str, session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        data = views.line(session, code)
        if data is None:
            raise HTTPException(404, f"unknown line {code}")
        return data

    @app.get("/api/v1/floor", tags=["views"])
    def floor_now(user: str = Depends(viewer)) -> Response:
        """Where every piece is right now (plain JSON, straight from memory)."""
        return Response(floor.response(), media_type="application/json",
                        headers={"Cache-Control": "no-store"})

    layouts: dict[str, dict] = {"null": layout.data}

    @app.get("/api/v1/floor/layout", tags=["views"])
    def floor_layout(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """The floor plan of the live plant: machines, conveyors and warehouses with their coordinates (a
        tuning scenario in the live plant may have fewer lines)."""
        run = control.current_run(session)
        knobs = run.knobs if run is not None else None
        key = json.dumps(knobs, sort_keys=True)
        if key not in layouts:
            from mes_lab import tuning
            layouts[key] = build_layout(tuning.model_for(knobs, config_dir)).data
        return {**layouts[key], "run": run.id if run is not None else None}

    @app.get("/api/v1/kpi", tags=["views"])
    def kpi_view(window: str = "shift", session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """OEE, yields, automation, productivity, reliability, robots and the cost of a good unit over a window
        of plant time: shift (since the start of this shift), 8h, 24h or 7d."""
        return kpis.page(session, window)

    @app.get("/api/v1/tuning", tags=["tuning"])
    def tuning_board(hours: int = 12, user: str = Depends(viewer)) -> dict:
        """The knobs of the tuning game, the plant as it is and the best scenarios so far."""
        return tuner.board(hours)

    @app.post("/api/v1/tuning/runs", tags=["tuning"])
    def tuning_run(body: dict, request: Request, user: str = Depends(viewer)) -> dict:
        """Run a scenario offline (queued; the same knobs give the stored result at once)."""
        try:
            return tuner.submit(body.get("knobs") or {}, int(body.get("hours") or 12), user)
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from None

    @app.get("/api/v1/tuning/runs/{jid}", tags=["tuning"])
    def tuning_status(jid: int, user: str = Depends(viewer)) -> dict:
        return tuner.status(jid)

    def scenario_state(session: Session) -> dict:
        run = control.current_run(session)
        live = {"knobs": run.knobs if run else None, "lines": run.lines if run else None,
                "since": run.plant_start.isoformat() if run else None}
        wanted = scenario.state()
        return {"wanted": wanted, "live": live, "base": tuner.base,
                "pending": (wanted["knobs"] or None) != (live["knobs"] or None)}

    @app.get("/api/v1/plant/scenario", tags=["tuning"])
    def plant_scenario(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """Which plant runs live: as configured or a scenario of the tuning game, and what is asked for."""
        return scenario_state(session)

    @app.post("/api/v1/plant/scenario", tags=["tuning"])
    def set_plant_scenario(body: ScenarioRequest, session: Session = Depends(get_session),
                           user: str = Depends(actor)) -> dict:
        """Put a scenario into the live plant (knobs), or the plant as configured again (null). The plant
        starts a new run with it within a second or two: the shop floor starts empty."""
        scenario.set(body.knobs, user)
        return scenario_state(session)

    @app.on_event("shutdown")
    def stop_tuning() -> None:
        if tuner._pool is not None:
            tuner._pool.shutdown(wait=False, cancel_futures=True)

    @app.get("/api/v1/orders", tags=["views"])
    def orders(status: str | None = None, limit: int = 200, session: Session = Depends(get_session),
               user: str = Depends(viewer)) -> dict:
        return views.orders(session, status, min(limit, 1000))

    @app.get("/api/v1/trace/search", tags=["traceability"])
    def trace_search(q: str, session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        return trace_views.search(session, q)

    @app.get("/api/v1/trace/units/{serial}", tags=["traceability"])
    def trace_unit(serial: str, session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        return trace_views.unit(session, serial)

    @app.get("/api/v1/shipments", tags=["shipping"])
    def shipments(limit: int = 30, session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        return trace_views.shipments(session, min(limit, 200))

    @app.get("/api/v1/shipments/{asn}", tags=["shipping"])
    def shipment(asn: str, session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        return trace_views.shipment(session, asn)

    @app.get("/api/v1/quality", tags=["quality"])
    def quality(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        return trace_views.quality(session)

    @app.get("/api/v1/holds", tags=["quality"])
    def holds(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        return trace_views.holds(session)

    # ------------------------------------------------------------------ actions
    @app.post("/api/v1/holds/preview", tags=["quality"])
    def hold_preview(body: HoldCreate, session: Session = Depends(get_session),
                     user: str = Depends(actor)) -> dict:
        return trace.preview_hold(session, body, plant_now(session))

    @app.post("/api/v1/holds", tags=["quality"])
    def hold_create(body: HoldCreate, session: Session = Depends(get_session),
                    user: str = Depends(actor)) -> dict:
        hold = trace.create_hold(session, body, user, plant_now(session))
        return {"id": hold.id, "affected": hold.affected}

    @app.post("/api/v1/holds/{hold_id}/release", tags=["quality"])
    def hold_release(hold_id: int, body: HoldRelease, session: Session = Depends(get_session),
                     user: str = Depends(actor)) -> dict:
        return trace.release_hold(session, hold_id, body.disposition, user, plant_now(session))

    @app.post("/api/v1/quarantine/{serial}/disposition", tags=["quality"])
    def disposition(serial: str, body: Disposition, session: Session = Depends(get_session),
                    user: str = Depends(actor)) -> dict:
        unit = trace.unit(session, serial)
        if unit is None or unit.status != "QUARANTINE":
            raise HTTPException(409, f"{serial} is not in quarantine")
        hold = trace.active_hold(session, unit)
        if body.action == "RETURN" and hold is not None:
            raise HTTPException(409, f"{serial} is still on hold #{hold.id}; release the hold first")
        trace.dispose(session, unit, body.action, plant_now(session))
        session.commit()
        return {"serial": serial, "action": body.action}

    @app.post("/api/v1/crew/calls", tags=["simulation"])
    def crew_call(body: CrewCallRequest, user: str = Depends(actor)) -> dict:
        """Call somebody of a department to a machine. The plant gets the call with the answer to its
        next report (within a second), puts it on the andon board and sends the nearest free person."""
        if body.station not in model.stations:
            raise HTTPException(404, f"unknown station {body.station}")
        call = calls.add(body.station, body.role, body.minutes, body.meter, user)
        return {"id": call["id"], "queued": True}

    @app.get("/api/v1/crew/calls", tags=["simulation"])
    def crew_calls(user: str = Depends(viewer)) -> dict:
        return {"pending": calls.pending, "sent": list(calls.sent)}

    @app.post("/api/v1/sim/speed", tags=["simulation"])
    def set_speed(body: SpeedRequest, session: Session = Depends(get_session),
                  user: str = Depends(actor)) -> dict:
        run = control.current_run(session)
        if run is None:
            raise HTTPException(409, "no simulation run yet")
        run.desired_speed = body.speed
        session.commit()
        return {"desired_speed": run.desired_speed}

    @app.post("/api/v1/sim/pause", tags=["simulation"])
    def pause(body: PauseRequest, user: str = Depends(actor)) -> dict:
        """Stop the plant's clock (machines, people, robots stand where they are) or let it run again."""
        sim_control.pause(body.paused, user)
        return sim_control.state()

    @app.post("/api/v1/sim/goto", tags=["simulation"])
    def goto(body: GotoRequest, session: Session = Depends(get_session), user: str = Depends(actor)) -> dict:
        """Run the plant as fast as it goes until a later plant time (it cannot go back)."""
        try:
            sim_control.jump(body.until, body.then, plant_now(session), user)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return sim_control.state()

    @app.delete("/api/v1/sim/goto", tags=["simulation"])
    def goto_cancel(user: str = Depends(actor)) -> dict:
        sim_control.cancel()
        return sim_control.state()

    # ------------------------------------------------------------------ pages
    def nav_lines(session: Session) -> list[dict]:
        rows = session.execute(select(db.Area.code, db.Area.name, db.Line.code, db.Line.name)
                               .join(db.Line, db.Line.area_id == db.Area.id)
                               .order_by(db.Area.sort, db.Line.sort)).all()
        areas: dict[str, dict] = {}
        for area_code, area_name, line_code, line_name in rows:
            areas.setdefault(area_code, {"name": area_name, "lines": []})["lines"].append(
                {"code": line_code, "name": line_name})
        return list(areas.values())

    def page(request: Request, template: str, session: Session, **context):
        user = signed_in(request)
        if policy == "all" and not user:
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
        return templates.TemplateResponse(request, template, {
            "nav": nav_lines(session), "user": user if policy != "off" else None,
            "can_act": bool(user), "year": datetime.now(UTC).year, **context})

    @app.get("/robots.txt", include_in_schema=False)
    def robots() -> Response:
        return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"active": "login"})

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def floor_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "floor.html", session, active="floor")

    @app.get("/overview", response_class=HTMLResponse, include_in_schema=False)
    def overview_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "overview.html", session, active="overview")

    @app.get("/lines/{code}", response_class=HTMLResponse, include_in_schema=False)
    def line_page(code: str, request: Request, session: Session = Depends(get_session)):
        line = session.scalar(select(db.Line).where(db.Line.code == code))
        if line is None:
            raise HTTPException(404, f"unknown line {code}")
        return page(request, "line.html", session, active="lines", line=line)

    @app.get("/andon", response_class=HTMLResponse, include_in_schema=False)
    def andon_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "andon.html", session, active="andon", rule=model.crew.andon)

    @app.get("/kpi", response_class=HTMLResponse, include_in_schema=False)
    def kpi_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "kpi.html", session, active="kpi", costs=model.costs)

    @app.get("/tuning", response_class=HTMLResponse, include_in_schema=False)
    def tuning_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "tuning.html", session, active="tuning")

    @app.get("/orders", response_class=HTMLResponse, include_in_schema=False)
    def orders_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "orders.html", session, active="orders")

    @app.get("/trace", response_class=HTMLResponse, include_in_schema=False)
    def trace_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "trace.html", session, active="trace")

    @app.get("/quality", response_class=HTMLResponse, include_in_schema=False)
    def quality_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "quality.html", session, active="quality")

    @app.get("/holds", response_class=HTMLResponse, include_in_schema=False)
    def holds_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "holds.html", session, active="holds")

    smt_types = model.lines_of_kind(AreaKind.SMT)[0].cell_types()
    fa_types = [t for t in model.station_types
                if any(t in line.cell_types() for line in model.lines_of_kind(AreaKind.FA))]
    other_types = [t for t in model.station_types if t not in smt_types and t not in fa_types]
    used_in = {t: [line.code for line in model.lines.values() if t in line.cell_types()]
               for t in model.station_types}
    guide = dict(
        model=model, used_in=used_in, glossary=glossary.GLOSSARY, product_help=glossary.PRODUCTS,
        station_groups=[("Surface mount (SMT)", "Povrchová montáž (SMT)", smt_types),
                        ("Final assembly", "Finálna montáž", fa_types),
                        ("Packing and repair", "Balenie a opravy", other_types)])

    @app.get("/guide", response_class=HTMLResponse, include_in_schema=False)
    def guide_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "guide.html", session, active="guide", **guide)

    return app
