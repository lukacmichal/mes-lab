"""Alembic environment. The MES runs migrations itself at start-up
(mes_lab.mes.db.migrate), passing its connection in config.attributes."""

from alembic import context

from mes_lab.mes import models

config = context.config
target_metadata = models.Base.metadata


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("run migrations through mes_lab.mes.db.migrate()")
    context.configure(connection=connection, target_metadata=target_metadata,
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
