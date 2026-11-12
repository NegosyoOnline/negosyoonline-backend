"""The B5 switches as the routes read them. Each needs billing itself to be on."""
from django.conf import settings


def register_enabled():
    return bool(settings.BILLING_ENABLED and settings.BILLING_INVOICE_REGISTER_ENABLED)


def statements_enabled():
    return bool(settings.BILLING_ENABLED and settings.BILLING_STATEMENTS_ENABLED)


def issuer_enabled():
    """The issuer is shared: the register and the statements both read it."""
    return register_enabled() or statements_enabled()
