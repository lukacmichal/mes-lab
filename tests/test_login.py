"""Who may look and who may change something (MES_LOGIN)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from mes_lab.mes.app import create_app
from mes_lab.mes.auth import set_user

HOLD = {"kind": "WORK_ORDER", "target": "WO-NONE", "reason": "test hold"}


def _client(pg_url, mode):
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text("drop schema public cascade; create schema public"))
    engine.dispose()
    app = create_app(pg_url, edge_token="", login=mode)
    with Session(app.state.engine) as s:
        set_user(s, "operator", "a-long-test-password")
    return app, TestClient(app)


def test_actions_mode_lets_anybody_look_but_not_change(pg_url):
    app, client = _client(pg_url, "actions")
    with client:
        page = client.get("/")
        assert page.status_code == 200 and "Sign in" in page.text
        assert client.get("/holds").status_code == 200
        assert client.get("/api/v1/plant/overview").status_code == 200
        assert client.post("/api/v1/holds", json=HOLD).status_code == 401
        assert client.post("/api/v1/sim/speed", json={"speed": 600}).status_code == 401
        assert client.post("/api/v1/crew/calls", json={"station": "FA-01-ICT", "role": "IT"}).status_code == 401
        assert client.post("/api/v1/quarantine/X/disposition", json={"action": "SCRAP"}).status_code == 401
        assert client.get("/docs").status_code == 404
        assert "noindex" in page.text and client.get("/robots.txt").text.startswith("User-agent")

        bad = client.post("/api/v1/auth/login", json={"username": "operator", "password": "nope"})
        assert bad.status_code == 401
        assert client.post("/api/v1/auth/login",
                           json={"username": "operator", "password": "a-long-test-password"}).status_code == 200
        assert "operator · Sign out" in client.get("/").text
        assert client.post("/api/v1/holds", json=HOLD).status_code == 404  # allowed now; the order does not exist
    app.state.engine.dispose()


@pytest.mark.parametrize("mode,status", [("all", 303), ("off", 200)])
def test_other_modes(pg_url, mode, status):
    app, client = _client(pg_url, mode)
    with client:
        assert client.get("/", follow_redirects=False).status_code == status
        if mode == "all":
            assert client.get("/api/v1/plant/overview").status_code == 401
        else:
            assert client.post("/api/v1/sim/speed", json={"speed": 10}).status_code == 409  # allowed, no run yet
    app.state.engine.dispose()
