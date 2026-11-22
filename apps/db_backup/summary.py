"""The count summary of a restore: how many tables, and how many rows in each. Counts only, never a
row, a column or a value. It is kept in the private Blob store under `ops/`, so a restore into the
data app can be proved without anyone reading the data.
"""
import json
from datetime import datetime, timezone

import psycopg
from psycopg import sql

PREFIX = "ops/"
NAME = PREFIX + "restore-summary-{stamp}.json"
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


def table_counts(conn):
    """{"schema.table": row_count} for every ordinary table outside the system schemas.

    `conn` is the dict of connection parts from `dump.connection_from_url`. Exact counts
    (`count(*)`), one query per table; the connection is read-only.
    """
    kwargs = {"host": conn["host"], "dbname": conn["name"], "user": conn["user"], "password": conn["password"],
              "connect_timeout": 15}
    if conn["port"]:
        kwargs["port"] = int(conn["port"])
    if conn["sslmode"]:
        kwargs["sslmode"] = conn["sslmode"]
    counts = {}
    with psycopg.connect(**{k: v for k, v in kwargs.items() if v != ""}, autocommit=True) as db:
        db.read_only = True
        tables = db.execute(
            "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') AND n.nspname <> ALL(%s) AND n.nspname NOT LIKE 'pg_toast%%' "
            "ORDER BY 1, 2",
            (list(SYSTEM_SCHEMAS),),
        ).fetchall()
        for schema, table in tables:
            query = sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
            counts[f"{schema}.{table}"] = db.execute(query).fetchone()[0]
    return counts


def build(counts, now=None, source=""):
    """The summary document (bytes): table count, per-table rows, total, and the time."""
    now = now or datetime.now(timezone.utc)
    return json.dumps(
        {
            "at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_copy": source,
            "tables": len(counts),
            "total_rows": sum(counts.values()),
            "rows": dict(sorted(counts.items())),
        },
        indent=2,
    ).encode()


def pathname_for(now=None):
    now = now or datetime.now(timezone.utc)
    return NAME.format(stamp=now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))


def write(store, counts, now=None, source=""):
    """Put the summary under `ops/` and return its pathname."""
    pathname = pathname_for(now)
    store.put(pathname, build(counts, now, source), "application/json")
    return pathname
