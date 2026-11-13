"""Masked forms of contact details, for screens that list people."""

HIDDEN = "***"


def mask_email(email):
    """ana@example.com -> a***@example.com. Anything that is not an address becomes ***."""
    value = (email or "").strip()
    if not value:
        return ""
    local, separator, domain = value.rpartition("@")
    if not separator or not local or not domain:
        return HIDDEN
    return f"{local[0]}{HIDDEN}@{domain}"


def mask_mobile(mobile):
    """+639171234567 -> +63917***4567. A value too short to mask safely becomes ***."""
    value = (mobile or "").strip()
    if not value:
        return ""
    if len(value) < 10:
        return HIDDEN
    return f"{value[:6]}{HIDDEN}{value[-4:]}"
