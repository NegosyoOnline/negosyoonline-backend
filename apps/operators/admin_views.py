"""The Django admin's code page (and its "not set up" and "paused" answers) and Sign out.

These sit outside the admin site, so they are reachable without the flag and carry no admin
chrome. They use the hq shell.
"""
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME, logout
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.operators import admin_site, two_step
from apps.operators.pages import SeeOther, page

ADMIN_INDEX = "/admin/"
ADMIN_LOGIN = "/admin/login/"


def _private(response):
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex"
    return response


def _next(request):
    target = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME) or ""
    safe = url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return target if safe else ADMIN_INDEX


@require_http_methods(["GET", "POST"])
def two_step_code(request):
    if not settings.ADMIN_TWO_STEP_REQUIRED:
        return HttpResponseRedirect(ADMIN_INDEX)
    target = _next(request)
    if not admin_site.is_operator(request.user):
        query = urlencode({REDIRECT_FIELD_NAME: target}, safe="/")
        return HttpResponseRedirect(f"{ADMIN_LOGIN}?{query}")
    if admin_site.flag_valid(request):
        return SeeOther(target)

    identity = admin_site.identity_of(request.user)
    now = timezone.now()
    if identity is None or identity.enrolled_at is None or not identity.totp_secret_ciphertext:
        return _private(page(request, "hq/not_set_up.html", status=403))
    if two_step.is_locked(identity, now):
        return _private(page(request, "hq/paused.html"))

    context = {"recovery": request.GET.get("method") == "recovery", "next": target}
    if request.method == "GET":
        return _private(page(request, "hq/two_step.html", context))
    typed = request.POST.get("code", "")
    if not typed.strip():
        return _private(page(request, "hq/two_step.html", {**context, "error": True}))
    result = two_step.verify_second_step(
        identity.pk, typed, surface=two_step.SURFACE_DJANGO_ADMIN
    )
    if result.locked:
        return _private(page(request, "hq/paused.html"))
    if not result.ok:
        return _private(page(request, "hq/two_step.html", {**context, "error": True}))
    request.session.cycle_key()
    # The version comes from the check's own locked read, never from a second look.
    admin_site.set_flag(request, identity.pk, result.credential_version)
    return _private(SeeOther(target))


@require_POST
def two_step_sign_out(request):
    logout(request)  # ends the Django session, and the flag with it
    return _private(SeeOther(ADMIN_LOGIN))
