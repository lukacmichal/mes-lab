"""The labels the MES prints: a KLT box label when a full box passes the label printer of its packing line
(the check-out of BOX_LABEL), a pallet label with the SSCC when the palletizer closes a full pallet.

The MES fills the template in force (config/labels.yaml with the released settings) from what it knows of the
box or pallet and keeps the values and the layout with the label: the trace page shows it as it was printed,
and a reprint (a damaged label) prints the same label again, with who and why in its history.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_lab.labels import render
from mes_lab.model.schema import Material, PlantModel

from . import models as db
from .trace import GS1_COMPANY_PREFIX, gs1_check_digit

log = logging.getLogger("mes_lab.labels")

BATCHES_SHOWN = 3


def sscc_text(sscc: str) -> str:
    """The SSCC for people: (00) extension · company prefix · serial reference · check digit."""
    if len(sscc) != 18 or not sscc.isdigit():
        return sscc
    k = len(GS1_COMPANY_PREFIX)
    return f"(00) {sscc[0]} {sscc[1:1 + k]} {sscc[1 + k:17]} {sscc[17]}"


def _kg(value: float) -> str:
    return f"{value:.1f}"


class Labels:
    def __init__(self, model: PlantModel, revision: Callable[[Session], int | None] = lambda s: None):
        self.model = model
        self.revision = revision
        self.tz = ZoneInfo(model.plant.timezone)

    # ------------------------------------------------------------------ what goes on a label
    def _common(self, mat: Material, when: datetime, line: str) -> dict:
        cust = self.model.customers.get(mat.customer or "")
        sup = self.model.labels.supplier
        return {
            "customer_name": cust.name if cust else "", "customer_city": cust.city if cust else "",
            "dock": cust.dock if cust else "", "supplier_no": cust.supplier_no if cust else "",
            "supplier_name": sup.name, "supplier_address": sup.address, "delivery_note": "",
            "customer_part": mat.customer_part, "supplier_part": mat.code, "description": mat.name,
            "change_level": mat.change_level, "date": f"{when.astimezone(self.tz):%Y-%m-%d}", "line": line,
        }

    @staticmethod
    def _batch(rows: list[tuple[str | None, int]]) -> tuple[str, str]:
        """(the main work order, the list for people)."""
        orders = [o for o, _ in rows if o]
        if not orders:
            return "", ""
        shown = ", ".join(orders[:BATCHES_SHOWN]) + (f" +{len(orders) - BATCHES_SHOWN}" if len(orders) > BATCHES_SHOWN else "")
        return orders[0], shown

    def klt_data(self, session: Session, box: db.Unit, when: datetime, line: str) -> dict:
        mat = self.model.materials[box.material.code]
        rows = session.execute(select(db.WorkOrder.order_no, func.count())
                               .select_from(db.UnitLink)
                               .join(db.Unit, db.Unit.id == db.UnitLink.source_id)
                               .outerjoin(db.WorkOrder, db.WorkOrder.id == db.Unit.work_order_id)
                               .where(db.UnitLink.target_id == box.id, db.UnitLink.kind == "PACK")
                               .group_by(db.WorkOrder.order_no).order_by(func.count().desc())).all()
        main, _ = self._batch(rows)
        net = box.qty * mat.packing.unit_g / 1000
        return {**self._common(mat, when, line), "qty": box.qty, "boxes": 1, "net_kg": _kg(net),
                "gross_kg": _kg(net + mat.packing.klt_g / 1000), "package_id": box.serial, "batch": main}

    def pallet_data(self, session: Session, pallet: db.Unit, when: datetime, line: str) -> dict:
        mat = self.model.materials[pallet.material.code]
        klt = db.Unit.__table__.alias("klt")
        units = session.scalar(select(func.coalesce(func.sum(klt.c.qty), 0)).select_from(db.UnitLink)
                               .join(klt, klt.c.id == db.UnitLink.source_id)
                               .where(db.UnitLink.target_id == pallet.id, db.UnitLink.kind == "PALLET"))
        inner = db.UnitLink.__table__.alias("inner")
        rows = session.execute(select(db.WorkOrder.order_no, func.count())
                               .select_from(db.UnitLink)
                               .join(inner, inner.c.target_id == db.UnitLink.source_id)
                               .join(db.Unit, db.Unit.id == inner.c.source_id)
                               .outerjoin(db.WorkOrder, db.WorkOrder.id == db.Unit.work_order_id)
                               .where(db.UnitLink.target_id == pallet.id, db.UnitLink.kind == "PALLET",
                                      inner.c.kind == "PACK")
                               .group_by(db.WorkOrder.order_no).order_by(func.count().desc())).all()
        _, shown = self._batch(rows)
        net = int(units) * mat.packing.unit_g / 1000
        gross = net + pallet.qty * mat.packing.klt_g / 1000 + mat.packing.pallet_kg
        return {**self._common(mat, when, line), "qty": int(units), "boxes": pallet.qty, "net_kg": _kg(net),
                "gross_kg": _kg(gross), "package_id": pallet.serial, "sscc": pallet.serial,
                "sscc_text": sscc_text(pallet.serial), "batch": shown}

    def sample(self, kind: str, material: str, when: datetime) -> dict:
        """Made-up values for the preview on the settings page."""
        mat = self.model.materials[material]
        body = f"3{GS1_COMPANY_PREFIX}{1234:09d}"
        sscc = body + str(gs1_check_digit(body))
        pack = mat.packing
        if kind == "KLT":
            net = pack.klt_qty * pack.unit_g / 1000
            return {**self._common(mat, when, "PACK-01"), "qty": pack.klt_qty, "boxes": 1, "net_kg": _kg(net),
                    "gross_kg": _kg(net + pack.klt_g / 1000), "package_id": "K000012345", "batch": "WO0001234"}
        units = pack.klt_qty * pack.klt_per_pallet
        net = units * pack.unit_g / 1000
        return {**self._common(mat, when, "PACK-01"), "qty": units, "boxes": pack.klt_per_pallet,
                "net_kg": _kg(net), "gross_kg": _kg(net + pack.klt_per_pallet * pack.klt_g / 1000 + pack.pallet_kg),
                "package_id": sscc, "sscc": sscc, "sscc_text": sscc_text(sscc), "batch": "WO0001234, WO0001240"}

    # ------------------------------------------------------------------ printing
    def template_for(self, kind: str):
        return next((t for t in self.model.labels.templates.values() if t.kind == kind), None)

    def print(self, session: Session, unit: db.Unit, when: datetime, station: str) -> db.Label | None:
        """Print the label of a full box or pallet (once: a check-out reported twice prints nothing)."""
        kind = unit.kind
        template = self.template_for(kind)
        if template is None or session.scalar(select(db.Label.id).where(db.Label.unit_id == unit.id)) is not None:
            return None
        line = station.rsplit("-", 1)[0]
        data = self.klt_data(session, unit, when, line) if kind == "KLT" else self.pallet_data(session, unit, when, line)
        code = next(c for c, t in self.model.labels.templates.items() if t is template)
        label = db.Label(unit_id=unit.id, serial=unit.serial, template=code, settings_rev=self.revision(session),
                         printed_at=when, station=station, data=data,
                         layout={"name": template.name, "size_mm": list(template.size_mm),
                                 "fields": {k: f.model_dump() for k, f in template.fields.items()}},
                         prints=[{"at": when.isoformat(), "by": station, "reason": None}])
        session.add(label)
        return label

    def reprint(self, session: Session, label: db.Label, at: datetime, who: str, reason: str) -> None:
        label.prints = [*label.prints, {"at": at.isoformat(), "by": who, "reason": reason}]

    # ------------------------------------------------------------------ showing
    @staticmethod
    def svg(label: db.Label) -> str:
        return render.svg(label.layout, label.data, title=f"{label.layout.get('name', label.template)} · {label.serial}")

    def preview(self, template_code: str, material: str, when: datetime) -> str:
        t = self.model.labels.templates[template_code]
        data = self.sample(t.kind, material, when)
        return render.svg({"size_mm": list(t.size_mm), "fields": {k: f.model_dump() for k, f in t.fields.items()}},
                          data, title=t.name)

    @staticmethod
    def out(label: db.Label) -> dict:
        return {"serial": label.serial, "template": label.template, "name": label.layout.get("name"),
                "settings_rev": label.settings_rev, "printed_at": label.printed_at.isoformat(),
                "station": label.station, "data": label.data, "prints": label.prints,
                "reprints": len(label.prints) - 1}
