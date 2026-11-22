"""Deploy and system checks for custom domains. They name settings, never values."""
from django.conf import settings
from django.core.checks import Error, Warning, register

from apps.domains import names


@register(deploy=True)
def check_domains(app_configs, **kwargs):
    found = []
    if not names.psl_available():  # always: the file is not in the repository yet (fail closed)
        found.append(Warning(
            "The public suffix list file is missing, so every custom domain name is refused.",
            hint="Install apps/domains/data/public_suffix_list.dat (its download needs Coach Ed's permission).",
            id="domains.W001",
        ))
    if not settings.DOMAINS_ENABLED:
        return found
    if settings.DOMAINS_PROVIDER == "real":
        missing = [
            name for name in (
                "CLOUDFLARE_DNS_API_TOKEN", "VERCEL_API_TOKEN", "VERCEL_STOREFRONTS_PROJECT_ID",
                "CLOUDFLARE_ACCOUNT_ID",
            ) if not getattr(settings, name)
        ]
        if missing:
            found.append(Warning(
                "DOMAINS_PROVIDER is real but these settings are empty: " + ", ".join(missing) + ".",
                id="domains.W002",
            ))
        if not settings.DOMAINS_PLATFORM_ZONES:
            found.append(Warning(
                "DOMAINS_PROVIDER is real but DOMAINS_PLATFORM_ZONES is empty, so the DNS adapter refuses to run.",
                hint="List every platform zone name.", id="domains.W003",
            ))
    if settings.DOMAINS_PURCHASE_ENABLED:
        unset = [
            name for name, value in (
                ("CLOUDFLARE_REGISTRAR_API_TOKEN", settings.CLOUDFLARE_REGISTRAR_API_TOKEN),
                ("DOMAINS_MAX_COST_USD", settings.DOMAINS_MAX_COST_CENTS),
                ("DOMAINS_DAILY_PURCHASE_CAP", settings.DOMAINS_DAILY_PURCHASE_CAP),
                ("DOMAINS_DAILY_SPEND_CAP_USD", settings.DOMAINS_DAILY_SPEND_CAP_CENTS),
                ("DOMAINS_ALLOWED_TLDS", settings.DOMAINS_ALLOWED_TLDS),
            ) if not value
        ]
        if unset:
            found.append(Warning(
                "DOMAINS_PURCHASE_ENABLED is on but these settings are empty or 0, so nothing can be bought: "
                + ", ".join(unset) + ".",
                id="domains.W004",
            ))
    return found


@register()
def check_domains_fake(app_configs, **kwargs):
    """The fake provider is for local runs only: it is an error unless DEBUG is on."""
    if settings.DOMAINS_PROVIDER == "fake" and not settings.DEBUG:
        return [Error("DOMAINS_PROVIDER=fake needs DJANGO_DEBUG=true.", id="domains.E001")]
    return []
