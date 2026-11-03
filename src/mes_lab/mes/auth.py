"""Users and login. Passwords are stored as scrypt hashes; the session is a
signed cookie. Login is switched on with MES_REQUIRE_LOGIN=1 (the server);
local development runs without it."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as db

N, R, P = 2 ** 14, 8, 1

# What a signed-in user may do, by role. Anybody may look (MES_LOGIN=actions); every change needs one of these:
#   plant     the simulation: speed, pause, a jump in time, calling people, a tuning scenario in the live plant
#   quality   holds, the quarantine (release or scrap)
#   settings  the settings page: drafts and releases of master data
#   users     the users and their roles
#   interface the error queue of the ERP interface: process a message again or discard it
#   people    the qualifications of the operators: grant, renew a certificate, revoke
PERMS = ("plant", "quality", "settings", "users", "interface", "people")
ROLES = {
    "ADMIN": {"plant", "quality", "settings", "users", "interface", "people"},
    "ENGINEER": {"settings", "plant", "interface"},          # a process engineer: master data, and runs the plant to try it
    "QUALITY": {"quality", "people"},           # also takes a qualification away after an escape
    "SUPERVISOR": {"plant", "quality", "people"},   # a shift supervisor: the plant, a hold, who is trained for what
}
ROLE_NAMES = {
    "ADMIN": ("Administrator", "Administrátor"),
    "ENGINEER": ("Process engineer", "Technológ"),
    "QUALITY": ("Quality engineer", "Kvalita"),
    "SUPERVISOR": ("Shift supervisor", "Majster"),
}


def perms_of(role: str | None) -> set[str]:
    return set(ROLES.get(role or "", set()))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                                p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def generate_password() -> str:
    return secrets.token_urlsafe(12)


def set_user(session: Session, username: str, password: str, display_name: str | None = None,
             role: str = "ADMIN") -> db.AppUser:
    """Create the user, or set a new password if it exists."""
    user = session.scalar(select(db.AppUser).where(db.AppUser.username == username))
    if user is None:
        user = db.AppUser(username=username, display_name=display_name or username, role=role, active=True,
                          password_hash=hash_password(password), created_at=datetime.now(UTC))
        session.add(user)
    else:
        user.password_hash = hash_password(password)
        if display_name:
            user.display_name = display_name
    session.commit()
    return user


def authenticate(session: Session, username: str, password: str) -> db.AppUser | None:
    user = session.scalar(select(db.AppUser).where(db.AppUser.username == username))
    if user is None:
        verify_password(password, hash_password("x"))  # same work for unknown names
        return None
    if not verify_password(password, user.password_hash) or not user.active:
        return None
    user.last_login_at = datetime.now(UTC)
    session.commit()
    return user


class LoginThrottle:
    """At most `limit` failed logins per client address in `window` seconds."""

    def __init__(self, limit: int = 5, window: float = 600):
        self.limit = limit
        self.window = window
        self.failures: dict[str, deque] = defaultdict(deque)

    def blocked(self, client: str) -> bool:
        q = self.failures[client]
        while q and q[0] < time.monotonic() - self.window:
            q.popleft()
        return len(q) >= self.limit

    def failed(self, client: str) -> None:
        self.failures[client].append(time.monotonic())

    def succeeded(self, client: str) -> None:
        self.failures.pop(client, None)
