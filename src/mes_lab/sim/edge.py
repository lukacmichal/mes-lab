"""Edge gateway: sits between the machines and the MES.

Machines change state and finish cycles many times a minute. Like a real edge
device, the gateway condenses these signals before they leave the shop floor:

* time per station, hour and state (plus how often the state was entered),
* counts per station, hour and job (processed, good, bad, false calls),
* defects found per station, hour and defect code,
* individual events for breakdowns, changeovers and planned maintenance,
* job progress and a live snapshot of every station and store,
* traceability records not yet sent with a check-in (see trace.py).

Hourly values are sent as cumulative totals, so sending them again is harmless:
the MES simply overwrites them. Nothing is lost while the MES is unreachable,
the dirty values are sent with the next successful flush.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Protocol

from .core import EVENT_STATES, PlantClock, State

if TYPE_CHECKING:
    from .plant import PlantSim
    from .station import Station

log = logging.getLogger("mes_lab.edge")


class Sink(Protocol):
    def send(self, payload: dict) -> dict | None: ...


class NullSink:
    def send(self, payload: dict) -> dict | None:
        return None


class EdgeGateway:
    def __init__(self, plant: PlantSim, clock: PlantClock, sink: Sink, run_id: str):
        self.plant = plant
        self.env = plant.env
        self.clock = clock
        self.sink = sink
        self.run_id = run_id
        self.stations: dict[str, Station] = {}
        self.cursor: dict[str, float] = {}
        # hourly accumulators, keyed by epoch hour
        self.acc_state: dict[tuple[str, int, str], list[float]] = defaultdict(lambda: [0.0, 0])
        self.acc_count: dict[tuple[str, int, int], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        self.acc_defect: dict[tuple[str, int, str], int] = defaultdict(int)
        self.dirty_state: set = set()
        self.dirty_count: set = set()
        self.dirty_defect: set = set()
        self.events: dict[str, dict] = {}
        self.dirty_events: set[str] = set()
        self.open_event: dict[str, str] = {}
        self.dirty_jobs: set[int] = set()
        self.dirty_shipments: dict[str, dict] = {}
        # whole-run totals, used by the offline report and tests
        self.total_state: dict[tuple[str, str], float] = defaultdict(float)
        self.total_count: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        self.total_defect: dict[tuple[str, str], int] = defaultdict(int)
        # for the KPIs (see mes_lab.kpi): ideal seconds earned, counts per product, breakdowns
        self.total_earned: dict[str, float] = defaultdict(float)
        self.total_by_material: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        self.total_down: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
        self.down_since: dict[str, float] = {}
        self.event_log_limit = 0  # >0 keeps closed events in memory (tests)
        self.closed_events: list[dict] = []
        self.flushes = 0
        self.failures = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------ signals
    def register(self, station: Station) -> None:
        self.stations[station.code] = station
        self.cursor[station.code] = self.env.now
        key = (station.code, self.clock.hour_of(self.env.now), station.state.value)
        self.acc_state[key][1] += 1
        self.dirty_state.add(key)

    def on_state(self, station: Station, new_state: State, new_reason: str | None) -> None:
        """Called by the station just before it changes state."""
        now = self.env.now
        self._account(station, now)
        key = (station.code, self.clock.hour_of(now), new_state.value)
        self.acc_state[key][1] += 1
        self.dirty_state.add(key)

        old = station.state
        if old == State.DOWN and new_state != State.DOWN and station.code in self.down_since:
            down = self.total_down[station.code]
            down[0] += 1
            down[1] += now - self.down_since.pop(station.code)
        if new_state == State.DOWN and old != State.DOWN:
            self.down_since[station.code] = now
        if old in EVENT_STATES and station.code in self.open_event:
            event_id = self.open_event.pop(station.code)
            event = self.events[event_id]
            event["end"] = now
            self.dirty_events.add(event_id)
            if self.event_log_limit:
                self.closed_events.append(event)
                del self.closed_events[:-self.event_log_limit]
        if new_state in EVENT_STATES:
            # unique across runs: the MES upserts events by id, a repeated id would
            # overwrite an event of an earlier run with the same seed
            event_id = str(uuid.uuid4())
            job = station.current_job()
            self.events[event_id] = {
                "id": event_id, "station": station.code, "state": new_state.value,
                "reason": new_reason, "job_id": job.id if job else None,
                "start": now, "end": None,
            }
            self.open_event[station.code] = event_id
            self.dirty_events.add(event_id)

    def count(self, station: Station, job_id: int | None, processed: int, good: int,
              bad: int = 0, false_calls: int = 0) -> None:
        key = (station.code, self.clock.hour_of(self.env.now), job_id or 0)
        acc = self.acc_count[key]
        total = self.total_count[station.code]
        job = self.plant.jobs.get(job_id) if job_id else None
        by_mat = self.total_by_material[(station.code, job.material.code if job else "")]
        for i, value in enumerate((processed, good, bad, false_calls)):
            acc[i] += value
            total[i] += value
            by_mat[i] += value
        ideal = job.steps.get(station.type.code) if job else station.type.default_cycle_s
        self.total_earned[station.code] += processed * (ideal or 0.0)
        self.dirty_count.add(key)

    def defects_found(self, station: Station, codes: list[str]) -> None:
        hour = self.clock.hour_of(self.env.now)
        for code in codes:
            key = (station.code, hour, code)
            self.acc_defect[key] += 1
            self.total_defect[(station.code, code)] += 1
            self.dirty_defect.add(key)

    def job_changed(self, job) -> None:
        self.dirty_jobs.add(job.id)

    def add_shipment(self, shipment: dict) -> None:
        """A truck left; sent with every flush until the MES has it."""
        self.dirty_shipments[shipment["asn"]] = shipment

    # ------------------------------------------------------------------ accounting
    def _account(self, station: Station, now: float) -> None:
        t0 = self.cursor[station.code]
        if now <= t0:
            return
        state = station.state.value
        self.total_state[(station.code, state)] += now - t0
        while t0 < now:
            hour = self.clock.hour_of(t0)
            end = self.clock.hour_end(hour)
            seg_end = now if end <= t0 or end >= now else end
            key = (station.code, hour, state)
            self.acc_state[key][0] += seg_end - t0
            self.dirty_state.add(key)
            t0 = seg_end
        self.cursor[station.code] = now

    def checkpoint(self) -> None:
        now = self.env.now
        for station in self.stations.values():
            self._account(station, now)

    # ------------------------------------------------------------------ flushing
    def build_payload(self) -> dict:
        self.checkpoint()
        clock = self.clock
        state_rows = [
            {"station": s, "hour": clock.hour_iso(h), "state": st,
             "seconds": round(self.acc_state[(s, h, st)][0], 3),
             "occurrences": self.acc_state[(s, h, st)][1]}
            for (s, h, st) in self.dirty_state
        ]
        count_rows = [
            {"station": s, "hour": clock.hour_iso(h), "job_id": j,
             "processed": v[0], "good": v[1], "bad": v[2], "false_calls": v[3]}
            for (s, h, j) in self.dirty_count
            for v in [self.acc_count[(s, h, j)]]
        ]
        defect_rows = [
            {"station": s, "hour": clock.hour_iso(h), "code": c, "found": self.acc_defect[(s, h, c)]}
            for (s, h, c) in self.dirty_defect
        ]
        event_rows = [
            {**e, "start": clock.iso(e["start"]), "end": clock.iso(e["end"])}
            for e in (self.events[i] for i in self.dirty_events)
        ]
        jobs = [self.plant.job_progress(self.plant.jobs[j]) for j in self.dirty_jobs
                if j in self.plant.jobs]
        return {
            "run_id": self.run_id,
            "plant_time": clock.iso(self.env.now),
            "speed": self.plant.speed,
            "state_time": state_rows,
            "counts": count_rows,
            "defects": defect_rows,
            "events": event_rows,
            "jobs": jobs,
            "stations": [self.plant.station_snapshot(s) for s in self.stations.values()],
            "live": self.plant.live_snapshot(),
            "floor": self.plant.floor_snapshot(),
            "records": self.plant.trace.take_records(),
            "shipments": list(self.dirty_shipments.values()),
            "acks": list(self.plant.acks),
        }

    def flush(self) -> dict | None:
        payload = self.build_payload()
        sent_state = set(self.dirty_state)
        sent_count = set(self.dirty_count)
        sent_defect = set(self.dirty_defect)
        sent_events = set(self.dirty_events)
        sent_jobs = set(self.dirty_jobs)
        sent_shipments = {s["asn"] for s in payload["shipments"]}
        try:
            control = self.sink.send(payload)
        except Exception as exc:  # noqa: BLE001 - keep the plant running whatever happens
            self.plant.trace.restore_records(payload["records"])
            self.failures += 1
            message = f"{type(exc).__name__}: {exc}"
            if message != self.last_error or self.failures % 30 == 1:
                log.warning("edge flush failed (%s), data kept for the next flush", message)
            self.last_error = message
            return None
        if self.last_error:
            log.info("edge flush works again after %d failures", self.failures)
            self.last_error = None
        self.flushes += 1
        del self.plant.acks[:len(payload["acks"])]
        self.dirty_state -= sent_state
        self.dirty_count -= sent_count
        self.dirty_defect -= sent_defect
        self.dirty_events -= sent_events
        self.dirty_jobs -= sent_jobs
        for asn in sent_shipments:
            self.dirty_shipments.pop(asn, None)
        for event_id in sent_events:
            event = self.events.get(event_id)
            if event and event["end"] is not None and event_id not in self.dirty_events:
                del self.events[event_id]
        self.plant.forget_completed_jobs(sent_jobs)
        self._prune()
        return control

    def _prune(self) -> None:
        """Forget closed hours that were already delivered."""
        current = self.clock.hour_of(self.env.now)
        for acc, dirty in ((self.acc_state, self.dirty_state),
                           (self.acc_count, self.dirty_count),
                           (self.acc_defect, self.dirty_defect)):
            for key in [k for k in acc if k[1] < current and k not in dirty]:
                del acc[key]

    def flush_and_apply(self) -> None:
        control = self.flush()
        if control:
            try:
                self.plant.apply_control(control)
            except Exception:  # noqa: BLE001 - a bad control message must not stop the plant
                log.exception("cannot apply control %s", control)

    def run(self, interval):
        """SimPy process: flush every interval() simulation seconds."""
        while True:
            yield self.env.timeout(interval())
            self.flush_and_apply()
