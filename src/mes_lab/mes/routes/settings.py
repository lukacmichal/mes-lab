"""The settings page: drafts and revisions of the master data, the change log."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from mes_lab import settings as settings_mod

from ..context import Mes
from ..schemas import SettingChangeRequest, SettingReleaseRequest, SettingScheduledRequest

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/api/v1/settings", tags=["settings"])
    def settings_page_data(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """The master data by tab: every value that may be changed, with the value in force, the value of
        the config files and the draft; the revision in force, the one waiting for its time, the draft."""
        return m.settings_views.page(session, m.plant_now(session), m.model)

    @app.post("/api/v1/settings/draft", tags=["settings"])
    def settings_change(body: SettingChangeRequest, session: Session = Depends(m.get_session),
                        user: str = Depends(m.need("settings"))) -> dict:
        """Put one change into the draft (value), or back to the config files (reset). The whole plant
        is checked with it at once: a change that does not fit is refused with the reason."""
        try:
            m.store.change(session, body.path, body.value, user, reset=body.reset)
        except settings_mod.SettingError as exc:
            raise m.setting_error(exc) from None
        return m.settings_views.page(session, m.plant_now(session), m.model)

    @app.delete("/api/v1/settings/draft/change", tags=["settings"])
    def settings_drop(path: str, session: Session = Depends(m.get_session), user: str = Depends(m.need("settings"))) -> dict:
        """Take one change out of the draft."""
        try:
            m.store.drop(session, path, user)
        except settings_mod.SettingError as exc:
            raise m.setting_error(exc) from None
        return m.settings_views.page(session, m.plant_now(session), m.model)

    @app.delete("/api/v1/settings/draft", tags=["settings"])
    def settings_discard(session: Session = Depends(m.get_session), user: str = Depends(m.need("settings"))) -> dict:
        m.store.discard(session, user)
        return m.settings_views.page(session, m.plant_now(session), m.model)

    @app.post("/api/v1/settings/release", tags=["settings"])
    def settings_release(body: SettingReleaseRequest, session: Session = Depends(m.get_session),
                         user: str = Depends(m.need("settings"))) -> dict:
        """Release the draft as a revision: a reason, a trial hour of the plant offline, then in force now or
        at the start of the next shift (plant time). Changes the machines need make the plant start a new
        run with them (the shop floor starts empty)."""
        try:
            rev = m.store.release(session, user, body.reason, body.when, m.plant_now(session), m.model)
        except settings_mod.SettingError as exc:
            raise m.setting_error(exc) from None
        return {"number": rev.number, "status": rev.status, **m.settings_views.page(session, m.plant_now(session), m.model)}

    @app.post("/api/v1/settings/scheduled", tags=["settings"])
    def settings_scheduled(body: SettingScheduledRequest, session: Session = Depends(m.get_session),
                           user: str = Depends(m.need("settings"))) -> dict:
        """The revision waiting for its time: into force now, or withdrawn (back to a draft)."""
        rev = m.store.scheduled(session)
        if rev is None:
            raise HTTPException(409, "no revision is waiting")
        if body.action == "now":
            m.store.now_in_force(session, rev, m.plant_now(session))
        else:
            m.store.withdraw(session, user)
        return m.settings_views.page(session, m.plant_now(session), m.model)

    @app.get("/api/v1/settings/log", tags=["settings"])
    def settings_log(q: str | None = None, limit: int = 50, session: Session = Depends(m.get_session),
                     user: str = Depends(m.viewer)) -> dict:
        """The change log: released revisions with every change (the value before, the new one, who, when,
        why), and what administrators changed about users."""
        return m.settings_views.log(session, min(max(limit, 1), 200), q)
