"""Training scenarios in the live plant."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from .. import control
from ..context import Mes
from ..schemas import TrainingFinish, TrainingStart
from ..views import Clock

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/api/v1/training", tags=["training"])
    def training_view(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """The scenarios, the one running (its brief, the holds made since it began), the graded ones."""
        out = m.training.view(session, m.plant_now(session))
        out["clock"] = Clock(session, m.model).as_dict()
        return out

    @app.post("/api/v1/training/start", tags=["training"])
    def training_start(body: TrainingStart, session: Session = Depends(m.get_session),
                       user: str = Depends(m.need("plant"))) -> dict:
        """Start a scenario: a real fault in the live plant (or a complaint about a shipped unit)."""
        entry = m.training.start(session, body.scenario, user, m.plant_now(session), control.current_run(session))
        return m.training.out(entry, m.plant_now(session))

    @app.post("/api/v1/training/finish", tags=["training"])
    def training_finish(body: TrainingFinish, session: Session = Depends(m.get_session),
                        user: str = Depends(m.need("plant"))) -> dict:
        """The trainee is done: the fault stops (if it still runs) and the containment is graded."""
        entry = m.training.finish(session, user, m.plant_now(session), body.asns, body.abort)
        return m.training.out(entry, m.plant_now(session))

    @app.post("/api/v1/training/{run_id}/release-holds", tags=["training"])
    def training_release(run_id: int, session: Session = Depends(m.get_session),
                         user: str = Depends(m.need("quality"))) -> dict:
        """The holds of a graded scenario back to production (sorted): the live plant goes on."""
        return {"released": m.training.release_holds(session, run_id, user, m.plant_now(session))}
