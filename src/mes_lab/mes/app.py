"""MES web application: shop-floor API for the plant and web pages for people.

create_app puts it together: what the routes share (context.Mes) and the routes themselves, one module per
part of the MES (routes/)."""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from mes_lab import __version__

from . import health, routes
from .context import LOGIN_MODES, WEB_DIR, Mes, _flag, asset_version, guide_context, login_mode

__all__ = ["LOGIN_MODES", "asset_version", "create_app", "guide_context", "login_mode"]


def create_app(database_url: str | None = None, config_dir: str | Path | None = None,
               edge_token: str | None = None, require_login: bool | None = None,
               login: str | None = None) -> FastAPI:
    m = Mes(database_url, config_dir, edge_token, require_login, login)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        m.close()

    app = FastAPI(title="ACME Plant 1 - MES", version=__version__,
                  description="Manufacturing execution system of a fictional automotive "
                              "electronics plant. Shop-floor API and read models.",
                  docs_url="/docs" if m.policy == "off" else None, redoc_url=None,
                  openapi_url="/openapi.json" if m.policy == "off" else None, lifespan=lifespan)
    m.app = app
    app.state.engine = m.engine
    app.state.settings = m.store
    app.state.uns = m.uns
    m.build_parts(m.model)

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

    for part in routes.ALL:
        part.register(app, m)
    return app
