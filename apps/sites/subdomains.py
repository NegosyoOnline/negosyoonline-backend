"""Subdomain rules. A name is never reissued: the Subdomain ledger decides what is taken."""
import re

from apps.sites.models import Subdomain

MIN_LENGTH, MAX_LENGTH = 3, 30
FORMAT = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")

# Never assignable.
INFRA_LABELS = frozenset({
    "www", "dash", "hq", "api", "admin", "app", "mail", "static", "notify", "billing",
    "login", "support", "help", "status", "ggwp", "webhook",
})
# Reserved for the brand; a subscriber cannot choose them.
BRAND_NAMES = frozenset({"sante", "santebarley", "santebarleyph", "official", "negosyoonline"})

AVAILABLE, TAKEN, RESERVED, INVALID = "available", "taken", "reserved", "invalid"
MESSAGES = {
    AVAILABLE: "",
    INVALID: "Use 3 to 30 lowercase letters, digits or single hyphens.",
    RESERVED: "That name is reserved. Please choose another.",
    TAKEN: "That name is already taken. Please choose another.",
}


def normalise(raw):
    return (raw or "").strip().lower()


def _squash(name):
    """The name without hyphens and digits, so `w-w-w` and `api2` match their labels."""
    return re.sub(r"[-0-9]", "", name)


def is_reserved(name):
    squashed = _squash(name)
    if name in INFRA_LABELS or squashed in INFRA_LABELS:
        return True
    if name in BRAND_NAMES or squashed in BRAND_NAMES or "official" in squashed:
        return True
    if "sante" in squashed:
        return "ph" in squashed.replace("sante", "") or "corporate" in squashed
    return False


def is_valid_format(name):
    return (
        MIN_LENGTH <= len(name) <= MAX_LENGTH
        and FORMAT.fullmatch(name) is not None
        and "--" not in name
    )


def check(raw):
    """Return (name, status) for a requested subdomain. Reads the ledger; changes nothing.

    Reserved is answered before invalid, so a short label such as `hq` reads as reserved.
    """
    name = normalise(raw)
    if is_reserved(name):
        return name, RESERVED
    if not is_valid_format(name):
        return name, INVALID
    if Subdomain.objects.filter(name=name, released_at__isnull=True).exists():
        return name, TAKEN
    return name, AVAILABLE
