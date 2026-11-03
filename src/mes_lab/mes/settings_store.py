"""The settings page in the MES: one draft of changes, released revisions, the change log.

People change master data in a draft (DRAFT revision): a cycle time, the name of a code, the start of a
shift. Every change is checked at once against the whole plant (mes_lab.settings.check), so the draft is
always a plant that can be built. A release needs a reason and runs the plant with the draft offline for
an hour first (the trial run: a change that makes the plant fail never reaches it). It takes effect now or
at the start of the next shift (plant time); until then it is SCHEDULED, and no other revision may be
released. When it takes effect (ACTIVE), the MES builds its plant model anew and, if the machines need the
changes (a cycle time, packing, shifts), asks the simulation for a new run built with them.

Released revisions never change: the log shows every change with the value before, the new value, who and
when, the reason and the revision. `overrides` of a revision is all that is in force with it.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_lab import settings
from mes_lab.model.schema import PlantModel

from . import models as db

log = logging.getLogger("mes_lab.settings")

TRIAL_H = 1.0          # the trial run before a release, hours of plant time


class SettingsStore:
    def __init__(self, config_dir=None):
        self.config_dir = config_dir
        self.base = settings.base_raw(config_dir)          # the config files (the plant as built)
        self._lock = threading.Lock()
        self.pending: datetime | None = None                # plant time a scheduled revision waits for
        self.on_activate: Callable[[Session, db.SettingRevision, PlantModel], None] | None = None

    # ------------------------------------------------------------------ revisions
    def active(self, session: Session) -> db.SettingRevision | None:
        return session.scalar(select(db.SettingRevision).where(db.SettingRevision.status == "ACTIVE")
                              .order_by(db.SettingRevision.number.desc()).limit(1))

    def scheduled(self, session: Session) -> db.SettingRevision | None:
        return session.scalar(select(db.SettingRevision).where(db.SettingRevision.status == "SCHEDULED")
                              .order_by(db.SettingRevision.number).limit(1))

    def released(self, session: Session) -> db.SettingRevision | None:
        """The latest released revision (scheduled or active): a draft builds on it."""
        return self.scheduled(session) or self.active(session)

    def draft(self, session: Session) -> db.SettingRevision | None:
        return session.scalar(select(db.SettingRevision).where(db.SettingRevision.status == "DRAFT").limit(1))

    def in_force(self, session: Session) -> dict:
        rev = self.active(session)
        return dict(rev.overrides or {}) if rev is not None else {}

    def effective_raw_in_force(self, session: Session) -> dict:
        return settings.effective_raw(self.in_force(session), base=self.base)

    def model(self, session: Session) -> tuple[PlantModel, int | None]:
        """The plant the MES works with: the config files and the active revision. A revision that does
        not fit the config files any more (they changed with a new version) is left out with a warning."""
        rev = self.active(session)
        if rev is None:
            return settings.build(None, base=self.base), None
        try:
            return settings.check(rev.overrides or {}, base=self.base), rev.number
        except settings.SettingError as exc:
            log.error("the settings of revision %s do not fit the config files: %s; the plant runs as configured",
                      rev.number, exc.en)
            return settings.build(None, base=self.base), None

    @staticmethod
    def _draft_overrides(base_overrides: dict, draft: db.SettingRevision | None) -> dict:
        out = dict(base_overrides)
        for ch in (draft.changes if draft is not None else []):
            if ch.action == "RESET":
                out.pop(ch.path, None)
            else:
                out[ch.path] = ch.new
        return out

    def proposed(self, session: Session) -> dict:
        """Everything in force with the draft (the overrides a release would put in force)."""
        rel = self.released(session)
        return self._draft_overrides(dict(rel.overrides or {}) if rel is not None else {}, self.draft(session))

    # ------------------------------------------------------------------ the draft
    def change(self, session: Session, path: str, value: Any, user: str, reset: bool = False) -> db.SettingChange | None:
        """Put a change into the draft (a new value, or back to the config files). The same path changed
        again replaces its earlier change; back to the value in force takes it out of the draft."""
        with self._lock:
            rel = self.released(session)
            before = dict(rel.overrides or {}) if rel is not None else {}
            draft = self.draft(session)
            proposed = self._draft_overrides(before, draft)
            raw = settings.effective_raw(proposed, base=self.base)
            segs = path.split("/")
            entry = "/".join(segs[:2])
            if len(segs) == 3 and segs[0] in ("defect_codes", "downtime_reasons") and isinstance(proposed.get(entry), dict):
                if reset:                                   # a field of a code added on the web: the code is the change
                    raise settings.SettingError("A code added here has no value as built; remove the code instead",
                                                "Kód pridaný tu nemá hodnotu pri postavení; odstráň radšej celý kód")
                field_value = settings.normalize(path, value, raw)
                path, value = entry, {**proposed[entry], segs[2]: field_value}
                if value.get(segs[2]) is None:
                    value.pop(segs[2], None)
            if not reset:
                new = settings.normalize(path, value, raw)
                configured = settings.get(self.base, path)
                reset = new == configured and settings.field_for(path).kind != "entry"   # None: not there as built
            if reset:                                       # the value of the config files: no override needed
                if settings.field_for(path) is None:
                    raise settings.SettingError(f"{path} cannot be changed here", f"{path} sa tu meniť nedá")
                new, action = None, "RESET"
                proposed.pop(path, None)
            else:
                action = "SET"
                proposed[path] = new
            settings.check(proposed, base=self.base)          # SettingError: the draft stays as it was
            old_value = settings.get(settings.effective_raw(before, base=self.base), path)
            new_value = settings.get(settings.effective_raw(proposed, base=self.base), path)
            same = (path in before) == (path in proposed) and before.get(path) == proposed.get(path)
            if draft is None:
                if same:
                    return None
                draft = db.SettingRevision(status="DRAFT", created_at=datetime.now(UTC), created_by=user, plant=False)
                session.add(draft)
                session.flush()
            row = next((c for c in draft.changes if c.path == path), None)
            if same:
                if row is not None:
                    draft.changes.remove(row)
                    session.delete(row)
            else:
                if row is None:
                    row = db.SettingChange(revision=draft, path=path)
                    session.add(row)
                row.action, row.old, row.new = action, old_value, new if action == "SET" else new_value
                row.at, row.by = datetime.now(UTC), user
            draft.plant = settings.needs_plant(c.path for c in draft.changes)
            session.commit()
            log.info("settings draft: %s %s = %r by %s", action, path, new if action == "SET" else "(config)", user)
            return row

    def drop(self, session: Session, path: str, user: str) -> None:
        """Take one change out of the draft."""
        with self._lock:
            draft = self.draft(session)
            row = next((c for c in draft.changes if c.path == path), None) if draft is not None else None
            if row is None:
                return
            rel = self.released(session)
            before = dict(rel.overrides or {}) if rel is not None else {}
            proposed = self._draft_overrides(before, draft)
            if path in before:                              # back to the value in force
                proposed[path] = before[path]
            else:
                proposed.pop(path, None)
            settings.check(proposed, base=self.base)          # e.g. min lot back above the new max lot
            draft.changes.remove(row)
            session.delete(row)
            draft.plant = settings.needs_plant(c.path for c in draft.changes)
            session.commit()
            log.info("settings draft: %s taken out by %s", path, user)

    def discard(self, session: Session, user: str) -> None:
        with self._lock:
            draft = self.draft(session)
            if draft is not None:
                draft.status = "DISCARDED"
                draft.released_at, draft.released_by = datetime.now(UTC), user
                session.commit()
                log.info("settings draft discarded by %s", user)

    # ------------------------------------------------------------------ release and effect
    def next_shift_start(self, model: PlantModel, now: datetime) -> datetime:
        tz = ZoneInfo(model.plant.timezone)
        local = now.astimezone(tz)
        best = None
        for day in (0, 1):
            for s in model.shifts:
                at = datetime.combine(local.date() + timedelta(days=day), s.start, tzinfo=tz)
                if at > local and (best is None or at < best):
                    best = at
        return best.astimezone(UTC)

    def trial(self, model: PlantModel) -> dict:
        """An hour of the plant with the draft, offline: it must run without an error."""
        from mes_lab.sim.runner import run_offline

        t0 = datetime.now(UTC)
        plant = run_offline(model, TRIAL_H, seed=7)
        good = 0
        for line in plant.lines.values():
            if line.spec.area_kind.value == "FA":
                good += sum(plant.edge.total_count[s.code][1] for s in line.stations if s.type.code == line.spec.output)
        return {"hours": TRIAL_H, "fa_good": good, "wall_s": round((datetime.now(UTC) - t0).total_seconds(), 1)}

    def release(self, session: Session, user: str, reason: str, when: str, plant_now: datetime,
                model_now: PlantModel) -> db.SettingRevision:
        reason = " ".join((reason or "").split())
        if len(reason) < 5:
            raise settings.SettingError("Say why (at least a few words): the log keeps it",
                                        "Napíš prečo (aspoň pár slov): záznam zmien si to pamätá")
        with self._lock:
            draft = self.draft(session)
            if draft is None or not draft.changes:
                raise settings.SettingError("The draft has no changes", "Návrh nemá žiadne zmeny")
            waiting = self.scheduled(session)
            if waiting is not None:
                raise settings.SettingError(
                    f"Revision {waiting.number} waits for its time; put it into force or withdraw it first",
                    f"Revízia {waiting.number} čaká na svoj čas; najprv ju uveď do platnosti alebo stiahni")
            proposed = self.proposed(session)
            model = settings.check(proposed, base=self.base)
            try:
                result = self.trial(model)
            except Exception as exc:  # noqa: BLE001 - whatever breaks the plant keeps the change away from it
                log.exception("the trial run of the settings draft failed")
                raise settings.SettingError(f"The trial run of the plant failed: {type(exc).__name__}: {exc}",
                                            f"Skúšobný beh závodu zlyhal: {type(exc).__name__}: {exc}") from None
            last = session.scalar(select(db.SettingRevision.number).where(db.SettingRevision.number.is_not(None))
                                  .order_by(db.SettingRevision.number.desc()).limit(1))
            draft.number = (last or 0) + 1
            draft.status = "SCHEDULED"
            draft.reason = reason
            draft.released_at, draft.released_by = datetime.now(UTC), user
            draft.overrides = proposed
            draft.check = result
            draft.plant = settings.needs_plant(c.path for c in draft.changes)
            draft.effective_at = plant_now if when == "now" else self.next_shift_start(model_now, plant_now)
            session.commit()
            self.pending = draft.effective_at
            log.info("settings revision %s released by %s (%s change(s), %s): %s; effective %s",
                     draft.number, user, len(draft.changes), "the plant builds anew" if draft.plant else "MES only",
                     reason, draft.effective_at.isoformat())
        if when == "now":
            self.activate(session, draft, plant_now)
        return draft

    def withdraw(self, session: Session, user: str) -> None:
        """A scheduled revision back to a draft (if there is no other draft; else it is discarded)."""
        with self._lock:
            rev = self.scheduled(session)
            if rev is None:
                return
            if self.draft(session) is None:
                rev.status, rev.number, rev.effective_at, rev.overrides, rev.check = "DRAFT", None, None, None, None
                rev.released_at = rev.released_by = rev.reason = None
            else:
                rev.status = "DISCARDED"
            session.commit()
            self.pending = None
            log.info("settings revision withdrawn by %s", user)

    def activate(self, session: Session, rev: db.SettingRevision, plant_time: datetime) -> None:
        with self._lock:
            if rev.status != "SCHEDULED":
                return
            rev.status = "ACTIVE"
            rev.activated_at = plant_time
            session.commit()
            self.pending = None
        model, _ = self.model(session)
        log.info("settings revision %s in force from plant time %s", rev.number, plant_time.isoformat())
        if self.on_activate is not None:
            self.on_activate(session, rev, model)

    def now_in_force(self, session: Session, rev: db.SettingRevision, plant_now: datetime) -> None:
        """A scheduled revision into force now (it does not wait for its shift)."""
        if rev.status == "SCHEDULED":
            rev.effective_at = plant_now
            session.commit()
            self.activate(session, rev, plant_now)

    def load_pending(self, session: Session) -> None:
        rev = self.scheduled(session)
        self.pending = rev.effective_at if rev is not None else None

    def due(self, session: Session, plant_time: datetime) -> None:
        """Called with every report of the plant: a scheduled revision whose time has come takes effect."""
        if self.pending is None or plant_time < self.pending:
            return
        rev = self.scheduled(session)
        if rev is not None and rev.effective_at is not None and rev.effective_at <= plant_time:
            self.activate(session, rev, plant_time)
