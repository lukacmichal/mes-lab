"""The settings page: master data changed in a draft, released as a revision with a reason, in force now or
at the next shift; the MES and the plant build the same plant with it; the log keeps every change; roles."""

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from mes_lab import settings
from mes_lab.mes import models as db
from mes_lab.mes.app import create_app
from mes_lab.mes.auth import set_user
from mes_lab.sim.mes_client import MesClient

CYCLE = "materials/PCBA-BCM-200/routing/SMT_BOT/steps/PNP_CHIP"
AOI_NAME = "areas/SMT/lines/SMT-01/station_names/SMT-01-AOI"


def _start(client):
    run_id = str(uuid.uuid4())
    mes = MesClient(client=client)
    reg = mes.register_run(run_id, 5, None, {})
    return mes, run_id, datetime.fromisoformat(reg["plant_start"])


def test_overrides_go_over_the_config_files_by_code():
    model = settings.check({CYCLE: 30.0, AOI_NAME: "AOI Koh Young", "shifts/B/start": "13:30",
                            "materials/PCBA-BCM-200/routing/SMT_BOT/components/RES-0402-100R": None,
                            "downtime_reasons/BARCODE_FAULT": {"name": "Barcode reader fault", "category": "IT",
                                                               "crew": "IT", "added": True}})
    assert model.materials["PCBA-BCM-200"].operation("SMT_BOT").steps["PNP_CHIP"] == 30.0
    assert "RES-0402-100R" not in model.materials["PCBA-BCM-200"].operation("SMT_BOT").components
    assert model.stations["SMT-01-AOI"].name == "AOI Koh Young" and model.stations["SMT-02-AOI"].name != "AOI Koh Young"
    shifts = {s.code: (s.start.strftime("%H:%M"), s.end.strftime("%H:%M")) for s in model.shifts}
    assert shifts == {"A": ("06:00", "13:30"), "B": ("13:30", "22:00"), "C": ("22:00", "06:00")}   # A ends where B starts
    assert model.downtime_reasons["BARCODE_FAULT"].added
    raw = settings.base_raw()
    stale = settings.apply(raw, {"materials/NO-SUCH/name": "x"})
    assert stale == ["materials/NO-SUCH/name"]


@pytest.mark.parametrize("overrides,part", [
    ({"shifts/B/start": "07:00"}, "Shift A would last 1 h"),
    ({"materials/PCBA-BCM-200/routing/SMT_BOT/components/IC-AFE-TQFP64": 1,
      "materials/PCBA-BCM-200/routing/SMT_BOT/steps/PNP_IC": None}, ""),
    ({"station_types/SPI/measurements/paste_volume_pct/lsl": 101}, "must lie around the process mean"),
    ({"crew/breaks/lunch/start": 135}, "overlap"),
])
def test_what_does_not_fit_is_refused(overrides, part):
    with pytest.raises(settings.SettingError) as err:
        settings.check(overrides)
    assert part in err.value.en


def test_values_are_checked_one_by_one():
    raw = settings.base_raw()
    assert settings.normalize("materials/BCM-200/packing/klt_qty", "30", raw) == 30
    assert settings.normalize("materials/PCBA-BCM-200/routing/SMT_BOT/components/RES-0402-10K", 0, raw) is None
    for path, value in [("materials/BCM-200/packing/klt_qty", 0), ("shifts/A/start", "06:10"),
                        ("materials/BCM-200/customer", "NOBODY"), ("downtime_reasons/x1", {"name": "a", "category": "IT"}),
                        ("defect_codes/SOLDER_BRIDGE", {"name": "again", "category": "SOLDERING"}),
                        ("station_types/ICT/interlock", False)]:
        with pytest.raises(settings.SettingError):
            settings.normalize(path, value, raw)


