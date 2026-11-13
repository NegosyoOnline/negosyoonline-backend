"""`manage.py check` refuses the statements switch without a private store to keep the PDFs in.
A running service treats a missing store as the issue job having nothing it can do."""
from django.conf import settings
from django.core.checks import Error, register

from apps.common import private_files


@register()
def check_statements_store(app_configs, **kwargs):
    if not (settings.BILLING_ENABLED and settings.BILLING_STATEMENTS_ENABLED):
        return []
    if private_files.available():
        return []
    return [Error(
        "BILLING_STATEMENTS_ENABLED is on but no private file store is available.",
        hint="Set PRIVATE_BLOB_READ_WRITE_TOKEN; statements cannot be stored without it.",
        id="statements.E001",
    )]
