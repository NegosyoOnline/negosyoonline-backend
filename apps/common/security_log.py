"""One WARNING line per access denial, to the `security` logger.

The line holds the event, the route pattern, the method, a coarse reason and whether the caller
was signed in. It never holds an email, mobile, name, code, IP address, account or object id.
"""
import logging

logger = logging.getLogger("security")


def log_denial(request, event, reason):
    """Log one denial. `request` is the Django request the denial answers."""
    match = getattr(request, "resolver_match", None)
    route = (getattr(match, "route", "") or "-").replace(" ", "")
    authenticated = "yes" if getattr(request, "account", None) is not None else "no"
    logger.warning(
        "denied event=%s route=%s method=%s reason=%s authenticated=%s",
        event,
        route,
        getattr(request, "method", "-"),
        reason,
        authenticated,
    )
