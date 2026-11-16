"""The Subscriber's buyer journey (ClickUp z8uq9meyqk):

    GET /v1/sites/{site_id}/pack/journey/?period=7d|30d|all   (default 30d)

Counts only, no buyer's name, mobile, email, address or proof. Sign-in, site lookup, the
Billing-only rule and the PACK_ENABLED switch are the pack owner routes' own (`pack_site`).
Any other period answers 422 `invalid_period`.

    GET /v1/sites/{site_id}/pack/journey/prospects/?stage=&q=&page=   (ClickUp z8uq9mfw14)

The people who placed an order, visited the Membership Pack page or wrote to the site, newest activity
first, 25 to a page: a name, the order number, the source, the furthest stage (new_lead, landing_page,
start_watching, done_watching, added_to_cart, checked_out, account_setup, activated), the video progress
and the last activity. Only a visit with an unfinished checkout (no order) shows its
mobile and email, in full; an order or an inquiry row shows neither.
An unknown `stage` or a `page` that is not a whole number from 1 answers 422 `invalid_stage` or `invalid_page`.
"""
from ninja import Router

from apps.common.errors import ApiError
from apps.pack import journey as read
from apps.pack import prospects as people
from apps.pack.api_owner import pack_site

router = Router(tags=["pack-journey"], auth=pack_site)


@router.get("/sites/{site_id}/pack/journey/")
def journey(request, site_id: str, period: str = read.DEFAULT_PERIOD):
    if period not in read.PERIODS:
        raise ApiError("invalid_period", "Period must be 7d, 30d or all.", status=422)
    return read.journey(request.pack_site, period)


@router.get("/sites/{site_id}/pack/journey/prospects/")
def prospect_list(request, site_id: str, stage: str = "", q: str = "", page: str = "1"):
    if stage and stage not in people.STAGES:
        raise ApiError("invalid_stage", "Stage is not one of the listed stages.", status=422)
    if not page.isascii() or not page.isdigit() or int(page) < 1:
        raise ApiError("invalid_page", "Page must be a whole number from 1.", status=422)
    return people.prospects(request.pack_site, stage=stage, q=q[:120], page=int(page))
