"""The skills of the people (qualifications, certificates), absences and who covers which seat.

A part of the Crew (crew.py), which holds the state these methods work on."""

from __future__ import annotations

from datetime import datetime

from .crew_base import (
    ARRIVE_LEAD_S,
    HANDOVER_MAX_S,
    LOGISTICS,
    MATERIAL,
    OPERATOR,
    Person,
)


class SkillsMixin:
    # ------------------------------------------------------------------ skills
    def _give_skills(self) -> None:
        """Everybody is qualified for the seat that is theirs; many for one or two more; relief people for
        the seats they cover and more. The visual inspection and the repairs need a certificate that runs
        out (an eye test once a year, IPC-7711/7721 every two years): a few run out within days."""
        rng = self.skill_rng
        cfg = self.config.skills
        skills = sorted({seat.skill for seat in self.seats.values()})
        expiring = set(cfg.certified)

        def valid(skill: str) -> float | None:
            if skill not in expiring:
                return None
            if rng.random() < cfg.expiring_share:
                return rng.uniform(0.5, 6) * 86400             # runs out within days
            return rng.uniform(30, cfg.valid_days) * 86400

        for shift, ros in self.roster.items():
            for key, person in ros["primaries"].items():
                own = self.seats[key].skill
                person.skills = {own: valid(own)}
                for extra in rng.sample([k for k in skills if k != own and k != "REPAIR"], k=rng.choice((0, 0, 1, 1, 2))):
                    person.skills[extra] = valid(extra)
            for relief in ros["reliefs"]:
                home = {self.seats[k].skill for k in self.relief_keys.get(relief.id, [])} or {"FA"}
                pick = home | set(rng.sample(skills, k=rng.choice((1, 2))))
                relief.skills = {k: valid(k) for k in sorted(pick)}

    def attendance_rows(self) -> list[dict]:
        """Who came to the shifts that started since the last report (for the MES)."""
        clock = self.plant.clock
        out = []
        for entry in self.attendance:
            for row in entry["rows"]:
                out.append({"shift_start": clock.iso(entry["start"]), "shift": entry["shift"], **row})
        return out

    def attendance_sent(self, rows: int) -> None:
        while rows and self.attendance:
            rows -= len(self.attendance[0]["rows"])
            self.attendance.pop(0)

    def renewals_out(self) -> list[dict]:
        clock = self.plant.clock
        return [{"id": pid, "skill": skill, "until": clock.iso(until)} for pid, skill, until in self.renewals]

    def renewals_sent(self, rows: int) -> None:
        del self.renewals[:rows]

    def roster_out(self) -> list[dict]:
        """The people of the plant and their qualifications, for the MES (at the start of a run)."""
        clock = self.plant.clock
        out = []
        for person in self.persons.values():
            home = person.home_seat.key if person.home_seat else (person.base if person.role != OPERATOR else None)
            out.append({"id": person.id, "name": person.name, "role": person.role, "shift": person.shift,
                        "sex": person.sex, "home": home,
                        "skills": {k: (clock.iso(v) if v is not None else None) for k, v in person.skills.items()}})
        return out

    def apply_skills(self, items: list[dict]) -> int:
        """The qualifications people decided in the MES (granted, renewed, taken away): they win over what
        the plant generated. The foreman plans with them from the next shift."""
        clock, n = self.plant.clock, 0
        for item in items:
            person = self.persons.get(item["id"])
            if person is None:
                continue
            until = (datetime.fromisoformat(item["until"]) - clock.start).total_seconds() if item.get("until") else None
            person.skills[item["skill"]] = until
            if item.get("revoked"):
                person.revoked.add(item["skill"])
            else:
                person.revoked.discard(item["skill"])
            n += 1
        return n

    def qualified(self, person: Person, skill: str, at: float) -> bool:
        if skill not in person.skills:
            return False
        until = person.skills[skill]
        return until is None or until > at

    # ------------------------------------------------------------------ absences and the cover of the seats
    def _plan_shift(self, shift: str) -> None:
        """Who is off this shift, who holds which seat. Seats of the absent are covered by qualified
        relief people, the machine seats first; then, for a machine seat still empty, a qualified desk
        operator moves over, of the same area first (their desk seat stays empty: it matters less)."""
        now = self.env.now
        rng = self.absence_rng
        ros = self.roster[shift]
        cfg = self.config.absence
        _, start, end = self.shift_window(now + ARRIVE_LEAD_S + 1)
        until = end + HANDOVER_MAX_S                     # the certificate must hold until the hand-over
        self._renew(shift, until)
        # the only material handler or logistics worker of a shift is never missing: without them the SMT lines run
        # out of reels and the trucks wait, so a colleague of another shift stands in (overtime)
        sole = {role for role in (MATERIAL, LOGISTICS)
                if sum(1 for p in self._all_in(shift) if p.role == role) <= 1}
        for person in self._all_in(shift):
            r = rng.random()
            person.absent = "sick" if r < cfg.sick else "holiday" if r < cfg.sick + cfg.holiday else None
            if person.role in sole:
                person.absent = None
            person.shift_seat = None
            if person.absent:
                self.absent_log.append((shift, person.id, person.absent))
        holder: dict[str, Person] = {}
        for key, person in ros["primaries"].items():
            if not person.absent and not self.qualified(person, self.seats[key].skill, until):
                person.absent = "training"            # the MES took the skill of their seat away: retraining
                self.absent_log.append((shift, person.id, person.absent))
            if not person.absent:
                holder[key] = person
        free = [r for r in ros["reliefs"] if not r.absent]
        empty = sorted((k for k in self.seat_order if k not in holder),
                       key=lambda k: (not self.seats[k].fixed, self.seat_order.index(k)))
        covers: dict[int, int] = {}
        for key in empty:
            seat = self.seats[key]
            able = [r for r in free if self.qualified(r, seat.skill, until)]
            if not able:
                continue
            near = [r for r in able if r.base.startswith(f"reserve:{seat.line}:")] or able
            relief = near[0]
            free.remove(relief)
            holder[key] = relief
            covers[relief.id] = ros["primaries"][key].id
        for key in [k for k in empty if k not in holder and self.seats[k].fixed]:
            seat = self.seats[key]
            movers = [(k, p) for k, p in holder.items() if not self.seats[k].fixed
                      and k not in empty and self.qualified(p, seat.skill, until)]
            movers.sort(key=lambda kp: self.seats[kp[0]].area != seat.area)      # the same area first
            if movers:
                k, person = movers[0]
                del holder[k]
                holder[key] = person
                covers[person.id] = ros["primaries"][key].id
        for key, person in holder.items():
            person.shift_seat = self.seats[key]
        self.holder = holder
        self.uncovered = [k for k in self.seat_order if k not in holder]
        self.break_reliefs = free
        self.plan_until = until
        rows = []
        for person in self._all_in(shift):
            seat = person.shift_seat.key if person.shift_seat else None
            rows.append({"id": person.id, "status": (person.absent or "present").upper(), "seat": seat,
                         "covers": covers.get(person.id)})
        self.attendance.append({"shift": shift, "start": start, "rows": rows, "uncovered": list(self.uncovered)})

    def _renew(self, shift: str, until: float) -> None:
        """Certificates that run out: the operator of a seat that needs one goes to the eye test or the
        course before it does; the certificate of another skill is renewed at the next course, some days
        after it ran out (until then that person may not cover such a seat)."""
        cfg = self.config.skills
        now = self.env.now
        for person in self._all_in(shift):
            own = person.home_seat.skill if person.home_seat else None
            for skill, valid in list(person.skills.items()):
                if valid is None or skill in person.revoked:
                    continue
                if (skill == own and valid < until) or valid < now - cfg.course_days * 86400:
                    person.skills[skill] = now + cfg.valid_days * 86400
                    self.renewals.append((person.id, skill, person.skills[skill]))

    def _reserve_for(self, keys: list[str], ordinal: int) -> str:
        """Where the relief person of these seats waits: at the team desk of the first line."""
        if not keys:
            return "door"
        seat = self.seats[keys[0]]
        reserve = f"reserve:{seat.line}:{ordinal}"
        return reserve if reserve in self.walkways.places else seat.home

    def _all_in(self, shift: str) -> list[Person]:
        ros = self.roster[shift]
        out = list(ros["primaries"].values()) + ros["reliefs"]
        for k in ("maintenance", "it", "foreman", "logistics", "manager", "material"):
            out += ros[k]
        return out

    def _start_shift_zero(self) -> None:
        """The plant starts in the middle of a shift: its people are already at work."""
        code, start, end = self.shift_window(0.0)
        self.current_shift, self.window = code, (start, end)
        self._arrive(code, instant=True)
        self.env.process(self._shift_manager())
