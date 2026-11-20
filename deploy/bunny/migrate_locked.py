"""Run `manage.py migrate --noinput` under a PostgreSQL advisory lock, then prove no plan is left."""
import os
import subprocess
import sys
import time

import psycopg

LOCK_KEY = 7_340_021_551  # fixed: the same key in every environment
WAIT_SECONDS = int(os.environ.get("MIGRATE_LOCK_WAIT_SECONDS", "600"))


def run(*args):
    return subprocess.run([sys.executable, "manage.py", *args], check=False).returncode


def main():
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        print("migrate: DATABASE_URL must be a PostgreSQL URL", file=sys.stderr)
        return 2
    # The lock belongs to this connection's session: released on unlock, or when the process
    # dies for any reason, so a crashed run can never leave it held.
    with psycopg.connect(url, autocommit=True, connect_timeout=10) as lock_conn:
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            if lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
                break
            if time.monotonic() >= deadline:
                print(f"migrate: lock not acquired within {WAIT_SECONDS}s", file=sys.stderr)
                return 1
            time.sleep(3)
        try:
            print("migrate: lock acquired", flush=True)
            if run("migrate", "--noinput") != 0:
                print("migrate: migrate failed", file=sys.stderr)
                return 1
            # Exit 0 only when no migration is left unapplied.
            if run("migrate", "--check") != 0:
                print("migrate: a migration plan is still pending", file=sys.stderr)
                return 1
            print("migrate: done, no plan left", flush=True)
            return 0
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


if __name__ == "__main__":
    sys.exit(main())
