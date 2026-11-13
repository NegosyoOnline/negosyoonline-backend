"""The Membership Pack checkout needs the member tables and a place to keep proof files."""
from django.conf import settings
from django.core.checks import Error, Warning, register

from apps.common import private_files


@register(deploy=True)
def check_pack_dependencies(app_configs, **kwargs):
    found = []
    if settings.PACK_SHIPPING_ENABLED and not settings.PACK_ENABLED:
        found.append(Error(
            "PACK_SHIPPING_ENABLED is on but PACK_ENABLED is not.",
            hint="Shipment is recorded on Membership Pack orders; turn PACK_ENABLED on first.",
            id="pack.E003",
        ))
    if settings.PACK_PURGE_ENABLED and not settings.PACK_ENABLED:
        found.append(Error(
            "PACK_PURGE_ENABLED is on but PACK_ENABLED is not.",
            hint="The purge clears Membership Pack buyer data; turn PACK_ENABLED on first.",
            id="pack.E004",
        ))
    if settings.PACK_BUYER_NOTICE_ENABLED and not settings.PACK_PURGE_ENABLED:
        found.append(Error(
            "PACK_BUYER_NOTICE_ENABLED is on but PACK_PURGE_ENABLED is not.",
            hint="The day-76 email promises the purge; turn PACK_PURGE_ENABLED on first.",
            id="pack.E005",
        ))
    if not settings.PACK_ENABLED:
        return found
    if not settings.MEMBERS_ENABLED:
        found.append(Error(
            "PACK_ENABLED is on but MEMBERS_ENABLED is not.",
            hint="A pack order creates a Santé member; turn MEMBERS_ENABLED on first.",
            id="pack.E001",
        ))
    if not settings.SANTE_AGREEMENT_ENABLED:
        found.append(Error(
            "PACK_ENABLED is on but SANTE_AGREEMENT_ENABLED is not.",
            hint="Checkout records the Membership Agreement version the buyer accepted.",
            id="pack.E002",
        ))
    if not private_files.available():
        found.append(Warning(
            "PACK_ENABLED is on but no private file store is available.",
            hint="Set PRIVATE_BLOB_READ_WRITE_TOKEN; without it a proof upload is refused.",
            id="pack.W001",
        ))
    if not settings.REDIS_URL:
        found.append(Warning(
            "PACK_ENABLED is on but REDIS_URL is not set.",
            hint="Checkout throttles would be counted in each process's own memory.",
            id="pack.W002",
        ))
    return found
