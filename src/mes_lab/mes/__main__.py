"""MES command line.

    python -m mes_lab.mes [serve] [--host 127.0.0.1] [--port 8070]
    python -m mes_lab.mes user NAME [--password-stdin]   create a user or set its password
    python -m mes_lab.mes migrate                        bring the database schema up to date
    python -m mes_lab.mes cancel-open-orders             cancel unfinished orders (after an incident)
"""

from __future__ import annotations

import argparse
import sys

from mes_lab import setup_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m mes_lab.mes", description="ACME Plant 1 MES")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="run the web server (default)")
    for p in (parser, serve):
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8070)
        p.add_argument("--config", help="config directory (default: ./config)")
    user = sub.add_parser("user", help="create a user or set a new password")
    user.add_argument("name")
    user.add_argument("--display-name")
    user.add_argument("--password-stdin", action="store_true",
                      help="read the password from stdin instead of generating one")
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("cancel-open-orders", help="cancel every unfinished work order (after an incident)")
    args = parser.parse_args(argv)
    setup_logging()

    if args.cmd in (None, "serve"):
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(config_dir=args.config), host=args.host, port=args.port,
                    log_level="warning", proxy_headers=True)
        return

    from .db import make_engine, make_sessionmaker, migrate

    engine = make_engine()
    migrate(engine)
    if args.cmd == "migrate":
        print("schema is up to date")
        return
    if args.cmd == "cancel-open-orders":
        from .control import cancel_open_orders

        with make_sessionmaker(engine)() as session:
            print(f"{cancel_open_orders(session)} work orders cancelled")
        return
    from .auth import generate_password, set_user

    password = sys.stdin.readline().strip() if args.password_stdin else generate_password()
    if len(password) < 10:
        sys.exit("password must have at least 10 characters")
    with make_sessionmaker(engine)() as session:
        set_user(session, args.name, password, args.display_name)
    if args.password_stdin:
        print(f"user {args.name} saved")
    else:
        print(f"user {args.name} saved, password: {password}")


if __name__ == "__main__":
    main()
