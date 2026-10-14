"""Database connection.

MES_DATABASE_URL selects the database (postgresql+psycopg://...). Without it,
development runs a private PostgreSQL from the `pgserver` package with its data
in var/pgdata, so nothing has to be installed on the developer machine.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from mes_lab import PROJECT_ROOT

log = logging.getLogger("mes_lab.db")

_local_servers: dict[Path, object] = {}  # keeps pgserver handles alive


def local_postgres_url(database: str = "mes", pgdata: Path | None = None) -> str:
    """Start (or reuse) the bundled development PostgreSQL and make sure the
    database exists."""
    import pgserver  # dev dependency

    pgdata = (pgdata or Path(os.environ.get("MES_PGDATA", PROJECT_ROOT / "var" / "pgdata"))).resolve()
    server = _local_servers.get(pgdata)
    if server is None:
        pgdata.mkdir(parents=True, exist_ok=True)
        log.info("starting local PostgreSQL in %s", pgdata)
        for attempt in range(1, 7):
            try:
                server = pgserver.get_server(pgdata, cleanup_mode="stop")
                break
            except subprocess.TimeoutExpired:
                # after an unclean stop PostgreSQL recovers first, which can take
                # longer than the 10 s pgserver waits; it keeps starting meanwhile
                log.warning("local PostgreSQL still starting (attempt %d), waiting", attempt)
                time.sleep(10)
        else:
            raise RuntimeError(f"local PostgreSQL in {pgdata} did not start, see {pgdata / 'log'}")
        _local_servers[pgdata] = server
    uri = server.get_uri()  # postgresql://postgres:@127.0.0.1:PORT/postgres
    base = uri.rsplit("/", 1)[0].replace("postgresql://", "postgresql+psycopg://", 1)
    admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        # development data is disposable: skip fsync, which on Windows makes
        # crash recovery and every commit slow
        if conn.execute(text("show fsync")).scalar() == "on":
            conn.execute(text("alter system set fsync = off"))
            conn.execute(text("alter system set synchronous_commit = off"))
            conn.execute(text("select pg_reload_conf()"))
        exists = conn.execute(text("select 1 from pg_database where datname = :d"),
                              {"d": database}).scalar()
        if not exists:
            conn.execute(text(f'create database "{database}"'))
    admin.dispose()
    return f"{base}/{database}"


def database_url() -> str:
    return os.environ.get("MES_DATABASE_URL") or local_postgres_url()


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True, pool_size=5, max_overflow=5)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config():
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def migrate(engine: Engine) -> None:
    """Bring the schema to the newest migration (alembic upgrade head)."""
    from alembic import command

    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
