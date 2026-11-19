"""The labels of boxes and pallets: what was printed, a reprint, the preview of a template."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Response
from sqlalchemy.orm import Session

from mes_lab import settings as settings_mod

from ..context import Mes
from ..labels import Labels
from ..schemas import ReprintRequest

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/api/v1/labels/{serial}", tags=["labels"])
    def label_info(serial: str, session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """The label printed for a box or pallet: the values, the template, every print of it."""
        return m.labels.out(m.label_of(session, serial))

    @app.get("/api/v1/labels/{serial}/svg", tags=["labels"])
    def label_svg(serial: str, session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> Response:
        """The label as it was printed (SVG, millimetres)."""
        return Response(m.labels.svg(m.label_of(session, serial)), media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/labels/{serial}/reprint", tags=["labels"])
    def label_reprint(serial: str, body: ReprintRequest, session: Session = Depends(m.get_session),
                      user: str = Depends(m.need("plant"))) -> dict:
        """Print the same label again (damaged, unreadable); who and why go into its history."""
        label = m.label_of(session, serial)
        m.labels.reprint(session, label, m.plant_now(session), user, body.reason.strip())
        session.commit()
        log.info("label of %s printed again by %s: %s", serial, user, body.reason)
        return m.labels.out(label)

    @app.get("/api/v1/settings/labels/{code}/preview", tags=["settings"])
    def label_preview(code: str, material: str | None = None, state: str = "draft",
                      session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> Response:
        """A template with made-up values: as in force, or with the draft (state=draft)."""
        overrides = m.store.proposed(session) if state == "draft" else m.store.in_force(session)
        shown = settings_mod.build(overrides, base=m.store.base)
        if code not in shown.labels.templates:
            raise HTTPException(404, f"unknown label template {code}")
        fg = [m.code for m in shown.finished_goods()]
        mat = material if material in fg else fg[0]
        svg = Labels(shown).preview(code, mat, m.plant_now(session))
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})
