"""Statistical process control (SPC) of the measured characteristics.

Every check-out of a measuring station carries its process values (paste volume at SPI, peak temperature
in the oven, press-fit force, coating weight, screw torque, supply current at the end-of-line test). For
the characteristics marked `spc` in config/station_types.yaml the MES keeps an X-bar / R chart of every
station, the way a control plan asks for it:

* every SAMPLE_MIN minutes of plant time a subgroup of SUBGROUP consecutive pieces (the first ones after
  the sampling moment). The limits are the "standard given" ones of the capable process (the target
  mean and the standard deviation of the configuration): the centre line of X-bar is the mean, its
  control limits mean +- 3 sigma / sqrt(n); the R chart has d2 sigma in the middle and D2 sigma on top;
* the rules on X-bar: one point beyond 3 sigma (R1), two of three beyond 2 sigma on one side (R2), seven
  points in a row rising or falling (R5, a trend). Fewer rules than the full Western Electric set: every
  rule raises false alarms too, and every alarm stops a machine.

A rule that fires on the newest subgroup is an SPC alarm, with the reaction plan:

1. the station is adjusted: the MES sends ADJUST to the plant, the station stops (maintenance), a
   technician with the measuring instruments sets it right, the plant confirms;
2. containment only where a part may be bad: a point beyond 3 sigma, or a single value outside the
   specification, puts a hold on what the station made since the last good subgroup; the held units are
   stopped at their next check-in;
3. after the adjustment the held units are judged by their own measured values: all inside the
   specification, the hold is released and they go on; otherwise the hold waits for a person to decide.

The evaluation runs every EVALUATE_EVERY_S wall seconds with the reports of the plant.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_lab.model.schema import PlantModel

from . import models as db
from .schemas import HoldCreate

log = logging.getLogger("mes_lab.spc")

SUBGROUP = 5
SAMPLE_MIN = 60
D2, D2_UCL = 2.326, 4.918                # d2 and D2 of a range of five
POINTS = 25                              # subgroups on a chart
EVALUATE_EVERY_S = 20.0
RULES = {
    "R1": ("one point beyond 3 sigma", "jeden bod za 3 sigma"),
    "R2": ("two of three beyond 2 sigma on one side", "dva z troch za 2 sigma na jednej strane"),
    "R5": ("seven points in a row rising or falling (a trend)", "sedem bodov za sebou stúpa alebo klesá (trend)"),
}


def violations(xbar: list[float], mean: float, sd: float) -> list[tuple[str, int, int]]:
    """Rules that fire, as (rule, first subgroup, last subgroup) of the points that make them fire."""
    s = sd / math.sqrt(SUBGROUP)
    z = [(x - mean) / s for x in xbar]
    out: list[tuple[str, int, int]] = []
    for i in range(len(z)):
        if abs(z[i]) > 3:
            out.append(("R1", i, i))
        if i >= 2:
            w = z[i - 2:i + 1]
            for sign in (1, -1):
                if z[i] * sign > 2 and sum(1 for v in w if v * sign > 2) >= 2:
                    out.append(("R2", i - 2, i))
        if i >= 6:
            w = xbar[i - 6:i + 1]
            steps = list(zip(w, w[1:]))
            if all(b > a for a, b in steps) or all(b < a for a, b in steps):
                out.append(("R5", i - 6, i))
    return out


class Spc:
    def __init__(self, model: PlantModel, trace):
        self.model = model
        self.trace = trace
        self.chars: list[tuple[str, str, str]] = []          # (station, station type, characteristic)
        for st in sorted(model.stations.values(), key=lambda s: s.code):
            for name, spec in model.station_types[st.type].measurements.items():
                if spec.spc:
                    self.chars.append((st.code, st.type, name))
        self._last = 0.0

    def spec(self, stype: str, name: str):
        return self.model.station_types[stype].measurements[name]

    # ------------------------------------------------------------------ the subgroups
    def subgroups(self, session: Session, station_id: int, name: str, now: datetime,
                  points: int = POINTS, since: datetime | None = None) -> list[dict]:
        """The subgroups of the last `points` sampling moments: the first SUBGROUP passed pieces after
        each (oldest first). A sampling moment with fewer pieces (the line stood) gives no point."""
        first = now - timedelta(minutes=SAMPLE_MIN * points)
        if since is not None and since > first:
            first = since
        rows = session.execute(
            select(db.OperationRecord.ended_at, db.OperationRecord.measurements, db.OperationRecord.unit_id)
            .where(db.OperationRecord.station_id == station_id, db.OperationRecord.ended_at > first,
                   db.OperationRecord.result == "PASS")
            .order_by(db.OperationRecord.ended_at)).all()
        period_s = SAMPLE_MIN * 60
        groups: dict[int, list] = {}
        for t, m, unit in rows:
            v = (m or {}).get(name)
            if v is None:
                continue
            k = int(t.timestamp() // period_s)                       # the sampling moments are whole hours
            bucket = groups.setdefault(k, [])
            if len(bucket) < SUBGROUP:
                bucket.append((t, float(v), unit))
        out = []
        for k in sorted(groups):
            g = groups[k]
            if len(g) < SUBGROUP:
                continue
            values = [v for _, v, _ in g]
            out.append({"from": g[0][0], "t": g[-1][0], "values": values, "units": [u for _, _, u in g],
                        "x": sum(values) / SUBGROUP, "r": max(values) - min(values)})
        return out

    # ------------------------------------------------------------------ the page
    def overview(self, session: Session, clock) -> list[dict]:
        """Every characteristic with its last point and whether it is in control."""
        out = []
        alarms = {(a.station_code, a.characteristic): a for a in session.scalars(
            select(db.SpcAlarm).order_by(db.SpcAlarm.id))}
        for code, stype, name in self.chars:
            station_id, _ = self.trace.station(session, code)
            spec = self.spec(stype, name)
            groups = self.subgroups(session, station_id, name, clock.now, points=9)
            fired = violations([g["x"] for g in groups], spec.mean, spec.sd)
            a = alarms.get((code, name))
            out.append({"station": code, "type": stype, "name": name, "unit": spec.unit,
                        "last": round(groups[-1]["x"], 4) if groups else None, "points": len(groups),
                        "signal": bool(fired and fired[-1][2] == len(groups) - 1),
                        "alarm": self.alarm_out(a, clock) if a else None})
        return out

    def chart(self, session: Session, code: str, name: str, clock) -> dict:
        station_id, stype = self.trace.station(session, code)
        spec = self.spec(stype, name)
        groups = self.subgroups(session, station_id, name, clock.now)
        s = spec.sd / math.sqrt(SUBGROUP)
        fired = violations([g["x"] for g in groups], spec.mean, spec.sd)
        alarms = session.scalars(select(db.SpcAlarm).where(db.SpcAlarm.station_code == code,
                                                            db.SpcAlarm.characteristic == name)
                                 .order_by(db.SpcAlarm.id.desc()).limit(10)).all()
        return {
            "station": code, "type": stype, "name": name, "unit": spec.unit, "subgroup": SUBGROUP,
            "sample_min": SAMPLE_MIN, "mean": spec.mean, "sd": spec.sd, "lsl": spec.lsl, "usl": spec.usl,
            "x": {"cl": spec.mean, "ucl": spec.mean + 3 * s, "lcl": spec.mean - 3 * s, "s": s},
            "r": {"cl": D2 * spec.sd, "ucl": D2_UCL * spec.sd, "lcl": 0.0},
            "points": [{"t": clock.iso(g["t"]), "x": round(g["x"], 4), "r": round(g["r"], 4),
                        "values": [round(v, 4) for v in g["values"]]} for g in groups],
            "fired": [{"rule": r, "from": a, "to": b} for r, a, b in fired],
            "alarms": [self.alarm_out(a, clock) for a in alarms],
        }

    def alarm_out(self, a, clock) -> dict:
        return {"id": a.id, "station": a.station_code, "name": a.characteristic, "rule": a.rule,
                "text": RULES.get(a.rule, (a.rule, a.rule)), "at": clock.iso(a.raised_at), "status": a.status,
                "hold_id": a.hold_id, "closed_at": clock.iso(a.closed_at), "note": a.note}

    # ------------------------------------------------------------------ watching all of them
    def due(self) -> bool:
        return time.monotonic() - self._last >= EVALUATE_EVERY_S

    def evaluate(self, session: Session, now: datetime) -> list:
        """Look at the newest subgroups of every characteristic; raise an alarm where a rule fires on the
        newest subgroup that came after the last alarm of that characteristic (one open alarm per station)."""
        self._last = time.monotonic()
        raised = []
        busy = {a.station_id for a in session.scalars(select(db.SpcAlarm).where(db.SpcAlarm.status != "CLOSED"))}
        for code, stype, name in self.chars:
            station_id, _ = self.trace.station(session, code)
            if station_id in busy:
                continue
            last = session.scalar(select(db.SpcAlarm.closed_at).where(
                db.SpcAlarm.station_id == station_id).order_by(db.SpcAlarm.id.desc()).limit(1))
            groups = self.subgroups(session, station_id, name, now, points=9, since=last)
            if not groups:
                continue
            spec = self.spec(stype, name)
            fired = [f for f in violations([g["x"] for g in groups], spec.mean, spec.sd) if f[2] == len(groups) - 1]
            if not fired:
                continue
            rule, first, _ = min(fired, key=lambda f: (f[0] != "R1", f[1]))
            raised.append(self._raise(session, station_id, code, stype, name, rule, groups, first, now))
            busy.add(station_id)
        if raised:
            session.commit()
        return raised

    def _raise(self, session: Session, station_id: int, code: str, stype: str, name: str, rule: str,
               groups: list[dict], first: int, now: datetime):
        spec = self.spec(stype, name)
        out_of_spec = any(not (spec.lsl <= v <= spec.usl) for g in groups[first:] for v in g["values"])
        hold_id = None
        if rule == "R1" or out_of_spec:
            # what the station made since the last good subgroup
            since = groups[first - 1]["t"] if first > 0 else groups[first]["from"] - timedelta(minutes=SAMPLE_MIN)
            hold = self.trace.create_hold(session, HoldCreate(
                kind="STATION_WINDOW", target=code, window_from=since, window_to=now,
                reason=f"SPC alarm: {name} on {code}, {RULES[rule][0]}"), "SPC", now)
            hold_id = hold.id
        alarm = db.SpcAlarm(station_id=station_id, station_code=code, characteristic=name, rule=rule,
                            raised_at=now, status="ADJUSTING", hold_id=hold_id)
        session.add(alarm)
        session.add(db.SimCommand(serial=code, action="ADJUST", created_at=now))
        session.flush()
        log.info("SPC alarm on %s %s (%s)%s: adjusting", code, name, rule, f", hold #{hold_id}" if hold_id else "")
        return alarm

    def adjusted(self, session: Session, station_code: str, now: datetime) -> None:
        """The plant has adjusted the station: the alarm is closed, the held units are judged by data."""
        alarm = session.scalar(select(db.SpcAlarm).where(db.SpcAlarm.station_code == station_code,
                                                          db.SpcAlarm.status == "ADJUSTING").limit(1))
        if alarm is None:
            return
        alarm.status, alarm.closed_at = "CLOSED", now
        hold = session.get(db.Hold, alarm.hold_id) if alarm.hold_id else None
        if hold is None:
            alarm.note = "NO_HOLD"                                  # adjusted, nothing to contain
            return
        if hold.status != "ACTIVE":
            return
        spec = self.spec(self.model.stations[station_code].type, alarm.characteristic)
        # the units the station passed (a unit it rejected went to repair and is tested again anyway)
        rows = session.scalars(select(db.OperationRecord.measurements).where(
            db.OperationRecord.station_id == alarm.station_id, db.OperationRecord.ended_at >= hold.window_from,
            db.OperationRecord.ended_at <= hold.window_to, db.OperationRecord.result == "PASS")).all()
        values = [m.get(alarm.characteristic) for m in rows if m and m.get(alarm.characteristic) is not None]
        bad = [v for v in values if not (spec.lsl <= v <= spec.usl)]
        if bad:
            alarm.note = f"WAITS:{len(bad)}:{len(values)}"            # some outside the specification: a person decides
            return
        alarm.note = f"RELEASED:{len(values)}"                    # all inside the specification: released by data
        self.trace.release_hold(session, hold.id, "RETURN", "SPC", now)
