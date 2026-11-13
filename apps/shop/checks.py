"""The shop needs the catalog, payment accounts, member sign-in and the storefront surface, and a
place to keep proof files. PACK_ENABLED is not required: with the pack off nobody has the member
price."""
from django.conf import settings
from django.core.checks import Error, Warning, register

from apps.common import private_files
from apps.shop import constants as C


@register(deploy=True)
def check_shop_dependencies(app_configs, **kwargs):
    if not settings.SHOP_ENABLED:
        return []
    found = []
    needs = (
        ("SANTE_CATALOG_ENABLED", "shop.E001", "The shop sells the catalog's products."),
        ("PAYMENT_METHODS_ENABLED", "shop.E002", "Checkout shows the store's own payment accounts."),
        ("MEMBERS_ENABLED", "shop.E003", "A signed-in member buys at the member price."),
        ("STOREFRONT_ENABLED", "shop.E004", "The shop's storefront routes are on the storefront surface."),
    )
    for name, code, hint in needs:
        if not getattr(settings, name):
            found.append(Error(f"SHOP_ENABLED is on but {name} is not.", hint=hint, id=code))
    if settings.SHOP_SHIPPING_POLICY not in (C.CHARGE, C.ZERO):
        found.append(Error(
            "SHOP_SHIPPING_POLICY is neither 'zero' nor 'charge'.",
            hint="Unset it for 'zero', or set 'charge'.", id="shop.E005",
        ))
    if not private_files.available():
        found.append(Warning(
            "SHOP_ENABLED is on but no private file store is available.",
            hint="Set PRIVATE_BLOB_READ_WRITE_TOKEN; without it a proof upload is refused.",
            id="shop.W001",
        ))
    if not settings.REDIS_URL:
        found.append(Warning(
            "SHOP_ENABLED is on but REDIS_URL is not set.",
            hint="Checkout throttles would be counted in each process's own memory.",
            id="shop.W002",
        ))
    return found
