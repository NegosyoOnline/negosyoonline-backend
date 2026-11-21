"""pg_dump and pg_restore, run as child processes.

The password and host go in the child's environment (PG* variables), never on the command
line, so they appear in no process list. The tools' own messages are never logged or raised:
they can echo connection details. Only the exit status is reported.
"""
import os
import subprocess
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit

from django.conf import settings

TIMEOUT = 3600


class DumpError(Exception):
    """A dump or restore could not be done. The message never holds a host, name or output."""


def connection_from_settings():
    """The default database as a dict of connection parts, or DumpError if it is not PostgreSQL."""
    db = settings.DATABASES["default"]
    if "postgresql" not in db.get("ENGINE", ""):
        raise DumpError("The default database is not PostgreSQL.")
    sslmode = (db.get("OPTIONS") or {}).get("sslmode", "")
    return {
        "host": db.get("HOST") or "", "port": str(db.get("PORT") or ""),
        "user": db.get("USER") or "", "password": db.get("PASSWORD") or "",
        "name": db.get("NAME") or "", "sslmode": sslmode,
    }


def connection_from_url(url):
    parts = urlsplit(url or "")
    if parts.scheme not in ("postgres", "postgresql") or not parts.path.strip("/"):
        raise DumpError("The target is not a PostgreSQL database URL.")
    query = parse_qs(parts.query)
    return {
        "host": parts.hostname or "", "port": str(parts.port or ""),
        "user": unquote(parts.username or ""), "password": unquote(parts.password or ""),
        "name": unquote(parts.path.strip("/")), "sslmode": (query.get("sslmode") or [""])[0],
    }


def same_database(a, b):
    """True when two connections name the same server and database (host spellings for this
    machine are treated as one)."""
    local = {"", "localhost", "127.0.0.1", "::1"}

    def ident(c):
        host = "" if c["host"] in local else c["host"].lower()
        return (host, c["port"] or "5432", c["name"])

    return ident(a) == ident(b)


def _env(conn):
    env = dict(os.environ)
    for variable, key in (
        ("PGHOST", "host"), ("PGPORT", "port"), ("PGUSER", "user"),
        ("PGPASSWORD", "password"), ("PGDATABASE", "name"), ("PGSSLMODE", "sslmode"),
    ):
        env.pop(variable, None)
        if conn[key]:
            env[variable] = conn[key]
    return env


def _run(args, conn, action):
    try:
        done = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            args, env=_env(conn), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False,
        )
    except FileNotFoundError:
        raise DumpError(f"{args[0]} is not installed on this machine.") from None
    except subprocess.TimeoutExpired:
        raise DumpError(f"{action} timed out.") from None
    if done.returncode != 0:
        raise DumpError(f"{action} failed (exit {done.returncode}).")


def dump_database(conn=None):
    """pg_dump -Fc of the default database, as bytes."""
    conn = conn or connection_from_settings()
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "db.dump")
        _run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", path], conn, "pg_dump")
        with open(path, "rb") as handle:
            return handle.read()


def restore_database(data, conn):
    """pg_restore of a custom-format dump into `conn`. Stops at the first error."""
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "db.dump")
        with open(path, "wb") as handle:
            handle.write(data)
        _run(["pg_restore", "--no-owner", "--no-acl", "--exit-on-error", "--dbname", conn["name"], path],
             conn, "pg_restore")
