"""Database connection rules that settings apply. No Django app code is imported here."""
from django.core.exceptions import ImproperlyConfigured

LOOPBACK_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
WEAK_SSL_MODES = frozenset({"disable", "allow", "prefer"})  # each can end up unencrypted


def is_local_database(database, *, debug):
    """True when the connection never leaves this machine, or this is local development."""
    host = (database.get("HOST") or "").strip().lower()
    return debug or host in LOOPBACK_HOSTS or host.startswith("/")  # "/..." is a socket path


def require_database_tls(database, *, debug):
    """Make a PostgreSQL connection require TLS unless it is local.

    Returns the database settings to use. A deployed configuration that asks for a mode
    which allows an unencrypted connection is refused rather than quietly upgraded.
    """
    if "postgresql" not in database.get("ENGINE", "") or is_local_database(database, debug=debug):
        return database
    options = dict(database.get("OPTIONS") or {})
    mode = options.get("sslmode")
    if mode in WEAK_SSL_MODES:
        raise ImproperlyConfigured(
            f"DATABASE_URL asks for sslmode={mode}, which allows an unencrypted connection. "
            "Outside local development the database connection must require TLS."
        )
    options.setdefault("sslmode", "require")
    return {**database, "OPTIONS": options}
