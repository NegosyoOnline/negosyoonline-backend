"""GET /storefront/v1/domain-probe/?host=<hostname>: which site a hostname is being connected for.

The storefronts server calls it to answer the worker's HTTPS probe (`{"site", "n"}`). It answers
by domain state, not billing state, so a suspended site's probe still works. It is mounted on the
storefront surface, so it carries the server credential like every route there.
"""
from django.http import HttpResponse, JsonResponse
from ninja import Router, Schema

from apps.common.errors import error_body
from apps.domains import hosts

router = Router(tags=["domains"])


class ProbeOut(Schema):
    site: str


@router.get("/domain-probe/", response=ProbeOut)
def domain_probe(request, response: HttpResponse, host: str = ""):
    site = hosts.site_for_probe(host)
    if site is None:
        return JsonResponse(error_body("not_found", "Not found."), status=404)
    return {"site": site.public_id}
