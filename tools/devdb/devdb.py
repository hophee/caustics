"""Local PostgreSQL for development and tests, without Docker.

Uses the ``pgserver`` package, which ships real PostgreSQL binaries as a pip wheel. The server listens
on a Unix socket inside the data directory; the backend connects with the printed URL.

    uv run --project tools/devdb kompas-devdb start            # prints DATABASE_URL
    uv run --project tools/devdb kompas-devdb url
    uv run --project tools/devdb kompas-devdb stop
    uv run --project tools/devdb kompas-devdb reset             # stop and delete all data

Data directory: ``$KOMPAS_DEVDB_DIR`` or ``.devdb/`` at the repository root (gitignored). The Unix
socket goes to a short directory (``$KOMPAS_DEVDB_SOCKET_DIR`` or ``/tmp/kompas-devdb-<uid>``) because
socket paths are limited to ~107 bytes and worktree paths are long. The server is started with
``cleanup_mode=None`` so it keeps running after this CLI exits.

Tests (Python 3.13) don't import this module: conftest reads ``KOMPAS_TEST_DATABASE_URL`` or runs
``uv run --project tools/devdb kompas-devdb start --database kompas_test`` as a subprocess and parses
the printed URL; then ``alembic upgrade head`` runs once per session.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Directory holding the cluster (created on first start)."""
    return Path(os.environ.get("KOMPAS_DEVDB_DIR", REPO_ROOT / ".devdb")).resolve()


def socket_dir() -> Path:
    """Short directory for the Unix socket."""
    return Path(os.environ.get("KOMPAS_DEVDB_SOCKET_DIR", f"/tmp/kompas-devdb-{os.getuid()}"))


def _bin(name: str) -> str:
    import pgserver  # imported lazily: only available inside this tool's environment

    return str(Path(pgserver.__file__).parent / "pginstall" / "bin" / name)


def _running() -> bool:
    return subprocess.run([_bin("pg_ctl"), "-D", str(data_dir()), "status"], capture_output=True).returncode == 0


def _psql(database: str, sql: str) -> str:
    out = subprocess.run(
        [_bin("psql"), "-h", str(socket_dir()), "-U", "postgres", "-d", database, "-At", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _url(database: str) -> str:
    return f"postgresql+asyncpg://postgres@/{database}?host={socket_dir()}"


def start(database: str = "kompas") -> str:
    """Start (or reuse) the server, create ``database`` if missing, return an asyncpg URL
    ``postgresql+asyncpg://postgres@/kompas?host=<socket dir>``."""
    pgdata = data_dir()
    sockets = socket_dir()
    sockets.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (pgdata / "PG_VERSION").exists():
        pgdata.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [_bin("initdb"), "-D", str(pgdata), "-U", "postgres", "--auth=trust", "-E", "UTF8", "--locale=C.UTF-8"],
            capture_output=True,
            check=True,
        )
    if not _running():
        options = f"-k {sockets} -c listen_addresses='' -c max_connections=200 -c timezone=UTC"
        subprocess.run(
            [_bin("pg_ctl"), "-D", str(pgdata), "-l", str(pgdata / "server.log"), "-o", options, "-w", "start"],
            capture_output=True,
            check=True,
        )
    exists = _psql("postgres", f"SELECT 1 FROM pg_database WHERE datname = '{database}'")
    if not exists:
        _psql("postgres", f'CREATE DATABASE "{database}"')
    return _url(database)


def url(database: str = "kompas") -> str:
    """URL of the running server (raises SystemExit if it is not running)."""
    if not _running():
        raise SystemExit("kompas-devdb: server is not running (run `kompas-devdb start`)")
    return _url(database)


def stop() -> None:
    """Stop the server if running."""
    if (data_dir() / "PG_VERSION").exists() and _running():
        subprocess.run([_bin("pg_ctl"), "-D", str(data_dir()), "-m", "fast", "-w", "stop"], capture_output=True, check=True)


def reset() -> None:
    """Stop the server and delete the data directory."""
    stop()
    shutil.rmtree(data_dir(), ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (start | url | stop | reset)."""
    parser = argparse.ArgumentParser(prog="kompas-devdb")
    parser.add_argument("command", choices=["start", "url", "stop", "reset"])
    parser.add_argument("--database", default="kompas")
    args = parser.parse_args(argv)
    if args.command == "start":
        print(start(args.database))
    elif args.command == "url":
        print(url(args.database))
    elif args.command == "stop":
        stop()
    else:
        reset()
    return 0


if __name__ == "__main__":
    sys.exit(main())
