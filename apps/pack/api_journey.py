"""The Subscriber's buyer journey (ClickUp z8uq9meyqk):

    GET /v1/sites/{site_id}/pack/journey/?period=7d|30d|all   (default 30d)

Counts only, no buyer's name, mobile, email, address or proof. Sign-in, site lookup, the
Billing-only rule and the PACK_ENABLED switch are the pack owner routes' own (`pack_site`).
Any other period answers 422 `invalid_period`.
"""
from ninja import Router

from apps.common.errors import ApiError
from apps.pack import journey as read
from apps.pack.api_owner import pack_site

router = Router(tags=["pack-journey"], auth=pack_site)


@router.get("/sites/{site_id}/pack/journey/")
def journey(request, site_id: str, period: str = read.DEFAULT_PERIOD):
    if period not in read.PERIODS:
        raise ApiError("invalid_period", "Period must be 7d, 30d or all.", status=422)
    return read.journey(request.pack_site, period)
