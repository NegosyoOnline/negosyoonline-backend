"""Two startup errors for a switch set in a way that cannot work. `manage.py check` and
`check --deploy` report them; with every switch off neither fires."""
from django.conf import settings
from django.core.checks import Error, register


@register()
def check_ledger_switches(app_configs, **kwargs):
    errors = []
    if settings.LEDGER_POST_BILLING_ENABLED and not settings.LEDGER_ENABLED:
        errors.append(
            Error(
                "LEDGER_POST_BILLING_ENABLED is on but LEDGER_ENABLED is not.",
                hint="Posting from billing stays off until LEDGER_ENABLED is on.",
                id="ledger.E001",
            )
        )
    if settings.LEDGER_LIVE_BOOKS and settings.DEBUG:
        errors.append(
            Error(
                "LEDGER_LIVE_BOOKS is on while DEBUG is on.",
                hint="Live books are only for the real deployment, never a debug run.",
                id="ledger.E002",
            )
        )
    return errors
