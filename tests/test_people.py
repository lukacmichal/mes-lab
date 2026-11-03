"""Qualifications decided on the web: grant, renew, take away; the MES checks them, the plant takes them over
and a new run of the plant does not undo them."""

import uuid
from datetime import datetime, timedelta

from mes_lab.mes import auth
from mes_lab.sim.mes_client import MesClient
from mes_lab.sim.runner import run_offline

T = datetime.fromisoformat("2026-01-05T08:00:00+01:00")


def _people(shift):
    return [
        {"id": 9001, "name": "Ján Malý", "role": "OPERATOR", "shift": shift, "skills": {"SMT": None}},
        {"id": 9002, "name": "Eva Malá", "role": "OPERATOR", "shift": shift, "sex": "w", "home": "FA-01:LOADER",
         "skills": {"FA": None, "INSPECTION": (T - timedelta(days=1)).isoformat()}},
        {"id": 9003, "name": "Peter Údržbár", "role": "MAINTENANCE", "shift": shift, "skills": {}},
    ]


def test_qualifications_are_decided_on_the_web_and_win(client):
    run_id = str(uuid.uuid4())
    mes = MesClient(client=client)
    mes.register_run(run_id, 5, None, {"PCBA-BCM-200": ["SB1", "SB2", "SB3"]}, plant_start=T.isoformat())
    job = mes.next_job(run_id, "FA-01", T.isoformat())
    shift = client.get("/api/v1/people").json()["clock"]["shift"]["code"]
    first = mes.register_people(run_id, _people(shift))
    assert first["skills"]["items"] == []

    def load(operator, at, board):
        return client.post("/api/v1/trace/check-in", json={
            "run_id": run_id, "station": "FA-01-LDR", "plant_time": at.isoformat(), "consume": board,
            "job_id": job["id"], "operator": operator}).json()

    def decide(person, skill, action, reason="trained on FA-01", until=None):
        return client.post(f"/api/v1/people/{person}/skills", json={"skill": skill, "action": action, "reason": reason,
                                                                     "until": until})

    assert load(9001, T, "SB1")["reason"] == "NOT_QUALIFIED"
    assert decide(9001, "FA", "GRANT").status_code == 200                # granted: valid at once
    assert load(9001, T, "SB1")["ok"]
    assert decide(9001, "FA", "GRANT").status_code == 409                # already
    assert decide(9003, "FA", "GRANT").status_code == 422                # not an operator
    assert decide(9001, "WELDING", "GRANT").status_code == 422
    assert decide(9001, "SMT", "REVOKE", reason="").status_code == 422   # say why

    # taken away: from the next shift on, the running shift finishes
    r = decide(9002, "FA", "REVOKE", reason="escape at the final inspection")
    assert r.status_code == 200
    effective = datetime.fromisoformat(r.json()["effective_at"])
    assert effective >= T + timedelta(minutes=30)
    assert load(9002, T, "SB2")["ok"]
    late = load(9002, effective + timedelta(minutes=1), "SB3")
    assert late["reason"] == "NOT_QUALIFIED" and "taken away" in late["message"]

    # a certificate renewed on the web; the plant's own renewal does not undo a revocation
    assert decide(9002, "INSPECTION", "RENEW", reason="eye test passed", until="2026-12-31").status_code == 200
    client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": T.isoformat(),
                                             "qualifications": [{"id": 9002, "skill": "FA", "until": None}]})
    board = client.get("/api/v1/people").json()
    eva = next(p for p in board["people"] if p["id"] == 9002)
    assert eva["skills"]["FA"]["state"] == "ending" and eva["skills"]["INSPECTION"]["state"] == "ok"
    assert [h["action"] for h in board["history"]] == ["RENEW", "REVOKE", "GRANT"]
    assert board["history"][1]["reason"] == "escape at the final inspection"

    # the plant gets the decisions with every answer, and a new run of the plant does not undo them
    answer = client.post("/api/v1/edge/ingest", json={"run_id": run_id, "plant_time": T.isoformat()}).json()
    items = {(i["id"], i["skill"]): i for i in answer["skills"]["items"]}
    assert items[(9002, "FA")]["revoked"] and not items[(9001, "FA")]["revoked"] and answer["skills"]["version"] == 3
    again = mes.register_people(run_id, _people(shift))                  # the generated skills come back...
    assert len(again["skills"]["items"]) == 3
    ján = next(p for p in client.get("/api/v1/people").json()["people"] if p["id"] == 9001)
    assert ján["skills"]["FA"]["state"] == "ok"                          # ...and the decisions of the web win
    assert client.get("/people").status_code == 200


def test_who_may_decide():
    assert {r for r, perms in auth.ROLES.items() if "people" in perms} == {"ADMIN", "QUALITY", "SUPERVISOR"}


def test_the_foreman_plans_with_the_decisions(model):
    plant = run_offline(model, 0.2, 3)
    crew = plant.crew
    person = next(p for p in crew.persons.values() if p.home_seat is not None and p.home_seat.skill == "FA")
    now = plant.env.now
    at = plant.clock.at(now + 3600)
    crew.apply_skills([{"id": person.id, "skill": "FA", "until": at.isoformat(), "revoked": True}])
    assert crew.qualified(person, "FA", now + 1800) and not crew.qualified(person, "FA", now + 7200)
    crew._renew(person.shift, now + 30 * 86400)                          # no course gives it back
    assert not crew.qualified(person, "FA", now + 7200)
    crew.apply_skills([{"id": person.id, "skill": "FA", "until": None, "revoked": False}])
    assert crew.qualified(person, "FA", now + 7200) and "FA" not in person.revoked


def test_an_operator_who_lost_the_skill_of_the_seat_goes_to_retraining(model):
    plant = run_offline(model, 0.5, 3)
    crew = plant.crew
    nxt = "B"                                                            # the plant starts in shift A
    key, person = next((k, p) for k, p in crew.roster[nxt]["primaries"].items() if crew.seats[k].fixed)
    skill = crew.seats[key].skill
    crew.apply_skills([{"id": person.id, "skill": skill, "until": plant.clock.at(plant.env.now).isoformat(), "revoked": True}])
    plant.env.run(until=9 * 3600)                                        # into shift B
    assert crew.current_shift == nxt and person.absent == "training" and not person.on_site
    assert crew.holder.get(key) is not person
    assert (nxt, person.id, "training") in crew.absent_log
