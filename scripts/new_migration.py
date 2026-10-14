"""Create a new Alembic migration from the difference between the models and the
migrations so far.

    python scripts/new_migration.py "add something"

Runs against a scratch database `mes_migrations` on the local development
PostgreSQL: upgrades it to head, then autogenerates the next revision.
Review the generated file before committing it.
"""

from __future__ import annotations

import sys

from alembic import command
from sqlalchemy import create_engine, text

from mes_lab.mes.db import alembic_config, local_postgres_url, migrate


def main() -> None:
    message = sys.argv[1] if len(sys.argv) > 1 else "schema change"
    url = local_postgres_url("mes_migrations")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("drop schema public cascade; create schema public"))
    migrate(engine)
    config = alembic_config()
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.revision(config, message=message, autogenerate=True)
    engine.dispose()


if __name__ == "__main__":
    main()
