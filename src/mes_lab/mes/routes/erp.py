"""The fake ERP and the interface to it."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from .. import models as db
from ..context import Mes

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/api/v1/erp", tags=["erp"])
    def erp_summary(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """Demand of the customers, production orders, the stock in the ERP's books, the interface."""
        return m.erp_views.summary(session)

    @app.get("/api/v1/erp/messages", tags=["erp"])
    def erp_messages(status: str | None = None, kind: str | None = None, limit: int = 100,
                     session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        return m.erp_views.messages(session, status, kind, max(1, min(limit, 500)))

    @app.post("/api/v1/erp/messages/{message_id}/{action}", tags=["erp"])
    def erp_message_action(message_id: str, action: str, session: Session = Depends(m.get_session),
                           user: str = Depends(m.need("interface"))) -> dict:
        """The error queue: process a message again (`reprocess`) or give it up (`discard`)."""
        msg = session.get(db.IntegrationMessage, message_id)
        if msg is None:
            raise HTTPException(404, "unknown message")
        at = m.plant_now(session)
        try:
            if action == "reprocess":
                m.bus.reprocess(session, msg, at, user)
            elif action == "discard":
                m.bus.discard(session, msg, at, user)
            else:
                raise HTTPException(404, f"unknown action {action}")
        except ValueError as e:
            raise HTTPException(409, str(e))
        session.commit()
        log.info("interface: %s %s %s by %s", action, msg.kind, msg.ref, user)
        return m.erp_views.message(msg)
