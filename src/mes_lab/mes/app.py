"""MES web application: shop-floor API for the plant and web pages for people."""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from mes_lab import __version__
from mes_lab.layout import build_layout
from mes_lab import settings as settings_mod
from mes_lab.model import AreaKind

from mes_lab.connect import opcua as connect_opcua, uns as connect_uns
from mes_lab.erp.bus import Bus
from mes_lab.erp.system import Erp

from . import auth, control, floor as floor_mod, glossary, health, retention
from .erp_link import ErpLink
from .erp_views import ErpViews
from .labels import Labels
from .qualifications import Qualifications, parse_day
from .training import Training
from .floor import CallQueue, FloorStore, SimControl
from . import models as db
from .db import make_engine, make_sessionmaker, migrate
from .ingest import StationDirectory, ingest
from .kpi_views import KpiViews
from .scenario import ScenarioStore
from .material import Material
from .spc import Spc
from .tuning_views import Tuning
from .schemas import (
    CheckInRequest,
    CrewCallRequest,
    Disposition,
    HoldCreate,
    HoldRelease,
    IngestPayload,
    GotoRequest,
    MaterialScanRequest,
    StockReport,
    NextJobRequest,
    PauseRequest,
    ReprintRequest,
    ShipCheck,
    SkillDecision,
    TrainingFinish,
    TrainingStart,
    RepairRequest,
    RunRegistration,
    SettingChangeRequest,
    SettingReleaseRequest,
    SettingScheduledRequest,
    UserCreate,
    UserUpdate,
    RosterReport,
    ScenarioRequest,
    SpeedRequest,
    VerificationReport,
)
from .seed import sync_master_data
from .settings_store import SettingsStore
from .settings_views import SettingsViews, audit
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


def guide_context(model) -> dict:
    """What the guide page shows of the plant (machine types by area, where each is used)."""
    smt_types = model.lines_of_kind(AreaKind.SMT)[0].cell_types()
    fa_types = [t for t in model.station_types
                if any(t in line.cell_types() for line in model.lines_of_kind(AreaKind.FA))]
    other_types = [t for t in model.station_types if t not in smt_types and t not in fa_types]
    used_in = {t: [line.code for line in model.lines.values() if t in line.cell_types()]
               for t in model.station_types}
    return dict(
        model=model, used_in=used_in, glossary=glossary.GLOSSARY, product_help=glossary.PRODUCTS,
        station_groups=[("Surface mount (SMT)", "Povrchová montáž (SMT)", smt_types),
                        ("Final assembly", "Finálna montáž", fa_types),
                        ("Packing and repair", "Balenie a opravy", other_types)])


