"""The users of the MES and their roles."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from .. import models as db
from ..context import Mes
from ..schemas import UserCreate, UserUpdate
from ..settings_views import audit

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/api/v1/users", tags=["settings"])
    def users_list(session: Session = Depends(m.get_session), user: str = Depends(m.need("users"))) -> dict:
        return m.settings_views.users(session)

    @app.post("/api/v1/users", tags=["settings"])
    def users_create(body: UserCreate, session: Session = Depends(m.get_session), user: str = Depends(m.need("users"))) -> dict:
        """A new user with a role; the generated password is shown once."""
        name = body.username.strip().lower()
        if session.scalar(select(db.AppUser).where(db.AppUser.username == name)) is not None:
            raise HTTPException(409, {"en": f"{name} exists already", "sk": f"{name} už existuje"})
        password = auth.generate_password()
        auth.set_user(session, name, password, body.display_name.strip() or name, role=body.role)
        audit(session, user, name, "CREATE", {"role": body.role})
        session.commit()
        log.info("user %s (%s) created by %s", name, body.role, user)
        return {**m.settings_views.users(session), "password": password, "username": name}

    @app.post("/api/v1/users/{username}", tags=["settings"])
    def users_update(username: str, body: UserUpdate, session: Session = Depends(m.get_session),
                     user: str = Depends(m.need("users"))) -> dict:
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
        return {**m.settings_views.users(session), **out}
