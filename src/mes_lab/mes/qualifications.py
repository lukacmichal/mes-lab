"""The qualifications of the people, kept by the MES: a person is trained and gets a skill (GRANT), a
certificate is renewed (RENEW), or a qualification is taken away (REVOKE), e.g. after a quality escape until
the person is trained again.

The plant generates its people and their skills at the start of a run (sim/crew.py) and renews the
certificates of its own seats; what people decide on the web is the record of the MES and wins: it goes
back into the qualification table after every new roster and to the plant with every answer to its report
(`skills`: the version and the decisions). A grant or a renewal is valid at once; a revocation from the start
of the next shift (at least half an hour ahead): the foreman plans the seats at the start of a shift, and the
person finishes the shift they are in instead of leaving a machine without anybody.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_lab.model.schema import PlantModel

from . import models as db

ACTIONS = ("GRANT", "RENEW", "REVOKE")
LEAD = timedelta(minutes=30)           # a revocation needs this much before the shift it starts with


class Qualifications:
    def __init__(self, model: PlantModel):
        self.model = model
        self.tz = ZoneInfo(model.plant.timezone)
        self.skills = sorted({rule.skill or area for area, rules in model.crew.seats.items() for rule in rules})
        self.certified = set(model.crew.skills.certified)
        self._cache: tuple[int, list[dict]] | None = None

    # ------------------------------------------------------------------ time
    def next_shift(self, now: datetime) -> datetime:
        """The start of the first shift at least LEAD after `now`."""
        local = (now + LEAD).astimezone(self.tz)
        best = None
        for day in (0, 1):
            for s in self.model.shifts:
                at = datetime.combine(local.date() + timedelta(days=day), s.start, tzinfo=self.tz)
                if at >= local and (best is None or at < best):
                    best = at
        return best.astimezone(UTC)

    def default_until(self, now: datetime) -> datetime:
        return now + timedelta(days=self.model.crew.skills.valid_days)

    # ------------------------------------------------------------------ a decision on the web
    def change(self, session: Session, employee_id: int, skill: str, action: str, reason: str, by: str,
               now: datetime, until: datetime | None = None) -> db.QualificationChange:
        person = session.get(db.Employee, employee_id)
        if person is None:
            raise HTTPException(404, f"no person {employee_id}")
        if person.role != "OPERATOR":
            raise HTTPException(422, "qualifications are for operators")
        if skill not in self.skills:
            raise HTTPException(422, f"unknown skill {skill} (known: {', '.join(self.skills)})")
        if action not in ACTIONS:
            raise HTTPException(422, f"action must be one of {ACTIONS}")
        reason = " ".join((reason or "").split())
        if len(reason) < 3:
            raise HTTPException(422, "say why (the training, the course, the reason of the revocation)")
        row = session.get(db.Qualification, (employee_id, skill))
        holds = row is not None and not row.revoked and (row.valid_until is None or row.valid_until > now)
        certified = skill in self.certified
        effective = now
        if action == "GRANT":
            if holds:
                raise HTTPException(409, f"{person.name} is qualified for {skill} already")
            until = (until or self.default_until(now)) if certified else None
        elif action == "RENEW":
            if not certified:
                raise HTTPException(422, f"{skill} has no certificate that runs out")
            if row is None or row.revoked:
                raise HTTPException(409, f"{person.name} has no {skill} qualification to renew: grant it")
            until = until or self.default_until(now)
        else:
            if row is None or row.revoked or not holds:
                raise HTTPException(409, f"{person.name} is not qualified for {skill}")
            effective = self.next_shift(now)
            until = effective
        if until is not None and until <= now and action != "REVOKE":
            raise HTTPException(422, "the certificate must be valid after now")
        before = row.valid_until if row is not None else None
        if row is None:
            row = db.Qualification(employee_id=employee_id, skill=skill)
            session.add(row)
        row.valid_until = until
        row.revoked = action == "REVOKE"
        row.note = reason[:200] if action == "REVOKE" else None
        entry = db.QualificationChange(employee_id=employee_id, skill=skill, action=action, valid_until=until,
                                       before=before, effective_at=effective, reason=reason[:300], by=by, at=now)
        session.add(entry)
        session.flush()
        self._cache = None
        return entry

    # ------------------------------------------------------------------ for the plant
    def decisions(self, session: Session) -> dict:
        """{"version": n, "items": [{id, skill, until, revoked}]}: the last decision of every person and skill."""
        version = session.scalar(select(func.coalesce(func.max(db.QualificationChange.id), 0)))
        if self._cache is None or self._cache[0] != version:
            last = (select(db.QualificationChange.employee_id, db.QualificationChange.skill,
                           func.max(db.QualificationChange.id).label("id"))
                    .group_by(db.QualificationChange.employee_id, db.QualificationChange.skill).subquery())
            rows = session.scalars(select(db.QualificationChange)
                                   .join(last, db.QualificationChange.id == last.c.id)).all()
            items = [{"id": r.employee_id, "skill": r.skill, "revoked": r.action == "REVOKE",
                      "until": r.valid_until.isoformat() if r.valid_until else None} for r in rows]
            self._cache = (version, items)
        return {"version": self._cache[0], "items": self._cache[1]}

    def reapply(self, session: Session) -> None:
        """After a new roster of the plant: the decisions of the MES go back into the qualification table."""
        for item in self.decisions(session)["items"]:
            if session.get(db.Employee, item["id"]) is None:
                continue
            row = session.get(db.Qualification, (item["id"], item["skill"]))
            if row is None:
                row = db.Qualification(employee_id=item["id"], skill=item["skill"])
                session.add(row)
            row.valid_until = datetime.fromisoformat(item["until"]) if item["until"] else None
            row.revoked = item["revoked"]
            if item["revoked"]:
                last = session.scalar(select(db.QualificationChange.reason).where(
                    db.QualificationChange.employee_id == item["id"], db.QualificationChange.skill == item["skill"])
                    .order_by(db.QualificationChange.id.desc()).limit(1))
                row.note = (last or "")[:200]

    # ------------------------------------------------------------------ for the page
    def history(self, session: Session, limit: int = 60) -> list[dict]:
        rows = session.execute(select(db.QualificationChange, db.Employee.name, db.Employee.shift)
                               .join(db.Employee, db.Employee.id == db.QualificationChange.employee_id)
                               .order_by(db.QualificationChange.id.desc()).limit(limit)).all()
        return [{"id": c.id, "person": c.employee_id, "name": name, "shift": shift, "skill": c.skill, "action": c.action,
                 "until": c.valid_until.isoformat() if c.valid_until else None,
                 "before": c.before.isoformat() if c.before else None,
                 "effective_at": c.effective_at.isoformat(), "reason": c.reason, "by": c.by, "at": c.at.isoformat()}
                for c, name, shift in rows]


def parse_day(text: str | None, tz: ZoneInfo) -> datetime | None:
    """A date from the page (YYYY-MM-DD): valid to the end of that day, plant time."""
    if not text:
        return None
    try:
        day = datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, "the date must be YYYY-MM-DD") from None
    return datetime.combine(day, time(23, 59), tzinfo=tz).astimezone(UTC)
