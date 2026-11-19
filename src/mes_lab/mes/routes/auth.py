"""Signing in and out."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.orm import Session

from .. import auth
from ..context import Mes
from ..schemas import LoginRequest

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.post("/api/v1/auth/login", tags=["auth"])
    def login(body: LoginRequest, request: Request, session: Session = Depends(m.get_session)) -> dict:
        client = request.client.host if request.client else "?"
        if m.throttle.blocked(client):
            raise HTTPException(429, "too many failed attempts, try again in 10 minutes")
        user = auth.authenticate(session, body.username.strip(), body.password)
        if user is None:
            m.throttle.failed(client)
            raise HTTPException(401, "wrong user name or password")
        m.throttle.succeeded(client)
        request.session["user"] = user.username
        return {"user": user.username}

    @app.post("/api/v1/auth/logout", tags=["auth"])
    def logout(request: Request) -> dict:
        request.session.clear()
        return {"ok": True}