def test_a_draft_released_now_is_in_force_in_the_mes_and_the_plant(app, client):
    mes, run_id, start = _start(client)
    payload = {"run_id": run_id, "plant_time": start.isoformat()}
    version = client.post("/api/v1/edge/ingest", json=payload).json()["scenario"]

    page = client.post("/api/v1/settings/draft", json={"path": CYCLE, "value": 30}).json()
    assert page["draft"]["count"] == 1 and page["draft"]["plant"]
    page = client.post("/api/v1/settings/draft", json={"path": "defect_codes/SOLDER_BRIDGE/name", "value": "Solder bridge (short)"}).json()
    bad = client.post("/api/v1/settings/draft", json={"path": "materials/PCBA-BCM-200/planning/min_lot", "value": 5000})
    assert bad.status_code == 422 and "min_lot" in bad.json()["detail"]["en"]
    assert client.post("/api/v1/settings/draft", json={"path": "station_types/ICT/mtbf_h", "value": 1}).status_code == 422
    # the same value again as in force: out of the draft; a value back to the config files: no change
    client.post("/api/v1/settings/draft", json={"path": "plant/plan_efficiency", "value": 0.9})
    page = client.post("/api/v1/settings/draft", json={"path": "plant/plan_efficiency", "value": 0.85}).json()
    assert page["draft"]["count"] == 2
    step = next(s for r in page["routings"] if r["code"] == "PCBA-BCM-200" for o in r["ops"] if o["op"] == "SMT_BOT"
                for s in o["steps"] if s["type"] == "PNP_CHIP")["cycle"]
    assert step["v"] == 30 and step["f"] == step["c"] == 42 and step["d"]

    assert client.post("/api/v1/settings/release", json={"reason": "", "when": "now"}).status_code == 422
    done = client.post("/api/v1/settings/release", json={"reason": "faster nozzles on the chip placers", "when": "now"}).json()
    assert done["number"] == 1 and done["active"]["number"] == 1 and done["draft"] is None
    assert app.state.model.materials["PCBA-BCM-200"].operation("SMT_BOT").steps["PNP_CHIP"] == 30   # the MES's own plant
    with Session(app.state.engine) as s:                                                           # the master data
        assert s.scalar(select(db.DefectCode.name).where(db.DefectCode.code == "SOLDER_BRIDGE")) == "Solder bridge (short)"
        rev = s.scalar(select(db.SettingRevision).where(db.SettingRevision.number == 1))
        assert rev.check["hours"] == 1 and rev.status == "ACTIVE" and rev.plant
    wish = client.get("/api/v1/sim/scenario").json()                     # the plant builds anew with it
    assert wish["settings"] == {"revision": 1, "overrides": {CYCLE: 30, "defect_codes/SOLDER_BRIDGE/name": "Solder bridge (short)"}}
    assert client.post("/api/v1/edge/ingest", json=payload).json()["scenario"] == version + 1
    reg = mes.register_run(str(uuid.uuid4()), 5, None, {}, settings_rev=1)
    assert reg["run_id"]

    log = client.get("/api/v1/settings/log").json()["revisions"]
    assert log[0]["number"] == 1 and log[0]["reason"] == "faster nozzles on the chip placers"
    change = next(c for c in log[0]["changes"] if c["path"] == CYCLE)
    assert (change["old"], change["new"], change["action"]) == (42, 30, "SET")
    assert "cycle of PNP_CHIP" in change["label"]["en"]
    assert client.get("/api/v1/settings/log?q=SOLDER").json()["revisions"][0]["changes"][0]["path"].startswith("defect")

    # back to the config files in the next revision, at the start of the next shift
    client.post("/api/v1/settings/draft", json={"path": CYCLE, "reset": True})
    later = client.post("/api/v1/settings/release", json={"reason": "the nozzles wear too fast", "when": "shift"}).json()
    assert later["scheduled"]["number"] == 2 and later["active"]["number"] == 1
    when = datetime.fromisoformat(later["scheduled"]["effective_at"])
    assert when > start and when - start <= timedelta(hours=8)
    client.post("/api/v1/edge/ingest", json={**payload, "plant_time": (when - timedelta(seconds=1)).isoformat()})
    assert app.state.model.materials["PCBA-BCM-200"].operation("SMT_BOT").steps["PNP_CHIP"] == 30
    client.post("/api/v1/edge/ingest", json={**payload, "plant_time": when.isoformat()})
    assert app.state.model.materials["PCBA-BCM-200"].operation("SMT_BOT").steps["PNP_CHIP"] == 42
    assert client.get("/api/v1/sim/scenario").json()["settings"]["overrides"] == {"defect_codes/SOLDER_BRIDGE/name": "Solder bridge (short)"}


def test_a_new_reason_code_and_a_draft_thrown_away(app, client):
    page = client.post("/api/v1/settings/draft", json={"path": "downtime_reasons/BARCODE_FAULT",
                                                        "value": {"name": "Barcode reader fault", "category": "IT", "crew": "IT"}}).json()
    assert any(r["code"] == "BARCODE_FAULT" and r["added"] for r in page["reasons"])
    page = client.request("DELETE", "/api/v1/settings/draft").json()
    assert page["draft"] is None and not any(r["code"] == "BARCODE_FAULT" for r in page["reasons"])
    assert client.get("/settings").status_code == 200


def _login_client(pg_url):
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text("drop schema public cascade; create schema public"))
    engine.dispose()
    app = create_app(pg_url, edge_token="", login="actions")
    with Session(app.state.engine) as s:
        set_user(s, "boss", "a-long-test-password")                         # ADMIN
        set_user(s, "qa", "a-long-test-password", role="QUALITY")
    return app, TestClient(app)


def test_roles_decide_who_may_change_what(pg_url):
    app, client = _login_client(pg_url)
    with client:
        draft = {"path": "plant/plan_efficiency", "value": 0.9}
        assert client.post("/api/v1/settings/draft", json=draft).status_code == 401
        assert client.get("/api/v1/settings").status_code == 200                       # anybody may look
        client.post("/api/v1/auth/login", json={"username": "qa", "password": "a-long-test-password"})
        assert client.post("/api/v1/settings/draft", json=draft).status_code == 403
        assert client.post("/api/v1/sim/speed", json={"speed": 60}).status_code == 403
        assert client.post("/api/v1/holds", json={"kind": "WORK_ORDER", "target": "WO-NONE", "reason": "test hold"}).status_code == 404
        assert client.get("/api/v1/users").status_code == 403
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"username": "boss", "password": "a-long-test-password"})
        assert client.post("/api/v1/settings/draft", json=draft).status_code == 200
        made = client.post("/api/v1/users", json={"username": "eng1", "display_name": "Process engineer", "role": "ENGINEER"}).json()
        assert made["password"] and any(u["username"] == "eng1" for u in made["users"])
        assert client.post("/api/v1/users/boss", json={"active": False}).status_code == 409   # not yourself
        client.post("/api/v1/users/qa", json={"active": False})
        client.post("/api/v1/auth/logout")
        assert client.post("/api/v1/auth/login", json={"username": "qa", "password": "a-long-test-password"}).status_code == 401
        assert client.post("/api/v1/auth/login", json={"username": "eng1", "password": made["password"]}).status_code == 200
        assert client.post("/api/v1/settings/draft", json={"path": "plant/plan_efficiency", "value": 0.8}).status_code == 200
        events = client.get("/api/v1/settings/log").json()["users"]
        assert {(e["subject"], e["action"]) for e in events} >= {("eng1", "CREATE"), ("qa", "ACTIVE")}
    app.state.engine.dispose()
