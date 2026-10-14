"""The live picture of the shop floor (see mes_lab.sim.floor), kept in memory.

It changes every second and nobody needs its history, so it is not written to the
database: the latest snapshot is held as ready-made JSON and handed to every page that
asks. After a restart of the MES the next flush of the plant fills it again.
"""

from __future__ import annotations

import itertools
import json
import time
from collections import deque
from datetime import UTC, datetime, timedelta


class FloorStore:
    def __init__(self) -> None:
        self.body: bytes | None = None
        self.run_id: str | None = None
        self.at = 0.0

    def put(self, floor: dict) -> None:
        if not floor:
            return
        self.body = json.dumps(floor, separators=(",", ":")).encode()
        self.run_id = floor.get("run")
        self.at = time.monotonic()

    def response(self) -> bytes:
        """{"age": seconds since the plant last reported (null: never), "floor": snapshot}"""
        if self.body is None:
            return b'{"age":null,"floor":null}'
        age = round(time.monotonic() - self.at, 1)
        return b'{"age":%s,"floor":%s}' % (str(age).encode(), self.body)


class CallQueue:
    """Calls for a person of a department (maintenance, IT, foreman, logistics) asked for on a page.
    They go to the plant with the answer to its next report, like the commands for the quarantine;
    the plant puts them on its andon board and sends the nearest free person. Kept in memory: a call
    lives a few minutes, and what happened to it is in the live picture of the plant."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.pending: list[dict] = []
        self.sent: deque[dict] = deque(maxlen=30)

    def add(self, station: str, role: str, minutes: float, meter: bool, by: str) -> dict:
        call = {"id": next(self._ids), "station": station, "role": role, "minutes": minutes, "meter": meter,
                "by": by, "at": datetime.now(UTC).isoformat(timespec="seconds")}
        self.pending.append(call)
        return call

    def take(self) -> list[dict]:
        """The calls for the plant's next report (each one once)."""
        out, self.pending = self.pending, []
        self.sent.extend(out)
        return out



class SimControl:
    """Pause and "go to a time" for the simulated plant, asked for on a page (signed in).

    The plant gets the state with the answer to every report: while paused it stops its clock (and
    still reports, so the pages know it is alive); with a target time it runs as fast as the MES keeps
    up until it gets there, then goes on at its speed or stops there. Kept in memory: after a restart
    of the MES the plant simply runs. A pause ends by itself after MAX_PAUSE_S, so the plant (and the
    server under it) never stands still for long by mistake."""

    MAX_PAUSE_S = 30 * 60.0
    MAX_JUMP = timedelta(days=7)

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self.paused = False
        self.paused_since = 0.0
        self.by: str | None = None
        self.goto: dict | None = None          # {"until": aware datetime, "then": "pause"|"run", "from": ..., "by": ...}

    def _expire(self) -> None:
        if self.paused and self._clock() - self.paused_since > self.MAX_PAUSE_S:
            self.paused = False

    def pause(self, paused: bool, by: str) -> None:
        self.paused = paused
        self.by = by
        if paused:
            self.paused_since = self._clock()
            self.goto = None                  # a pause stops a jump too

    def jump(self, until: datetime, then: str, now: datetime, by: str) -> dict:
        if until.tzinfo is None:
            raise ValueError("the target time needs its time zone")
        if until <= now + timedelta(seconds=30):
            raise ValueError("the plant cannot go back in time: pick a time later than the plant's clock")
        if until - now > self.MAX_JUMP:
            raise ValueError("at most seven days ahead")
        self.goto = {"until": until.astimezone(UTC), "then": then, "from": now.astimezone(UTC), "by": by}
        self.paused = False
        return self.goto

    def cancel(self) -> None:
        self.goto = None

    def reached(self, plant_time: datetime) -> None:
        """The plant reported this time: a jump that got there is done (and pauses when it was asked to)."""
        if self.goto and plant_time >= self.goto["until"] - timedelta(seconds=0.5):
            if self.goto["then"] == "pause":
                self.pause(True, self.goto["by"])
            self.goto = None

    def for_plant(self) -> dict:
        self._expire()
        return {"paused": self.paused,
                "goto": self.goto["until"].isoformat() if self.goto else None}

    def state(self) -> dict:
        self._expire()
        out: dict = {"paused": self.paused}
        if self.paused:
            out["pause_left_s"] = round(max(0.0, self.MAX_PAUSE_S - (self._clock() - self.paused_since)))
        if self.goto:
            out["goto"] = {"until": self.goto["until"].isoformat(), "from": self.goto["from"].isoformat(),
                           "then": self.goto["then"]}
        return out


CONTROL = SimControl()        # the one of the running MES (create_app puts a fresh one here)
