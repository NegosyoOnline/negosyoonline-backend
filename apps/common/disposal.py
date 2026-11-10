"""What the address of a disposed account looks like.

Shared by the disposal, which writes it, and the send guard, which refuses it, so the two can
never drift apart. `.invalid` is a reserved name that can never receive mail.
"""
DISPOSED_DOMAIN = "deleted.invalid"


def disposed_address(row_number):
    return f"deleted-{row_number}@{DISPOSED_DOMAIN}"


def is_disposed_address(address):
    return (address or "").strip().lower().endswith(f"@{DISPOSED_DOMAIN}")
