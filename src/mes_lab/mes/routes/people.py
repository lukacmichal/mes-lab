"""The people: the qualification matrix, attendance, and the qualifications decided on the web."""

from __future__ import annotations

import json
import logging

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from ..context import Mes
from ..qualifications import parse_day
from ..schemas import SkillDecision

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/api/v1/people", tags=["views"])
    def people(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """The qualification matrix, today's attendance and cover, the certificates that run out."""
        crew = {}
        if m.floor.body is not None:
            crew = (json.loads(m.floor.body).get("crew") or {})
        out = m.views.people(session, m.trace, crew)
        out["history"] = m.qualifications.history(session)
        out["rules"] = {"certified": sorted(m.qualifications.certified), "valid_days": m.model.crew.skills.valid_days,
                        "skills": m.qualifications.skills,
                        "revoke_from": m.qualifications.next_shift(m.plant_now(session)).isoformat()}
        return out

    @app.post("/api/v1/people/{employee_id}/skills", tags=["people"])
    def people_skill(employee_id: int, body: SkillDecision, session: Session = Depends(m.get_session),
                     user: str = Depends(m.need("people"))) -> dict:
        """Grant a qualification (after the training), renew a certificate, or take a qualification away
        (from the start of the next shift). The plant gets it with the answer to its next report."""
        now = m.plant_now(session)
        entry = m.qualifications.change(session, employee_id, body.skill, body.action, body.reason, user, now,
                                      until=parse_day(body.until, m.qualifications.tz))
        session.commit()
        log.info("qualification %s %s of %s by %s: %s", body.action, body.skill, employee_id, user, body.reason)
        return {"ok": True, "change": m.qualifications.history(session, 1)[0], "effective_at": entry.effective_at.isoformat()}
