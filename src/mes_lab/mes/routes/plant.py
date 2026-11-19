"""Running the plant: the tuning game, a scenario in the live plant, speed, pause, a jump in time, calls."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from .. import control
from ..context import Mes
from ..schemas import CrewCallRequest, GotoRequest, PauseRequest, ScenarioRequest, SpeedRequest, TuningRunRequest

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/api/v1/tuning", tags=["tuning"])
    def tuning_board(hours: int = 12, user: str = Depends(m.viewer)) -> dict:
        """The knobs of the tuning game, the plant as it is and the best scenarios so far."""
        return m.tuner.board(hours)

    @app.post("/api/v1/tuning/runs", tags=["tuning"])
    def tuning_run(body: TuningRunRequest, user: str = Depends(m.viewer)) -> dict:
        """Run a scenario offline (queued; the same knobs give the stored result at once)."""
        try:
            return m.tuner.submit(body.knobs or {}, body.hours, user)
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from None

    @app.get("/api/v1/tuning/runs/{jid}", tags=["tuning"])
    def tuning_status(jid: int, user: str = Depends(m.viewer)) -> dict:
        return m.tuner.status(jid)

    @app.get("/api/v1/plant/scenario", tags=["tuning"])
    def plant_scenario(session: Session = Depends(m.get_session), user: str = Depends(m.viewer)) -> dict:
        """Which plant runs live: as configured or a scenario of the tuning game, and what is asked for."""
        return m.scenario_state(session)

    @app.post("/api/v1/plant/scenario", tags=["tuning"])
    def set_plant_scenario(body: ScenarioRequest, session: Session = Depends(m.get_session),
                           user: str = Depends(m.need("plant"))) -> dict:
        """Put a scenario into the live plant (knobs), or the plant as configured again (null). The plant
        starts a new run with it within a second or two: the shop floor starts empty."""
        m.scenario.set(body.knobs, user, m.tuner.base)
        return m.scenario_state(session)

    @app.post("/api/v1/crew/calls", tags=["simulation"])
    def crew_call(body: CrewCallRequest, user: str = Depends(m.need("plant"))) -> dict:
        """Call somebody of a department to a machine. The plant gets the call with the answer to its
        next report (within a second), puts it on the andon board and sends the nearest free person."""
        if body.station not in m.model.stations:
            raise HTTPException(404, f"unknown station {body.station}")
        call = m.calls.add(body.station, body.role, body.minutes, body.meter, user)
        return {"id": call["id"], "queued": True}

    @app.get("/api/v1/crew/calls", tags=["simulation"])
    def crew_calls(user: str = Depends(m.viewer)) -> dict:
        return {"pending": m.calls.pending, "sent": list(m.calls.sent)}

    @app.post("/api/v1/sim/speed", tags=["simulation"])
    def set_speed(body: SpeedRequest, session: Session = Depends(m.get_session),
                  user: str = Depends(m.need("plant"))) -> dict:
        run = control.current_run(session)
        if run is None:
            raise HTTPException(409, "no simulation run yet")
        run.desired_speed = body.speed
        session.commit()
        log.info("plant control: speed %sx by %s", body.speed, user)
        return {"desired_speed": run.desired_speed}

    @app.post("/api/v1/sim/pause", tags=["simulation"])
    def pause(body: PauseRequest, user: str = Depends(m.need("plant"))) -> dict:
        """Stop the plant's clock (machines, people, robots stand where they are) or let it run again."""
        m.sim_control.pause(body.paused, user)
        return m.sim_control.state()

    @app.post("/api/v1/sim/goto", tags=["simulation"])
    def goto(body: GotoRequest, session: Session = Depends(m.get_session), user: str = Depends(m.need("plant"))) -> dict:
        """Run the plant as fast as it goes until a later plant time (it cannot go back)."""
        now = m.plant_now(session)
        try:
            m.sim_control.jump(body.until, body.then, now, user)
        except ValueError as exc:
            log.info("plant control: the jump to %s refused for %s (plant time %s): %s",
                     body.until.isoformat(), user, now.isoformat(), exc)
            raise HTTPException(409, str(exc)) from None
        return m.sim_control.state()

    @app.delete("/api/v1/sim/goto", tags=["simulation"])
    def goto_cancel(user: str = Depends(m.need("plant"))) -> dict:
        m.sim_control.cancel(user)
        return m.sim_control.state()
