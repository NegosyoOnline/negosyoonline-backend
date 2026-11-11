"""Santé site settings endpoints. Shapes here are the frontend's contract.

Every endpoint is signed-in only and reaches its site through
site_for(account, site_id, template=templates.SANTE), so a missing site, a removed site,
another account's site and a site that is not a Santé site all answer the same 404. While
SANTE_SETTINGS_ENABLED is off every route answers that 404 too, signed in or not.
"""
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.accounts.api import signed_in
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.sante import services
from apps.sites import templates
from apps.sites.services import site_for

WRITE_LIMIT, WRITE_WINDOW = 30, 3600  # settings writes per account per hour


def switched_on(request):
    """The router's sign-in check, closed while the switch is off."""
    if not services.enabled():
        raise ApiError("not_found", "Not found.", status=404)
    return signed_in(request)


router = Router(tags=["sante"], auth=switched_on)


class SanteSettingsPatch(Schema):
    """An unknown key is refused, so `catalog_owner` can never be sent."""

    model_config = ConfigDict(extra="forbid")

    sponsor_id: str | None = None


class SanteSettingsOut(Schema):
    sponsor_id: str
    registration_ready: bool
    catalog_owner: bool


def _site(request, site_id):
    return site_for(request.account, site_id, template=templates.SANTE)


@router.get("/sites/{site_id}/sante/settings/", response=SanteSettingsOut)
def sante_settings_detail(request, site_id: str):
    return services.settings_out(_site(request, site_id))


@router.patch("/sites/{site_id}/sante/settings/", response=SanteSettingsOut)
def sante_settings_update(request, site_id: str, payload: SanteSettingsPatch):
    site = _site(request, site_id)
    hit("sante-settings-write", request.account.pk, limit=WRITE_LIMIT, window=WRITE_WINDOW)
    site = services.update_settings(request.account, site, payload.dict(exclude_unset=True))
    return services.settings_out(site)
