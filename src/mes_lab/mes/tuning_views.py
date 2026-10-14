"""The tuning game in the MES: one scenario at a time in a worker process, the results kept.

Anybody may play (it changes nothing in the plant), so the runs are queued (one runs, a few wait) and
counted (at most RUNS_PER_HOUR an hour), and the same knobs are never run twice: the plant is
deterministic (fixed seed), the result is taken from the store. The store is a JSON file next to the
database, so the best scenarios survive a restart; the board on the page lists them.
"""

from __future__ import annotations

import itertools
import json
import logging
import multiprocessing
import threading
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from mes_lab import __version__, tuning

log = logging.getLogger("mes_lab.tuning")

RUNS_PER_HOUR = 30
QUEUE_MAX = 4
KEEP = 200


def _key(knobs: dict, hours: int) -> str:
    # a new version of the plant may run differently: its results are its own
    return json.dumps({"k": {k: knobs[k] for k in sorted(knobs)}, "h": hours, "v": __version__}, sort_keys=True)


def _summary(result: dict) -> dict:
    """What the board and the page need of a result (the full one has every line)."""
    p, c = result["plant"], result.get("costs") or {}
    return {"knobs": result["knobs"], "hours": result["hours"],
            "margin_per_h": c.get("margin_per_h"), "margin": c.get("margin"), "cost_per_unit": c.get("cost_per_unit"),
            "conversion_per_unit": c.get("conversion_per_unit"), "good_units": p["good_units"],
            "oee": p["oee"], "oee_smt": p["oee_smt"], "rty": p["rty"], "automation": p["automation"],
            "robot_utilization": p["robot_utilization"], "units_per_person_h": p["units_per_person_h"],
            "mttr_min": p["mttr_min"], "material_wait_h": result["extra"]["material_wait_h"],
            "smt_work_lines": result["areas"]["SMT"]["work_lines"], "wall_s": result["extra"]["wall_s"],
            "costs": {k: c.get(k) for k in ("labor", "lines", "energy", "robots", "scrap", "revenue", "material")},
            "lines": [{"code": l["code"], "oee": l["oee"], "loading": l["loading"], "output": l["output"],
                       "main_loss": l["main_loss"]} for l in result["lines"]]}


class Tuning:
    def __init__(self, store: Path | None = None, workers: int = 1):
        self.base = tuning.defaults()
        self.store = store
        self.results: dict[str, dict] = {}
        self.jobs: dict[int, dict] = {}
        self.queue: deque[int] = deque()
        self.started: deque[float] = deque()
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._pool: ProcessPoolExecutor | None = None
        self._workers = workers
        self._running: int | None = None
        self._load()

    # ------------------------------------------------------------------ the store
    def _load(self) -> None:
        if self.store and self.store.exists():
            try:
                for row in json.loads(self.store.read_text(encoding="utf-8")):
                    if row.get("version") == __version__:
                        self.results[_key(row["knobs"], row["hours"])] = row
            except (OSError, ValueError, KeyError) as exc:
                log.warning("cannot read %s: %s", self.store, exc)

    def _save(self) -> None:
        if not self.store:
            return
        rows = sorted(self.results.values(), key=lambda r: -(r.get("margin_per_h") or -1e9))[:KEEP]
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows), encoding="utf-8")
            tmp.replace(self.store)
        except OSError as exc:
            log.warning("cannot write %s: %s", self.store, exc)

    # ------------------------------------------------------------------ asking for a run
    def submit(self, knobs: dict, hours: int, by: str) -> dict:
        knobs = tuning.clean(knobs, self.base)
        hours = hours if hours in tuning.HOURS else 12
        key = _key(knobs, hours)
        with self._lock:
            if key in self.results:
                return {"state": "done", "result": self.results[key]}
            for jid, job in self.jobs.items():
                if job["key"] == key and job["state"] in ("queued", "running"):
                    return self._status(jid)
            now = time.time()
            while self.started and now - self.started[0] > 3600:
                self.started.popleft()
            if len(self.started) >= RUNS_PER_HOUR:
                raise RuntimeError("the plant has been tuned a lot this hour; try again in a while")
            if len(self.queue) >= QUEUE_MAX:
                raise RuntimeError("a few scenarios are waiting already; try again in a minute")
            jid = next(self._ids)
            self.jobs[jid] = {"id": jid, "key": key, "knobs": knobs, "hours": hours, "state": "queued",
                              "by": by, "queued": now, "started": None}
            self.queue.append(jid)
            self.started.append(now)
            self._next()
            return self._status(jid)

    def _next(self) -> None:
        if self._running is not None or not self.queue:
            return
        jid = self.queue.popleft()
        job = self.jobs[jid]
        job["state"], job["started"] = "running", time.time()
        self._running = jid
        if self._pool is None:
            # a fresh interpreter, not a fork of the MES (which holds database connections)
            self._pool = ProcessPoolExecutor(max_workers=self._workers, mp_context=multiprocessing.get_context("spawn"))
        future = self._pool.submit(tuning.run, job["knobs"], job["hours"])
        future.add_done_callback(lambda f, jid=jid: self._done(jid, f))

    def _done(self, jid: int, future) -> None:
        with self._lock:
            job = self.jobs[jid]
            try:
                result = _summary(future.result())
                result["by"] = job["by"]
                result["version"] = __version__
                self.results[job["key"]] = result
                job["state"] = "done"
                self._save()
            except Exception as exc:  # noqa: BLE001 - a broken scenario must not stop the queue
                log.exception("scenario %s failed", job["knobs"])
                job["state"], job["error"] = "failed", f"{type(exc).__name__}: {exc}"
            self._running = None
            self._next()

    # ------------------------------------------------------------------ reading
    def _expected_s(self, hours: int) -> float:
        walls = [r["wall_s"] * 1.0 for r in self.results.values() if r.get("hours") == hours and r.get("wall_s")]
        return sum(walls) / len(walls) if walls else hours * 4.0

    def _status(self, jid: int) -> dict:
        job = self.jobs.get(jid)
        if job is None:
            return {"state": "unknown"}
        out = {"id": jid, "state": job["state"], "knobs": job["knobs"], "hours": job["hours"]}
        if job["state"] == "queued":
            out["ahead"] = list(self.queue).index(jid) + (1 if self._running is not None else 0)
        if job["state"] == "running":
            spent = time.time() - job["started"]
            out["progress"] = min(0.97, spent / max(1.0, self._expected_s(job["hours"])))
        if job["state"] == "done":
            out["result"] = self.results.get(job["key"])
        if job["state"] == "failed":
            out["error"] = job.get("error")
        return out

    def status(self, jid: int) -> dict:
        with self._lock:
            return self._status(jid)

    def board(self, hours: int | None = None) -> dict:
        with self._lock:
            rows = [r for r in self.results.values() if hours is None or r["hours"] == hours]
            rows.sort(key=lambda r: -(r.get("margin_per_h") or -1e9))
            base = self.results.get(_key(self.base, hours or 12))
            return {"knobs": {k: {"min": lo, "max": hi, "what": what} for k, (lo, hi, what) in tuning.KNOBS.items()},
                    "base": self.base, "hours": list(tuning.HOURS), "baseline": base, "best": rows[:12],
                    "runs": len(self.results), "busy": self._running is not None, "waiting": len(self.queue)}