def create_app(database_url: str | None = None, config_dir: str | Path | None = None,
               edge_token: str | None = None, require_login: bool | None = None,
               login: str | None = None) -> FastAPI:
    engine = make_engine(database_url)
    migrate(engine)
    sessions = make_sessionmaker(engine)
    store = SettingsStore(config_dir)                   # the settings page: the config files + released changes
    with sessions() as session:
        model, settings_rev = store.model(session)
        settings_overrides = store.in_force(session)
        store.load_pending(session)
        sync_master_data(session, model)

    keep_days = int(os.environ.get("MES_RETENTION_DAYS") or 0)
    if keep_days > 0:
        retention.start(sessions, model, keep_days)

    planner = views = trace = trace_views = spc = material = layout = kpis = None
    bus = erp = erp_link = erp_views = labels = qualifications = training = None
    layouts: dict[str, dict] = {}
    guide: dict = {}

    def build_parts(new_model) -> None:
        """Everything the MES builds from the plant model; built anew when a revision of the settings
        takes effect (the routes see the new parts at once)."""
        nonlocal model, planner, views, trace, trace_views, spc, material, layout, kpis, bus, erp, erp_link, erp_views, labels, qualifications, training
        model = new_model
        planner = control.Planner(model)
        bus = Bus(model.erp.interface)                  # the fake ERP and the interface to it
        erp = Erp(model, bus)
        erp_link = ErpLink(model, planner, bus)
        erp_views = ErpViews(model, erp, erp_link)
        views = Views(model)
        trace = Trace(model)
        trace_views = TraceViews(model, trace)
        spc = Spc(model, trace)
        material = Material(model, trace)
        trace.material = material
        trace.labels = labels = Labels(model, revision=lambda s: (lambda r: r.number if r else None)(store.active(s)))
        trace.qualifications = qualifications = Qualifications(model)
        training = Training(model, trace)
        layout = build_layout(model)
        kpis = KpiViews(model, len(layout.seats))
        layouts.clear()
        layouts["null"] = layout.data
        guide.clear()
        guide.update(guide_context(model))
        app.state.model = model
        templates.env.globals.update(plant=model.plant)

    stations = StationDirectory()
    floor = FloorStore()
    calls = CallQueue()
    sim_control = floor_mod.CONTROL = SimControl()
    var_dir = Path(os.environ.get("MES_VAR_DIR") or "var")
    tuner = Tuning(var_dir / "tuning.json")
    tuner.use_settings(settings_rev, settings_overrides)
    scenario = ScenarioStore(var_dir / "scenario.json")
    settings_views = SettingsViews(store)
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
    app.state.engine = engine
    app.state.settings = store
    mqtt_url = os.environ.get("MES_MQTT_URL") or None
    uns = connect_uns.UnsMirror(mqtt_url) if mqtt_url else None     # the UNS: publish and mirror the tree
    opcua_url = os.environ.get("MES_OPCUA_URL") or None
    app.state.uns = uns
    build_parts(model)

    def settings_took_effect(session: Session, rev, new_model) -> None:
        """A revision of the settings is in force: the master data in the database, the MES's own plant
        model, the tuning game; the machines get it with a new run of the plant if they need it."""
        sync_master_data(session, new_model)
        build_parts(new_model)
        tuner.use_settings(rev.number, rev.overrides or {})
        if rev.plant:
            scenario.bump(f"settings revision {rev.number}")

    store.on_activate = settings_took_effect
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
            log.info("refused, not signed in: %s %s", request.method, request.url.path)
            raise HTTPException(401, "sign in to do this")
        return user

    def perms(request: Request, session: Session) -> set[str]:
        """What the person looking may do (signed-in users by their role; local development: everything)."""
        if policy == "off":
            return set(auth.PERMS)
        name = request.session.get("user")
        if not name:
            return set()
        user = session.scalar(select(db.AppUser).where(db.AppUser.username == name))
        if user is None or not user.active:
            return set()
        return auth.perms_of(user.role)

    def need(perm: str):
        """A dependency: a signed-in user whose role may do this (403 otherwise)."""
        def check(request: Request, session: Session = Depends(get_session)) -> str:
            user = actor(request)
            if perm not in perms(request, session):
                log.info("refused, %s may not %s: %s %s", user, perm, request.method, request.url.path)
                raise HTTPException(403, f"your role may not do this ({perm})")
            return user
        return check

    def plant_now(session: Session) -> datetime:
        return Clock(session, model).now

    erp_lock = threading.Lock()
    erp_clock: dict[str, datetime] = {}

    def uns_tick(session: Session, at: datetime, every_s: float = 30.0) -> None:
        """The MES's own data in the UNS: the order each job line works on and what waits for it."""
        last = erp_clock.get("uns")
        if uns is None or (last is not None and last <= at < last + timedelta(seconds=every_s)):
            return
        erp_clock["uns"] = at
        try:
            uns.publish(connect_uns.mes_topics(model, views.line_queues(session)), at.isoformat())
        except Exception:  # noqa: BLE001 - the UNS must not stop the shop floor
            log.exception("publishing to the UNS failed")

    def erp_tick(session: Session, at: datetime, every_s: float = 30.0) -> None:
        """The ERP and the interface live in plant time: the ERP plans, the MES reports, the messages go."""
        if not model.erp.enabled or not erp_lock.acquire(blocking=False):
            return
        try:
            last = erp_clock.get("at")
            if last is not None and last <= at < last + timedelta(seconds=every_s):
                return
            erp_clock["at"] = at
            run = control.current_run(session)
            erp.tick(session, at)
            erp_link.tick(session, at, run)
            bus.deliver(session, at)
            session.commit()
        except Exception:
            session.rollback()
            health.ERRORS.add("erp")
            log.exception("the ERP tick failed at %s", at)
        finally:
            erp_lock.release()

    # ------------------------------------------------------------------ shop floor
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.post("/api/v1/sim/runs", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def register_run(body: RunRegistration, session: Session = Depends(get_session)) -> dict:
        run = control.register_run(session, body.run_id, body.seed, body.speed, trace=trace,
                                   initial_stock=body.initial_stock, lines=body.lines, knobs=body.knobs,
                                   plant_start=body.plant_start, erp_link=erp_link if model.erp.enabled else None)
        erp_clock.clear()
        run.settings_rev = body.settings_rev
        session.commit()
        if body.plant_start is not None:
            sim_control.cancel("the new day")          # a jump of the day before does not carry over
        return {"run_id": run.id, "plant_start": run.plant_start.isoformat()}

    @app.get("/api/v1/sim/scenario", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def sim_scenario() -> dict:
        """The plant the simulation is to build: the knobs of a tuning scenario (None: as configured), the
        settings in force (the revision and its overrides of the config files) and the version of that wish
        (the answer to every report carries it: a new one = build anew)."""
        with sessions() as session:
            rev = store.active(session)
            return {**scenario.state(), "settings": {"revision": rev.number if rev else None,
                                                     "overrides": (rev.overrides or {}) if rev else {}}}

    @app.post("/api/v1/dispatch/next-job", tags=["shop floor"], dependencies=[Depends(shop_floor)],
              responses={204: {"description": "nothing to do"}})
    def next_job(body: NextJobRequest, session: Session = Depends(get_session)):
        run = session.get(db.SimRun, body.run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        erp_tick(session, body.plant_time)
        job = control.next_job(session, planner, run, body.line, body.plant_time)
        if job is None:
            return Response(status_code=204)
        return {"id": job.id, "order_no": job.work_order.order_no,
                "material": job.work_order.material.code, "operation": job.operation.code,
                "qty": job.qty}

    @app.post("/api/v1/edge/ingest", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def edge_ingest(payload: IngestPayload, session: Session = Depends(get_session)) -> dict:
        adjusted = []
        if payload.acks:                         # stations the plant adjusted after an SPC alarm
            adjusted = list(session.scalars(select(db.SimCommand.serial).where(
                db.SimCommand.id.in_(payload.acks), db.SimCommand.action == "ADJUST", db.SimCommand.acked_at.is_(None))))
        result = ingest(session, stations, trace, payload)
        for code in adjusted:
            spc.adjusted(session, code, payload.plant_time)
        if adjusted:
            session.commit()
        if spc.due():
            spc.evaluate(session, payload.plant_time)
        floor.put(payload.floor)
        result["calls"] = calls.take()           # people called from a page: the plant sends them
        result["skills"] = qualifications.decisions(session)   # qualifications decided on the web
        sim_control.reached(payload.plant_time)
        result.update(sim_control.for_plant())   # pause / go to a time
        store.due(session, payload.plant_time)   # a revision of the settings whose time has come
        erp_tick(session, payload.plant_time)    # the ERP plans, the confirmations and the delivery notes go
        uns_tick(session, payload.plant_time)    # the MES's data into the Unified Namespace
        try:                                     # a training scenario: its fault ended (fixed, held, by itself)?
            training.tick(session, payload.plant_time, payload.live, session.get(db.SimRun, payload.run_id))
        except Exception:  # noqa: BLE001 - the shop floor must not stop for the training page
            session.rollback()
            log.exception("training tick failed")
        result["scenario"] = scenario.version    # another plant asked for (tuning, settings): the plant builds anew
        return result

    @app.post("/api/v1/trace/ship", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def ship_check(body: ShipCheck, session: Session = Depends(get_session)) -> dict:
        """The pallet label scanned at the truck: a pallet on hold stays in the plant."""
        return trace.ship_check(session, body)

    @app.post("/api/v1/trace/check-in", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def check_in(body: CheckInRequest, session: Session = Depends(get_session)) -> dict:
        """May this station work on this unit? Issues serial numbers where needed."""
        return trace.check_in(session, body)

    @app.post("/api/v1/sim/people", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def sim_people(body: RosterReport, session: Session = Depends(get_session)) -> dict:
        """The plant reports its people and their qualifications (at the start of a run)."""
        return trace.roster(session, body)

    @app.post("/api/v1/trace/verification", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def verification(body: VerificationReport, session: Session = Depends(get_session)) -> dict:
        """The master sample check of a tester: a passed one lets it test production in this shift."""
        return trace.verification(session, body)

    @app.post("/api/v1/trace/material", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def material_scan(body: MaterialScanRequest, session: Session = Depends(get_session)) -> dict:
        """A scan of SMT material at a machine (a reel at a feeder, a paste jar at a printer): may it go on?"""
        return material.scan(session, body)

    @app.post("/api/v1/sim/reels", tags=["shop floor"], dependencies=[Depends(shop_floor)])
    def sim_reels(body: StockReport, session: Session = Depends(get_session)) -> dict:
        """The plant reports the reels in its store at goods-in (at the start of a run)."""
        return material.stock(session, body)

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

    @app.get("/api/v1/material", tags=["views"])
    def material_view(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """SMT material: the reels on the feeders, the paste on the printers, the scans, the floor life."""
        clock = Clock(session, model)
        lines = clock.run.lines if clock.run is not None else None
        return material.view(session, clock.now, clock.as_dict(), lines)

    @app.get("/api/v1/material/lot/{lot}", tags=["views"])
    def material_lot(lot: str, session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """Where a supplier lot went: its reels and everything made with it."""
        clock = Clock(session, model)
        reels = session.scalars(select(db.Reel).where(db.Reel.lot == lot).order_by(db.Reel.serial)).all()
        jars = session.scalars(select(db.PasteJar).where(db.PasteJar.lot == lot).order_by(db.PasteJar.serial)).all()
        panels = material.lot_units(session, lot)
        affected = trace._affected_ids(session, panels)
        rows = session.execute(select(db.Unit.kind, db.Unit.status, func.count()).where(db.Unit.id.in_(affected))
                               .group_by(db.Unit.kind, db.Unit.status)).all() if affected else []
        return {"clock": clock.as_dict(), "lot": lot,
                "reels": [{"serial": r.serial, "part": r.part, "status": r.status, "left": r.left, "qty": r.qty} for r in reels],
                "jars": [{"serial": j.serial, "status": j.status} for j in jars],
                "panels": len(panels), "affected": len(affected),
                "by_kind": [{"kind": k, "status": s, "count": c} for k, s, c in rows]}

    @app.get("/api/v1/spc", tags=["quality"])
    def spc_overview(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """Every characteristic under SPC with its last point, and the alarms."""
        clock = Clock(session, model)
        alarms = session.scalars(select(db.SpcAlarm).order_by(db.SpcAlarm.id.desc()).limit(30)).all()
        return {"clock": clock.as_dict(), "chars": spc.overview(session, clock),
                "alarms": [spc.alarm_out(a, clock) for a in alarms]}

    @app.get("/api/v1/spc/chart", tags=["quality"])
    def spc_chart(station: str, name: str, session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """The X-bar / R chart of one characteristic of one station: subgroups, limits, the rules that fired."""
        if (station, name) not in {(c, n) for c, _, n in spc.chars}:
            raise HTTPException(404, "no such characteristic under SPC")
        return spc.chart(session, station, name, Clock(session, model))

    @app.get("/api/v1/people", tags=["views"])
    def people(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """The qualification matrix, today's attendance and cover, the certificates that run out."""
        crew = {}
        if floor.body is not None:
            crew = (json.loads(floor.body).get("crew") or {})
        out = views.people(session, trace, crew)
        out["history"] = qualifications.history(session)
        out["rules"] = {"certified": sorted(qualifications.certified), "valid_days": model.crew.skills.valid_days,
                        "skills": qualifications.skills,
                        "revoke_from": qualifications.next_shift(plant_now(session)).isoformat()}
        return out

    @app.post("/api/v1/people/{employee_id}/skills", tags=["people"])
    def people_skill(employee_id: int, body: SkillDecision, session: Session = Depends(get_session),
                     user: str = Depends(need("people"))) -> dict:
        """Grant a qualification (after the training), renew a certificate, or take a qualification away
        (from the start of the next shift). The plant gets it with the answer to its next report."""
        now = plant_now(session)
        entry = qualifications.change(session, employee_id, body.skill, body.action, body.reason, user, now,
                                      until=parse_day(body.until, qualifications.tz))
        session.commit()
        log.info("qualification %s %s of %s by %s: %s", body.action, body.skill, employee_id, user, body.reason)
        return {"ok": True, "change": qualifications.history(session, 1)[0], "effective_at": entry.effective_at.isoformat()}

    @app.get("/api/v1/verifications", tags=["quality"])
    def verifications(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """The master sample checks of the testers: the last one of each, and whether it covers this shift."""
        return views.verifications(session, trace)

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

    @app.get("/api/v1/floor/layout", tags=["views"])
    def floor_layout(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """The floor plan of the live plant: machines, conveyors and warehouses with their coordinates (a
        tuning scenario in the live plant may have fewer lines)."""
        run = control.current_run(session)
        knobs = run.knobs if run is not None else None
        key = json.dumps(knobs, sort_keys=True)
        if key not in layouts:
            from mes_lab import tuning
            layouts[key] = build_layout(tuning.model_for(knobs, config_dir, store.in_force(session))).data
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
                           user: str = Depends(need("plant"))) -> dict:
        """Put a scenario into the live plant (knobs), or the plant as configured again (null). The plant
        starts a new run with it within a second or two: the shop floor starts empty."""
        scenario.set(body.knobs, user, tuner.base)
        return scenario_state(session)

    @app.on_event("shutdown")
    def stop_tuning() -> None:
        if tuner._pool is not None:
            tuner._pool.shutdown(wait=False, cancel_futures=True)
        if uns is not None:
            uns.close()

    # ------------------------------------------------------------------ connectivity: the UNS and OPC UA
    @app.get("/api/v1/uns", tags=["connectivity"])
    def uns_tree(prefix: str = "", user: str = Depends(viewer)) -> dict:
        """The Unified Namespace as the MES sees it (MQTT): every topic under `prefix` with its last payload."""
        if uns is None:
            return {"status": {"configured": False}, "rows": []}
        return {"status": uns.status(), "rows": uns.tree(prefix)}

    @app.get("/api/v1/opcua/browse", tags=["connectivity"])
    def opcua_browse(node: str | None = None, user: str = Depends(viewer)) -> dict:
        """The OPC UA server of the machines: the children of a node, the values of its variables."""
        if not opcua_url:
            raise HTTPException(503, "OPC UA is not configured (MES_OPCUA_URL)")
        try:
            return connect_opcua.browse(opcua_url, node)
        except LookupError as exc:
            raise HTTPException(404, str(exc)[:300]) from None
        except Exception as exc:  # noqa: BLE001 - the server of the machines is another process
            raise HTTPException(503, f"OPC UA server not reachable: {type(exc).__name__}: {exc}"[:300]) from None

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

    # ------------------------------------------------------------------ the fake ERP
    # ------------------------------------------------------------------ training scenarios (phase 6)
    @app.get("/api/v1/training", tags=["training"])
    def training_view(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """The scenarios, the one running (its brief, the holds made since it began), the graded ones."""
        out = training.view(session, plant_now(session))
        out["clock"] = Clock(session, model).as_dict()
        return out

    @app.post("/api/v1/training/start", tags=["training"])
    def training_start(body: TrainingStart, session: Session = Depends(get_session),
                       user: str = Depends(need("plant"))) -> dict:
        """Start a scenario: a real fault in the live plant (or a complaint about a shipped unit)."""
        entry = training.start(session, body.scenario, user, plant_now(session), control.current_run(session))
        return training.out(entry, plant_now(session))

    @app.post("/api/v1/training/finish", tags=["training"])
    def training_finish(body: TrainingFinish, session: Session = Depends(get_session),
                        user: str = Depends(need("plant"))) -> dict:
        """The trainee is done: the fault stops (if it still runs) and the containment is graded."""
        entry = training.finish(session, user, plant_now(session), body.asns, body.abort)
        return training.out(entry, plant_now(session))

    @app.post("/api/v1/training/{run_id}/release-holds", tags=["training"])
    def training_release(run_id: int, session: Session = Depends(get_session),
                         user: str = Depends(need("quality"))) -> dict:
        """The holds of a graded scenario back to production (sorted): the live plant goes on."""
        return {"released": training.release_holds(session, run_id, user, plant_now(session))}

    @app.get("/api/v1/erp", tags=["erp"])
    def erp_summary(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """Demand of the customers, production orders, the stock in the ERP's books, the interface."""
        return erp_views.summary(session)

    @app.get("/api/v1/erp/messages", tags=["erp"])
    def erp_messages(status: str | None = None, kind: str | None = None, limit: int = 100,
                     session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        return erp_views.messages(session, status, kind, min(limit, 500))

    @app.post("/api/v1/erp/messages/{message_id}/{action}", tags=["erp"])
    def erp_message_action(message_id: str, action: str, session: Session = Depends(get_session),
                           user: str = Depends(need("interface"))) -> dict:
        """The error queue: process a message again (`reprocess`) or give it up (`discard`)."""
        msg = session.get(db.IntegrationMessage, message_id)
        if msg is None:
            raise HTTPException(404, "unknown message")
        at = plant_now(session)
        try:
            if action == "reprocess":
                bus.reprocess(session, msg, at, user)
            elif action == "discard":
                bus.discard(session, msg, at, user)
            else:
                raise HTTPException(404, f"unknown action {action}")
        except ValueError as e:
            raise HTTPException(409, str(e))
        session.commit()
        log.info("interface: %s %s %s by %s", action, msg.kind, msg.ref, user)
        return erp_views.message(msg)

    # ------------------------------------------------------------------ labels
    def label_of(session: Session, serial: str) -> db.Label:
        label = session.scalar(select(db.Label).where(db.Label.serial == serial))
        if label is None:
            raise HTTPException(404, f"no label printed for {serial}")
        return label

    @app.get("/api/v1/labels/{serial}", tags=["labels"])
    def label_info(serial: str, session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """The label printed for a box or pallet: the values, the template, every print of it."""
        return labels.out(label_of(session, serial))

    @app.get("/api/v1/labels/{serial}/svg", tags=["labels"])
    def label_svg(serial: str, session: Session = Depends(get_session), user: str = Depends(viewer)) -> Response:
        """The label as it was printed (SVG, millimetres)."""
        return Response(labels.svg(label_of(session, serial)), media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/labels/{serial}/reprint", tags=["labels"])
    def label_reprint(serial: str, body: ReprintRequest, session: Session = Depends(get_session),
                      user: str = Depends(need("plant"))) -> dict:
        """Print the same label again (damaged, unreadable); who and why go into its history."""
        label = label_of(session, serial)
        labels.reprint(session, label, plant_now(session), user, body.reason.strip())
        session.commit()
        log.info("label of %s printed again by %s: %s", serial, user, body.reason)
        return labels.out(label)

    @app.get("/api/v1/settings/labels/{code}/preview", tags=["settings"])
    def label_preview(code: str, material: str | None = None, state: str = "draft",
                      session: Session = Depends(get_session), user: str = Depends(viewer)) -> Response:
        """A template with made-up values: as in force, or with the draft (state=draft)."""
        overrides = store.proposed(session) if state == "draft" else store.in_force(session)
        shown = settings_mod.build(overrides, base=store.base)
        if code not in shown.labels.templates:
            raise HTTPException(404, f"unknown label template {code}")
        fg = [m.code for m in shown.finished_goods()]
        mat = material if material in fg else fg[0]
        svg = Labels(shown).preview(code, mat, plant_now(session))
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

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
                     user: str = Depends(need("quality"))) -> dict:
        return trace.preview_hold(session, body, plant_now(session))

    @app.post("/api/v1/holds", tags=["quality"])
    def hold_create(body: HoldCreate, session: Session = Depends(get_session),
                    user: str = Depends(need("quality"))) -> dict:
        hold = trace.create_hold(session, body, user, plant_now(session))
        return {"id": hold.id, "affected": hold.affected}

    @app.post("/api/v1/holds/{hold_id}/release", tags=["quality"])
    def hold_release(hold_id: int, body: HoldRelease, session: Session = Depends(get_session),
                     user: str = Depends(need("quality"))) -> dict:
        return trace.release_hold(session, hold_id, body.disposition, user, plant_now(session))

    @app.post("/api/v1/quarantine/{serial}/disposition", tags=["quality"])
    def disposition(serial: str, body: Disposition, session: Session = Depends(get_session),
                    user: str = Depends(need("quality"))) -> dict:
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
    def crew_call(body: CrewCallRequest, user: str = Depends(need("plant"))) -> dict:
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
                  user: str = Depends(need("plant"))) -> dict:
        run = control.current_run(session)
        if run is None:
            raise HTTPException(409, "no simulation run yet")
        run.desired_speed = body.speed
        session.commit()
        log.info("plant control: speed %sx by %s", body.speed, user)
        return {"desired_speed": run.desired_speed}

    @app.post("/api/v1/sim/pause", tags=["simulation"])
    def pause(body: PauseRequest, user: str = Depends(need("plant"))) -> dict:
        """Stop the plant's clock (machines, people, robots stand where they are) or let it run again."""
        sim_control.pause(body.paused, user)
        return sim_control.state()

    @app.post("/api/v1/sim/goto", tags=["simulation"])
    def goto(body: GotoRequest, session: Session = Depends(get_session), user: str = Depends(need("plant"))) -> dict:
        """Run the plant as fast as it goes until a later plant time (it cannot go back)."""
        now = plant_now(session)
        try:
            sim_control.jump(body.until, body.then, now, user)
        except ValueError as exc:
            log.info("plant control: the jump to %s refused for %s (plant time %s): %s",
                     body.until.isoformat(), user, now.isoformat(), exc)
            raise HTTPException(409, str(exc)) from None
        return sim_control.state()

    @app.delete("/api/v1/sim/goto", tags=["simulation"])
    def goto_cancel(user: str = Depends(need("plant"))) -> dict:
        sim_control.cancel(user)
        return sim_control.state()

    # ------------------------------------------------------------------ settings (master data)
    def setting_error(exc: settings_mod.SettingError) -> HTTPException:
        return HTTPException(422, {"en": exc.en, "sk": exc.sk})

    @app.get("/api/v1/settings", tags=["settings"])
    def settings_page_data(session: Session = Depends(get_session), user: str = Depends(viewer)) -> dict:
        """The master data by tab: every value that may be changed, with the value in force, the value of
        the config files and the draft; the revision in force, the one waiting for its time, the draft."""
        return settings_views.page(session, plant_now(session), model)

    @app.post("/api/v1/settings/draft", tags=["settings"])
    def settings_change(body: SettingChangeRequest, session: Session = Depends(get_session),
                        user: str = Depends(need("settings"))) -> dict:
        """Put one change into the draft (value), or back to the config files (reset). The whole plant
        is checked with it at once: a change that does not fit is refused with the reason."""
        try:
            store.change(session, body.path, body.value, user, reset=body.reset)
        except settings_mod.SettingError as exc:
            raise setting_error(exc) from None
        return settings_views.page(session, plant_now(session), model)

    @app.delete("/api/v1/settings/draft/change", tags=["settings"])
    def settings_drop(path: str, session: Session = Depends(get_session), user: str = Depends(need("settings"))) -> dict:
        """Take one change out of the draft."""
        try:
            store.drop(session, path, user)
        except settings_mod.SettingError as exc:
            raise setting_error(exc) from None
        return settings_views.page(session, plant_now(session), model)

    @app.delete("/api/v1/settings/draft", tags=["settings"])
    def settings_discard(session: Session = Depends(get_session), user: str = Depends(need("settings"))) -> dict:
        store.discard(session, user)
        return settings_views.page(session, plant_now(session), model)

    @app.post("/api/v1/settings/release", tags=["settings"])
    def settings_release(body: SettingReleaseRequest, session: Session = Depends(get_session),
                         user: str = Depends(need("settings"))) -> dict:
        """Release the draft as a revision: a reason, a trial hour of the plant offline, then in force now or
        at the start of the next shift (plant time). Changes the machines need make the plant start a new
        run with them (the shop floor starts empty)."""
        try:
            rev = store.release(session, user, body.reason, body.when, plant_now(session), model)
        except settings_mod.SettingError as exc:
            raise setting_error(exc) from None
        return {"number": rev.number, "status": rev.status, **settings_views.page(session, plant_now(session), model)}

    @app.post("/api/v1/settings/scheduled", tags=["settings"])
    def settings_scheduled(body: SettingScheduledRequest, session: Session = Depends(get_session),
                           user: str = Depends(need("settings"))) -> dict:
        """The revision waiting for its time: into force now, or withdrawn (back to a draft)."""
        rev = store.scheduled(session)
        if rev is None:
            raise HTTPException(409, "no revision is waiting")
        if body.action == "now":
            store.now_in_force(session, rev, plant_now(session))
        else:
            store.withdraw(session, user)
        return settings_views.page(session, plant_now(session), model)

    @app.get("/api/v1/settings/log", tags=["settings"])
    def settings_log(q: str | None = None, limit: int = 50, session: Session = Depends(get_session),
                     user: str = Depends(viewer)) -> dict:
        """The change log: released revisions with every change (the value before, the new one, who, when,
        why), and what administrators changed about users."""
        return settings_views.log(session, min(max(limit, 1), 200), q)

    # ------------------------------------------------------------------ users
    @app.get("/api/v1/users", tags=["settings"])
    def users_list(session: Session = Depends(get_session), user: str = Depends(need("users"))) -> dict:
        return settings_views.users(session)

    @app.post("/api/v1/users", tags=["settings"])
    def users_create(body: UserCreate, session: Session = Depends(get_session), user: str = Depends(need("users"))) -> dict:
        """A new user with a role; the generated password is shown once."""
        name = body.username.strip().lower()
        if session.scalar(select(db.AppUser).where(db.AppUser.username == name)) is not None:
            raise HTTPException(409, {"en": f"{name} exists already", "sk": f"{name} už existuje"})
        password = auth.generate_password()
        auth.set_user(session, name, password, body.display_name.strip() or name, role=body.role)
        audit(session, user, name, "CREATE", {"role": body.role})
        session.commit()
        log.info("user %s (%s) created by %s", name, body.role, user)
        return {**settings_views.users(session), "password": password, "username": name}

    @app.post("/api/v1/users/{username}", tags=["settings"])
    def users_update(username: str, body: UserUpdate, session: Session = Depends(get_session),
                     user: str = Depends(need("users"))) -> dict:
        """Change the role, the name or whether the user may sign in; or a new password (shown once)."""
        target = session.scalar(select(db.AppUser).where(db.AppUser.username == username))
        if target is None:
            raise HTTPException(404, "unknown user")
        own = target.username == user
        if own and (body.role not in (None, target.role) and "users" not in auth.perms_of(body.role) or body.active is False):
            raise HTTPException(409, {"en": "You cannot take the administration away from yourself",
                                      "sk": "Sám sebe správu používateľov odobrať nemôžeš"})
        out: dict = {}
        if body.role is not None and body.role != target.role:
            audit(session, user, target.username, "ROLE", {"old": target.role, "new": body.role})
            target.role = body.role
        if body.active is not None and body.active != target.active:
            audit(session, user, target.username, "ACTIVE", {"old": target.active, "new": body.active})
            target.active = body.active
        if body.display_name is not None and body.display_name.strip() and body.display_name.strip() != target.display_name:
            audit(session, user, target.username, "NAME", {"old": target.display_name, "new": body.display_name.strip()})
            target.display_name = body.display_name.strip()
        if body.new_password:
            out["password"] = auth.generate_password()
            target.password_hash = auth.hash_password(out["password"])
            audit(session, user, target.username, "PASSWORD", None)
        session.commit()
        log.info("user %s changed by %s: %s", target.username, user, body.model_dump(exclude_none=True))
        return {**settings_views.users(session), **out}

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
        allowed = perms(request, session)
        return templates.TemplateResponse(request, template, {
            "nav": nav_lines(session), "user": user if policy != "off" else None,
            "can_act": bool(user), "perms": sorted(allowed), "can": lambda perm: perm in allowed,
            "year": datetime.now(UTC).year, **context})

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

    @app.get("/people", response_class=HTMLResponse, include_in_schema=False)
    def people_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "people.html", session, active="people", absence=model.crew.absence)

    @app.get("/material", response_class=HTMLResponse, include_in_schema=False)
    def material_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "material.html", session, active="material", rules=model.smt_material)

    @app.get("/spc", response_class=HTMLResponse, include_in_schema=False)
    def spc_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "spc.html", session, active="spc")

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

    @app.get("/connect", response_class=HTMLResponse, include_in_schema=False)
    def connect_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "connect.html", session, active="connect", opcua_url=opcua_url,
                    broker=uns.status()["broker"] if uns is not None else None)

    @app.get("/training", response_class=HTMLResponse, include_in_schema=False)
    def training_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "training.html", session, active="training")

    @app.get("/erp", response_class=HTMLResponse, include_in_schema=False)
    def erp_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "erp.html", session, active="erp", rules=model.erp)

    @app.get("/trace", response_class=HTMLResponse, include_in_schema=False)
    def trace_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "trace.html", session, active="trace")

    @app.get("/quality", response_class=HTMLResponse, include_in_schema=False)
    def quality_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "quality.html", session, active="quality")

    @app.get("/holds", response_class=HTMLResponse, include_in_schema=False)
    def holds_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "holds.html", session, active="holds")

    @app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
    def settings_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "settings.html", session, active="settings")

    @app.get("/guide", response_class=HTMLResponse, include_in_schema=False)
    def guide_page(request: Request, session: Session = Depends(get_session)):
        return page(request, "guide.html", session, active="guide", **guide)

    return app
