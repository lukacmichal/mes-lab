"""The plant the live simulation runs: as configured, or a scenario of the tuning game.

On the tuning page a signed-in user may put a scenario into the live plant ("Use it in the live plant"):
fewer SMT lines, more robots, other numbers of technicians... The MES keeps the wish here (a small JSON
file next to the database, so it survives a restart), with a version number that goes up with every
change. The plant learns the version with the answer to every report; when it differs from the one it
was built with, it ends its run and starts a new one with the new knobs (a new run starts with an empty
shop floor, like after a restart). The run tells the MES which lines it has, and the planner gives work
only to those.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from mes_lab import tuning

log = logging.getLogger("mes_lab.scenario")


class ScenarioStore:
    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self.knobs: dict | None = None          # None: the plant as configured
        self.version = 0
        self.by: str | None = None
        self.at: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.knobs = data.get("knobs")
            self.version = int(data.get("version") or 0)
            self.by, self.at = data.get("by"), data.get("at")
        except (OSError, ValueError) as exc:
            log.warning("cannot read %s: %s", self.path, exc)

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"knobs": self.knobs, "version": self.version, "by": self.by, "at": self.at}),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("cannot write %s: %s", self.path, exc)

    def set(self, knobs: dict | None, by: str) -> dict:
        """Ask for a plant: the knobs of a scenario (cleaned to their limits), or None for the plant as
        configured. Knobs equal to the configured plant are stored as None."""
        base = tuning.defaults()
        clean = tuning.clean(knobs, base) if knobs else None
        if clean == base:
            clean = None
        with self._lock:
            if clean != self.knobs:
                self.knobs = clean
                self.version += 1
                self.by = by
                self.at = datetime.now(UTC).isoformat(timespec="seconds")
                self._save()
                log.info("live plant scenario %s by %s: %s", self.version, by, clean or "as configured")
            return self.state()

    def state(self) -> dict:
        return {"knobs": self.knobs, "version": self.version, "by": self.by, "at": self.at}
