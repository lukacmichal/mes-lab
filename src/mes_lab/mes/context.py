"""What the routes of the MES share: the database, the plant model and everything built from it, the live
picture of the floor, who is signed in and what they may do. One Mes per application (see app.create_app); the
routes (mes_lab.mes.routes) get it as `m` and read its parts at the time of the request, so a revision of the
settings that takes effect (build_parts) is seen by all of them at once."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_lab import __version__
from mes_lab import settings as settings_mod
from mes_lab.connect import uns as connect_uns
from mes_lab.erp.bus import Bus
from mes_lab.erp.system import Erp
from mes_lab.layout import build_layout
from mes_lab.model import AreaKind

from . import auth, control, glossary, health, retention
from . import floor as floor_mod
from . import models as db
from .db import make_engine, make_sessionmaker, migrate
from .erp_link import ErpLink
from .erp_views import ErpViews
from .floor import CallQueue, FloorStore, SimControl
from .ingest import StationDirectory
from .kpi_views import KpiViews
from .labels import Labels
from .material import Material
from .qualifications import Qualifications
from .scenario import ScenarioStore
from .seed import sync_master_data
from .settings_store import SettingsStore
from .settings_views import SettingsViews
from .spc import Spc
from .trace import Trace
from .trace_views import TraceViews
from .training import Training
from .tuning_views import Tuning
from .views import Clock, Views

log = logging.getLogger("mes_lab.mes")
WEB_DIR = Path(__file__).parent / "web"
LOGIN_MODES = ("off", "actions", "all")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


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


def asset_version(static: Path) -> str:
    """What the pages put behind their scripts and styles (?v=...): it changes when any of the files does,
    so the proxy may let browsers keep them for good (deploy/Caddyfile)."""
    digest = hashlib.sha256()
    for path in sorted(static.iterdir()):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


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


class Mes:
    def __init__(self, database_url: str | None = None, config_dir: str | Path | None = None,
                 edge_token: str | None = None, require_login: bool | None = None, login: str | None = None):
        self.app: FastAPI | None = None                     # create_app puts the application here
        self.config_dir = config_dir
        self.engine = make_engine(database_url)
        migrate(self.engine)
        self.sessions = make_sessionmaker(self.engine)
        self.store = SettingsStore(config_dir)              # the settings page: the config files + released changes
        with self.sessions() as session:
            self.model, settings_rev = self.store.model(session)
            settings_overrides = self.store.in_force(session)
            self.store.load_pending(session)
            sync_master_data(session, self.model)

        keep_days = int(os.environ.get("MES_RETENTION_DAYS") or 0)
        if keep_days > 0:
            retention.start(self.sessions, self.model, keep_days)

        # built from the plant model by build_parts
        self.planner = self.views = self.trace = self.trace_views = self.spc = self.material = self.layout = self.kpis = None
        self.bus = self.erp = self.erp_link = self.erp_views = self.labels = self.qualifications = self.training = None
        self.layouts: dict[str, dict] = {}
        self.guide: dict = {}

        self.stations = StationDirectory()
        self.floor = FloorStore()
        self.calls = CallQueue()
        self.sim_control = floor_mod.CONTROL = SimControl()
        var_dir = Path(os.environ.get("MES_VAR_DIR") or "var")
        self.tuner = Tuning(var_dir / "tuning.json")
        self.tuner.use_settings(settings_rev, settings_overrides)
        self.scenario = ScenarioStore(var_dir / "scenario.json")
        self.settings_views = SettingsViews(self.store)
        self.throttle = auth.LoginThrottle()
        self.token = edge_token if edge_token is not None else os.environ.get("MES_EDGE_TOKEN")
        self.policy = login_mode(require_login, login)
        self.templates = Jinja2Templates(directory=WEB_DIR / "templates")
        self.templates.env.globals.update(
            version=__version__, asset=asset_version(WEB_DIR / "static"), plant=self.model.plant,
            login_enabled=self.policy != "off", station_help=glossary.STATIONS, state_help=glossary.STATES,
            unit_help=glossary.UNIT_STATUS)
        mqtt_url = os.environ.get("MES_MQTT_URL") or None
        self.uns = connect_uns.UnsMirror(mqtt_url) if mqtt_url else None     # the UNS: publish and mirror the tree
        self.opcua_url = os.environ.get("MES_OPCUA_URL") or None
        self.erp_lock = threading.Lock()
        self.erp_clock: dict[str, datetime] = {}
        self.store.on_activate = self.settings_took_effect

    def build_parts(self, new_model) -> None:
        """Everything the MES builds from the plant model; built anew when a revision of the settings
        takes effect (the routes see the new parts at once)."""
        store = self.store
        self.model = model = new_model
        self.planner = control.Planner(model)
        self.bus = Bus(model.erp.interface)                  # the fake ERP and the interface to it
        self.erp = Erp(model, self.bus)
        self.erp_link = ErpLink(model, self.planner, self.bus)
        self.erp_views = ErpViews(model, self.erp, self.erp_link)
        self.views = Views(model)
        self.trace = Trace(model)
        self.trace_views = TraceViews(model, self.trace)
        self.spc = Spc(model, self.trace)
        self.material = Material(model, self.trace)
        self.trace.material = self.material
        self.trace.labels = self.labels = Labels(model, revision=lambda s: (lambda r: r.number if r else None)(store.active(s)))
        self.trace.qualifications = self.qualifications = Qualifications(model)
        self.training = Training(model, self.trace)
        self.layout = build_layout(model)
        self.kpis = KpiViews(model, len(self.layout.seats))
        self.layouts.clear()
        self.layouts["null"] = self.layout.data
        self.guide.clear()
        self.guide.update(guide_context(model))
        if self.app is not None:
            self.app.state.model = model
        self.templates.env.globals.update(plant=model.plant)

    def settings_took_effect(self, session: Session, rev, new_model) -> None:
        """A revision of the settings is in force: the master data in the database, the MES's own plant
        model, the tuning game; the machines get it with a new run of the plant if they need it."""
        sync_master_data(session, new_model)
        self.build_parts(new_model)
        self.tuner.use_settings(rev.number, rev.overrides or {})
        if rev.plant:
            self.scenario.bump(f"settings revision {rev.number}")

    def close(self) -> None:
        """The application stops: the worker of the tuning game, the connection to the UNS."""
        if self.tuner._pool is not None:
            self.tuner._pool.shutdown(wait=False, cancel_futures=True)
        if self.uns is not None:
            self.uns.close()

    # ------------------------------------------------------------------ who asks, what may they do
    def get_session(self) -> Iterator[Session]:
        with self.sessions() as session:
            yield session

    def shop_floor(self, authorization: str | None = Header(default=None)) -> None:
        """Shop-floor endpoints require MES_EDGE_TOKEN when it is configured."""
        if self.token and not (authorization and secrets.compare_digest(authorization, f"Bearer {self.token}")):
            raise HTTPException(401, "edge token required")

    def signed_in(self, request: Request) -> str | None:
        return "local" if self.policy == "off" else request.session.get("user")

    def viewer(self, request: Request) -> str:
        """Who is looking; with MES_LOGIN=all only signed-in users may."""
        user = self.signed_in(request)
        if self.policy == "all" and not user:
            raise HTTPException(401, "login required")
        return user or "guest"

    def actor(self, request: Request) -> str:
        """Who is changing something: always a signed-in user (unless login is off)."""
        user = self.signed_in(request)
        if not user:
            log.info("refused, not signed in: %s %s", request.method, request.url.path)
            raise HTTPException(401, "sign in to do this")
        return user

    def perms(self, request: Request, session: Session) -> set[str]:
        """What the person looking may do (signed-in users by their role; local development: everything)."""
        if self.policy == "off":
            return set(auth.PERMS)
        name = request.session.get("user")
        if not name:
            return set()
        user = session.scalar(select(db.AppUser).where(db.AppUser.username == name))
        if user is None or not user.active:
            return set()
        return auth.perms_of(user.role)

    def need(self, perm: str):
        """A dependency: a signed-in user whose role may do this (403 otherwise)."""
        def check(request: Request, session: Session = Depends(self.get_session)) -> str:
            user = self.actor(request)
            if perm not in self.perms(request, session):
                log.info("refused, %s may not %s: %s %s", user, perm, request.method, request.url.path)
                raise HTTPException(403, f"your role may not do this ({perm})")
            return user
        return check

    def plant_now(self, session: Session) -> datetime:
        return Clock(session, self.model).now

    # ------------------------------------------------------------------ what goes on with every report of the plant
    def uns_tick(self, session: Session, at: datetime, every_s: float = 30.0) -> None:
        """The MES's own data in the UNS: the order each job line works on and what waits for it."""
        last = self.erp_clock.get("uns")
        if self.uns is None or (last is not None and last <= at < last + timedelta(seconds=every_s)):
            return
        self.erp_clock["uns"] = at
        try:
            self.uns.publish(connect_uns.mes_topics(self.model, self.views.line_queues(session)), at.isoformat())
        except Exception:  # noqa: BLE001 - the UNS must not stop the shop floor
            log.exception("publishing to the UNS failed")

    def erp_tick(self, session: Session, at: datetime, every_s: float = 30.0) -> None:
        """The ERP and the interface live in plant time: the ERP plans, the MES reports, the messages go."""
        if not self.model.erp.enabled or not self.erp_lock.acquire(blocking=False):
            return
        try:
            last = self.erp_clock.get("at")
            if last is not None and last <= at < last + timedelta(seconds=every_s):
                return
            self.erp_clock["at"] = at
            run = control.current_run(session)
            self.erp.tick(session, at)
            self.erp_link.tick(session, at, run)
            self.bus.deliver(session, at)
            session.commit()
        except Exception:
            session.rollback()
            health.ERRORS.add("erp")
            log.exception("the ERP tick failed at %s", at)
        finally:
            self.erp_lock.release()

    # ------------------------------------------------------------------ shared by some routes
    def scenario_state(self, session: Session) -> dict:
        run = control.current_run(session)
        live = {"knobs": run.knobs if run else None, "lines": run.lines if run else None,
                "since": run.plant_start.isoformat() if run else None}
        wanted = self.scenario.state()
        return {"wanted": wanted, "live": live, "base": self.tuner.base,
                "pending": (wanted["knobs"] or None) != (live["knobs"] or None)}

    def label_of(self, session: Session, serial: str) -> db.Label:
        label = session.scalar(select(db.Label).where(db.Label.serial == serial))
        if label is None:
            raise HTTPException(404, f"no label printed for {serial}")
        return label

    def setting_error(self, exc: settings_mod.SettingError) -> HTTPException:
        return HTTPException(422, {"en": exc.en, "sk": exc.sk})

    def nav_lines(self, session: Session) -> list[dict]:
        rows = session.execute(select(db.Area.code, db.Area.name, db.Line.code, db.Line.name)
                               .join(db.Line, db.Line.area_id == db.Area.id)
                               .order_by(db.Area.sort, db.Line.sort)).all()
        areas: dict[str, dict] = {}
        for area_code, area_name, line_code, line_name in rows:
            areas.setdefault(area_code, {"name": area_name, "lines": []})["lines"].append(
                {"code": line_code, "name": line_name})
        return list(areas.values())

    def page(self, request: Request, template: str, session: Session, **context):
        user = self.signed_in(request)
        if self.policy == "all" and not user:
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
        allowed = self.perms(request, session)
        return self.templates.TemplateResponse(request, template, {
            "nav": self.nav_lines(session), "user": user if self.policy != "off" else None,
            "can_act": bool(user), "perms": sorted(allowed), "can": lambda perm: perm in allowed,
            "year": datetime.now(UTC).year, **context})
