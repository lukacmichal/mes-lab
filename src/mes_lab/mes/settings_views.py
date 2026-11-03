"""What the settings page shows: the master data by tab, every editable value with the value in force, the
value of the config files and the draft, and the change log (who changed what from what to what, why)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_lab import settings
from mes_lab.labels import PLACEHOLDERS
from mes_lab.model.schema import AreaKind, LabelField

from . import auth, models as db
from .settings_store import SettingsStore

LABELS = (
    # pattern (as in settings.FIELDS), English, Slovak; {0}, {1}... are the codes in the path
    ("plant/plan_efficiency", "Plant · efficiency of the hour-by-hour plan", "Závod · účinnosť hodinového plánu"),
    ("shifts/*/name", "Shift {0} · name", "Zmena {0} · názov"),
    ("shifts/*/start", "Shift {0} · start", "Zmena {0} · začiatok"),
    ("crew/breaks/*/start", "Break {0} · start (min after the shift starts)", "Prestávka {0} · začiatok (min po začiatku zmeny)"),
    ("crew/breaks/*/length", "Break {0} · length (min)", "Prestávka {0} · dĺžka (min)"),
    ("customers/*/name", "Customer {0} · name", "Zákazník {0} · názov"),
    ("materials/*/name", "{0} · name", "{0} · názov"),
    ("materials/*/customer", "{0} · customer", "{0} · zákazník"),
    ("materials/*/packing/klt_qty", "{0} · pieces in a KLT box", "{0} · kusov v boxe KLT"),
    ("materials/*/packing/klt_per_pallet", "{0} · boxes on a pallet", "{0} · boxov na palete"),
    ("materials/*/kit/per_bin", "{0} · sets of parts in a bin", "{0} · sád dielov v prepravke"),
    ("materials/*/planning/safety_stock", "{0} · safety stock (boards)", "{0} · poistná zásoba (dosky)"),
    ("materials/*/planning/min_lot", "{0} · smallest SMT lot (boards)", "{0} · najmenšia dávka SMT (dosky)"),
    ("materials/*/planning/max_lot", "{0} · largest SMT lot (boards)", "{0} · najväčšia dávka SMT (dosky)"),
    ("materials/*/routing/*/name", "{0} · {1} · name", "{0} · {1} · názov"),
    ("materials/*/routing/*/steps/*", "{0} · {1} · cycle of {2} (s)", "{0} · {1} · takt {2} (s)"),
    ("materials/*/routing/*/components/*", "{0} · {1} · {2} per board", "{0} · {1} · {2} na dosku"),
    ("areas/*/lines/*/name", "Line {1} · name", "Linka {1} · názov"),
    ("areas/*/lines/*/station_names/*", "Station {2} · name", "Stanica {2} · názov"),
    ("station_types/*/name", "Machine type {0} · name", "Typ stroja {0} · názov"),
    ("station_types/*/changeover_min", "{0} · changeover (min)", "{0} · prestavba (min)"),
    ("station_types/*/pm/interval_h", "{0} · planned maintenance every (operating h)", "{0} · plánovaná údržba každých (prevádzkových h)"),
    ("station_types/*/pm/duration_min", "{0} · planned maintenance takes (min)", "{0} · plánovaná údržba trvá (min)"),
    ("station_types/*/measurements/*/lsl", "{0} · {1} · lower limit", "{0} · {1} · dolná hranica"),
    ("station_types/*/measurements/*/usl", "{0} · {1} · upper limit", "{0} · {1} · horná hranica"),
    ("station_types/*/measurements/*/spc", "{0} · {1} · control chart (SPC)", "{0} · {1} · regulačná karta (SPC)"),
    ("defect_codes/*", "New defect code {0}", "Nový kód chyby {0}"),
    ("defect_codes/*/name", "Defect {0} · name", "Chyba {0} · názov"),
    ("defect_codes/*/category", "Defect {0} · category", "Chyba {0} · kategória"),
    ("downtime_reasons/*", "New downtime reason {0}", "Nový dôvod prestoja {0}"),
    ("downtime_reasons/*/name", "Downtime {0} · name", "Prestoj {0} · názov"),
    ("downtime_reasons/*/category", "Downtime {0} · category", "Prestoj {0} · kategória"),
    ("downtime_reasons/*/crew", "Downtime {0} · who repairs it", "Prestoj {0} · kto opravuje"),
    ("downtime_reasons/*/needs", "Downtime {0} · the technician brings", "Prestoj {0} · technik prinesie"),
    ("customers/*/city", "Customer {0} · town, country", "Zákazník {0} · mesto, krajina"),
    ("customers/*/supplier_no", "Customer {0} · our supplier number", "Zákazník {0} · naše číslo dodávateľa"),
    ("customers/*/dock", "Customer {0} · unloading point", "Zákazník {0} · miesto vykládky"),
    ("materials/*/customer_part", "{0} · customer part number", "{0} · číslo dielu zákazníka"),
    ("materials/*/change_level", "{0} · change level", "{0} · index výkresu"),
    ("materials/*/packing/unit_g", "{0} · weight of a unit (g)", "{0} · hmotnosť kusa (g)"),
    ("materials/*/packing/klt_g", "{0} · empty KLT box (g)", "{0} · prázdny box KLT (g)"),
    ("labels/supplier/name", "Labels · our company", "Štítky · naša firma"),
    ("labels/supplier/address", "Labels · our address", "Štítky · naša adresa"),
    ("labels/templates/*/name", "Label {0} · name", "Štítok {0} · názov"),
    ("labels/templates/*/fields/*/title", "Label {0} · {1} · title", "Štítok {0} · {1} · nadpis"),
    ("labels/templates/*/fields/*/value", "Label {0} · {1} · content", "Štítok {0} · {1} · obsah"),
    ("labels/templates/*/fields/*/x", "Label {0} · {1} · x (mm)", "Štítok {0} · {1} · x (mm)"),
    ("labels/templates/*/fields/*/y", "Label {0} · {1} · y (mm)", "Štítok {0} · {1} · y (mm)"),
    ("labels/templates/*/fields/*/w", "Label {0} · {1} · width (mm)", "Štítok {0} · {1} · šírka (mm)"),
    ("labels/templates/*/fields/*/h", "Label {0} · {1} · height (mm)", "Štítok {0} · {1} · výška (mm)"),
    ("labels/templates/*/fields/*/size", "Label {0} · {1} · text height (mm)", "Štítok {0} · {1} · výška písma (mm)"),
    ("labels/templates/*/fields/*/bold", "Label {0} · {1} · bold", "Štítok {0} · {1} · tučne"),
    ("labels/templates/*/fields/*/show", "Label {0} · {1} · printed", "Štítok {0} · {1} · tlačí sa"),
)


def describe(path: str) -> dict:
    segs = path.split("/")
    for pattern, en, sk in LABELS:
        pat = pattern.split("/")
        if len(pat) == len(segs) and all(p in ("*", s) for p, s in zip(pat, segs)):
            codes = [s for p, s in zip(pat, segs) if p == "*"]
            return {"en": en.format(*codes), "sk": sk.format(*codes)}
    return {"en": path, "sk": path}


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def revision_out(rev: db.SettingRevision | None, with_changes: bool = True) -> dict | None:
    if rev is None:
        return None
    out = {"id": rev.id, "number": rev.number, "status": rev.status, "created_at": _iso(rev.created_at),
           "created_by": rev.created_by, "reason": rev.reason, "released_at": _iso(rev.released_at),
           "released_by": rev.released_by, "effective_at": _iso(rev.effective_at),
           "activated_at": _iso(rev.activated_at), "plant": rev.plant, "check": rev.check,
           "count": len(rev.changes)}
    if with_changes:
        out["changes"] = [{"path": c.path, "label": describe(c.path), "action": c.action, "old": c.old, "new": c.new,
                           "at": _iso(c.at), "by": c.by, "plant": settings.needs_plant([c.path])} for c in rev.changes]
    return out


class SettingsViews:
    def __init__(self, store: SettingsStore):
        self.store = store

    # ------------------------------------------------------------------ the page
    def page(self, session: Session, plant_now: datetime, model_now) -> dict:
        st = self.store
        active, scheduled, draft = st.active(session), st.scheduled(session), st.draft(session)
        in_force = st.effective_raw_in_force(session)
        proposed_ov = st.proposed(session)
        proposed = settings.effective_raw(proposed_ov, base=st.base)
        base = st.base
        draft_paths = {c.path for c in draft.changes} if draft is not None else set()
        history = self._history(session)
        model = settings.build(proposed_ov, base=base)

        def F(path: str, **extra) -> dict:
            field = settings.field_for(path)
            out = {"p": path, "v": settings.get(proposed, path), "f": settings.get(in_force, path),
                   "c": settings.get(base, path), "d": path in draft_paths, "k": field.kind,
                   "pl": field.plant, "o": path in proposed_ov}
            if field.lo is not None:
                out["lo"], out["hi"] = field.lo, field.hi
            if field.choices:
                out["ch"] = list(field.choices)
            if path in history:
                out["h"] = history[path]
            out.update(extra)
            return out

        customers = [c["code"] for c in proposed["customers"]]
        products, bom, routings = [], [], []
        for mat in model.materials.values():
            mp = f"materials/{mat.code}"
            row = {"code": mat.code, "kind": mat.kind, "name": F(f"{mp}/name")}
            if mat.kind == "FG":
                row.update(customer=F(f"{mp}/customer", ch=customers), pcba=mat.pcba,
                           klt_qty=F(f"{mp}/packing/klt_qty"), klt_per_pallet=F(f"{mp}/packing/klt_per_pallet"),
                           per_bin=F(f"{mp}/kit/per_bin") if mat.kit else None, kit=mat.kit.name if mat.kit else None,
                           lines=[l.code for l in model.lines.values() if mat.code in l.materials])
            else:
                row.update(boards_per_panel=mat.boards_per_panel, safety_stock=F(f"{mp}/planning/safety_stock"),
                           min_lot=F(f"{mp}/planning/min_lot"), max_lot=F(f"{mp}/planning/max_lot"),
                           used_in=[m.code for m in model.fg_using(mat.code)])
            products.append(row)
            ops = []
            for op in mat.routing:
                op_path = f"{mp}/routing/{op.op}"
                steps = [{"type": t, "type_name": model.station_types[t].name, "cycle": F(f"{op_path}/steps/{t}")}
                         for t in op.steps]
                ops.append({"op": op.op, "name": F(f"{op_path}/name"), "steps": steps})
            routings.append({"code": mat.code, "kind": mat.kind, "name": mat.name, "per": "panel" if mat.kind == "PCBA" else "unit",
                             "boards_per_panel": mat.boards_per_panel, "ops": ops})
            if mat.kind == "PCBA":
                passes = []
                configured = {o["op"]: o for o in next(m for m in base["materials"] if m["code"] == mat.code)["routing"]}
                in_force_mat = next(m for m in in_force["materials"] if m["code"] == mat.code)
                for op in mat.routing:
                    op_path = f"{mp}/routing/{op.op}"
                    shown = list(op.components)
                    gone = [p for p in list(configured.get(op.op, {}).get("components", {}))
                            + list(next(o for o in in_force_mat["routing"] if o["op"] == op.op).get("components", {}))
                            if p not in shown]
                    shown += [p for p in dict.fromkeys(gone)]         # taken out: still listed, with 0
                    parts = []
                    for code in shown:
                        part = model.smt_material.parts.get(code)
                        parts.append({"part": code, "name": part.name if part else code, "machine": part.machine if part else "",
                                      "msl": part.msl if part else None, "qty": F(f"{op_path}/components/{code}")})
                    addable = sorted(p.code for p in model.smt_material.parts.values()
                                     if p.machine in op.steps and p.code not in shown)
                    passes.append({"op": op.op, "name": op.name, "parts": parts, "addable": addable})
                bom.append({"code": mat.code, "name": mat.name, "used_in": [m.code for m in model.fg_using(mat.code)],
                            "passes": passes})
            else:
                bom.append({"code": mat.code, "name": mat.name, "pcba": mat.pcba,
                            "kit": {"name": mat.kit.name, "per_bin": F(f"{mp}/kit/per_bin")} if mat.kit else None})

        types = []
        used_in = {t: [l.code for l in model.lines.values() if t in l.cell_types()] for t in model.station_types}
        for t in model.station_types.values():
            tp = f"station_types/{t.code}"
            types.append({"code": t.code, "kind": t.kind.value, "tag": t.tag, "name": F(f"{tp}/name"),
                          "manual": t.manual, "interlock": t.interlock, "lines": used_in[t.code],
                          "changeover": F(f"{tp}/changeover_min"),
                          "pm": {"interval": F(f"{tp}/pm/interval_h"), "duration": F(f"{tp}/pm/duration_min")} if t.pm else None,
                          "measurements": [{"name": n, "unit": m.unit, "mean": m.mean, "sd": m.sd,
                                            "lsl": F(f"{tp}/measurements/{n}/lsl"), "usl": F(f"{tp}/measurements/{n}/usl"),
                                            "spc": F(f"{tp}/measurements/{n}/spc")} for n, m in t.measurements.items()]})
        lines = []
        for area in model.areas:
            for code in area.lines:
                line = model.lines[code]
                lp = f"areas/{area.code}/lines/{code}"
                lines.append({"code": code, "area": area.code, "area_name": area.name, "name": F(f"{lp}/name"),
                              "stations": [{"code": s.code, "type": s.type, "name": F(f"{lp}/station_names/{s.code}",
                                                                                      v=s.name, f=None, c=None)}
                                           for s in line.stations]})
        for row in lines:                      # a station name: the name it has; as built it is called after its type
            line = model.lines[row["code"]]
            built = {st.code: model.station_types[st.type].name + (f" #{st.slot}" if len(cell.stations) > 1 else "")
                     for cell in line.cells for st in cell.stations}
            for s in row["stations"]:
                path = s["name"]["p"]
                s["name"]["f"] = settings.get(in_force, path) or built[s["code"]]
                s["name"]["c"] = built[s["code"]]

        used_defects: dict[str, list[str]] = {}
        used_reasons: dict[str, list[str]] = {}
        for t in model.station_types.values():
            for c in set(t.defects) | set(t.detects):
                used_defects.setdefault(c, []).append(t.code)
            for c in t.failures:
                used_reasons.setdefault(c, []).append(t.code)
        defects = [{"code": c.code, "added": c.added, "name": F(f"defect_codes/{c.code}/name"),
                    "category": F(f"defect_codes/{c.code}/category"), "used": sorted(used_defects.get(c.code, []))}
                   for c in model.defect_codes.values()]
        reasons = [{"code": c.code, "added": c.added, "name": F(f"downtime_reasons/{c.code}/name"),
                    "category": F(f"downtime_reasons/{c.code}/category"),
                    "crew": F(f"downtime_reasons/{c.code}/crew"), "needs": F(f"downtime_reasons/{c.code}/needs"),
                    "used": sorted(used_reasons.get(c.code, []))}
                   for c in model.downtime_reasons.values()]
        shifts = [{"code": s.code, "name": F(f"shifts/{s.code}/name"), "start": F(f"shifts/{s.code}/start"),
                   "end": s.end.strftime("%H:%M")} for s in model.shifts]
        breaks = [{"name": b.name, "kind": b.kind, "start": F(f"crew/breaks/{b.name}/start"),
                   "length": F(f"crew/breaks/{b.name}/length")} for b in model.crew.breaks]

        label_fields = ("title", "value", "x", "y", "w", "h", "size", "bold", "show")

        def LF(code: str, fc: str, k: str) -> dict:
            """A box of a template: a value the YAML leaves out is the default of the template (printed, 3 mm)."""
            out = F(f"labels/templates/{code}/fields/{fc}/{k}")
            spec = LabelField.model_fields[k]
            default = None if spec.is_required() else spec.default
            for key in ("v", "f", "c"):
                if out[key] is None:
                    out[key] = default
            return out
        labels = {
            "supplier": {"name": F("labels/supplier/name"), "address": F("labels/supplier/address")},
            "customers": [{"code": c["code"], "name": c["name"], "city": F(f"customers/{c['code']}/city"),
                           "supplier_no": F(f"customers/{c['code']}/supplier_no"), "dock": F(f"customers/{c['code']}/dock")}
                          for c in proposed["customers"]],
            "products": [{"code": m.code, "name": m.name, "customer": m.customer,
                          "customer_part": F(f"materials/{m.code}/customer_part"),
                          "change_level": F(f"materials/{m.code}/change_level"),
                          "unit_g": F(f"materials/{m.code}/packing/unit_g"), "klt_g": F(f"materials/{m.code}/packing/klt_g")}
                         for m in model.finished_goods()],
            "templates": [{"code": code, "kind": t.kind, "size": list(t.size_mm), "name": F(f"labels/templates/{code}/name"),
                           "fields": [{"code": fc, "barcode": f.barcode, "di": f.di,
                                       **{k: LF(code, fc, k) for k in label_fields}}
                                      for fc, f in t.fields.items()]}
                          for code, t in model.labels.templates.items()],
            "placeholders": {k: list(v) for k, v in PLACEHOLDERS.items()},
        }

        return {
            "labels": labels,
            "clock": {"plant_now": plant_now.isoformat(), "next_shift": st.next_shift_start(model_now, plant_now).isoformat()},
            "active": revision_out(active, with_changes=False), "scheduled": revision_out(scheduled),
            "draft": revision_out(draft),
            "products": products, "customers": [{"code": c["code"], "country": c.get("country"),
                                                 "name": F(f"customers/{c['code']}/name")} for c in proposed["customers"]],
            "bom": bom, "routings": routings, "types": types, "lines": lines,
            "defects": defects, "reasons": reasons, "categories": {
                "defect": sorted({c.category for c in model.defect_codes.values()}),
                "downtime": list(settings.DOWNTIME_CATEGORIES)},
            "shifts": shifts, "breaks": breaks, "plant": {"plan_efficiency": F("plant/plan_efficiency")},
            "kinds": {k.value: k.value for k in AreaKind},
        }

    def _history(self, session: Session) -> dict[str, dict]:
        """The last released change of every path: revision, who, when."""
        rows = session.execute(select(db.SettingChange.path, db.SettingRevision.number, db.SettingRevision.released_by,
                                      db.SettingRevision.activated_at, db.SettingRevision.status)
                               .join(db.SettingRevision, db.SettingRevision.id == db.SettingChange.revision_id)
                               .where(db.SettingRevision.status.in_(("ACTIVE", "SCHEDULED")))
                               .order_by(db.SettingRevision.number)).all()
        return {p: {"rev": n, "by": by, "at": _iso(at), "status": status} for p, n, by, at, status in rows}

    # ------------------------------------------------------------------ the log
    def log(self, session: Session, limit: int = 50, q: str | None = None) -> dict:
        revs = session.scalars(select(db.SettingRevision).where(db.SettingRevision.status.in_(("ACTIVE", "SCHEDULED")))
                               .order_by(db.SettingRevision.number.desc()).limit(limit)).all()
        out = [revision_out(r) for r in revs]
        if q:
            ql = q.lower()
            for r in out:
                r["changes"] = [c for c in r["changes"] if ql in c["path"].lower() or ql in c["label"]["en"].lower()
                                or ql in c["label"]["sk"].lower()]
            out = [r for r in out if r["changes"] or ql in (r["reason"] or "").lower()]
        users = session.scalars(select(db.AuditEvent).order_by(db.AuditEvent.id.desc()).limit(100)).all()
        return {"revisions": out, "users": [{"at": _iso(e.at), "by": e.by, "subject": e.subject, "action": e.action,
                                             "detail": e.detail} for e in users]}

    # ------------------------------------------------------------------ users
    @staticmethod
    def users(session: Session) -> dict:
        rows = session.scalars(select(db.AppUser).order_by(db.AppUser.username)).all()
        return {"users": [{"username": u.username, "display_name": u.display_name, "role": u.role, "active": u.active,
                           "created_at": _iso(u.created_at), "last_login_at": _iso(u.last_login_at)} for u in rows],
                "roles": [{"code": r, "name": {"en": auth.ROLE_NAMES[r][0], "sk": auth.ROLE_NAMES[r][1]},
                           "perms": sorted(p)} for r, p in auth.ROLES.items()]}


def audit(session: Session, by: str, subject: str, action: str, detail: dict | None = None) -> None:
    session.add(db.AuditEvent(at=datetime.now(UTC), by=by, kind="USER", subject=subject, action=action, detail=detail))
