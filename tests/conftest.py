from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from mes_lab.model import load_model


@pytest.fixture(scope="session")
def model():
    return load_model()


@pytest.fixture(scope="session")
def pg_url() -> str:
    """MES_TEST_DATABASE_URL, or a `mes_test` database on the local dev PostgreSQL."""
    url = os.environ.get("MES_TEST_DATABASE_URL")
    if url:
        return url
    from mes_lab.mes.db import local_postgres_url

    return local_postgres_url("mes_test")


@pytest.fixture
def app(pg_url, tmp_path, monkeypatch):
    from mes_lab.mes.app import create_app

    monkeypatch.setenv("MES_VAR_DIR", str(tmp_path))          # the tuning board and the live scenario of the test
    engine = create_engine(pg_url)
    with engine.begin() as conn:  # empty database; the app migrates it
        conn.execute(text("drop schema public cascade; create schema public"))
    engine.dispose()
    app = create_app(pg_url, edge_token="", require_login=False)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c
