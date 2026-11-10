"""The Django admin, asking for the second step when ADMIN_TWO_STEP_REQUIRED is on.

After the password, the code page puts a flag in the Django session. The flag names the
identity and the credential version it was made under, so a lock, reset, disable or enrol
ends it for good. With the switch off this site behaves exactly as Django's own.
"""
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.http import HttpResponseRedirect
from django.utils import timezone

from apps.operators import audit, two_step
from apps.operators.models import AdminIdentity

FLAG = "admin_two_step"
FLAG_HOURS = 12
CODE_PATH = "/admin/two-step/"


def is_operator(user):
    return user.is_authenticated and user.is_active and user.is_staff


def identity_of(user):
    """The one active identity linked to this staff user, or None."""
    return AdminIdentity.objects.select_related("user").filter(user=user, is_active=True).first()


def set_flag(request, identity_id, credential_version):
    request.session[FLAG] = {
        "identity_id": identity_id,
        "credential_version": credential_version,
        "exp": (timezone.now() + timedelta(hours=FLAG_HOURS)).timestamp(),
    }
    request._admin_two_step_ok = True


def _check_flag(request):
    flag = request.session.get(FLAG)
    if not isinstance(flag, dict):
        return False
    now = timezone.now()
    identity = (
        AdminIdentity.objects.select_related("user").filter(pk=flag.get("identity_id")).first()
    )
    return (
        identity is not None
        and flag.get("exp", 0) > now.timestamp()
        and identity.user_id == request.user.pk
        and identity.credential_version == flag.get("credential_version")
        and two_step.eligible(identity, now)
    )


def flag_valid(request):
    """Checked against the identity as it is now. A flag that fails is removed."""
    if not hasattr(request, "_admin_two_step_ok"):
        request._admin_two_step_ok = _check_flag(request)
        if not request._admin_two_step_ok and FLAG in request.session:
            del request.session[FLAG]
    return request._admin_two_step_ok


class TwoStepAdminSite(admin.AdminSite):
    def has_permission(self, request):
        if not super().has_permission(request):
            return False
        return not settings.ADMIN_TWO_STEP_REQUIRED or flag_valid(request)

    def login(self, request, extra_context=None):
        if (
            settings.ADMIN_TWO_STEP_REQUIRED
            and request.method == "GET"
            and is_operator(request.user)
            and not flag_valid(request)
        ):
            # The password is accepted; the code page asks for the rest. It never sends back here.
            target = request.GET.get(REDIRECT_FIELD_NAME, "")
            query = f"?{urlencode({REDIRECT_FIELD_NAME: target}, safe='/')}" if target else ""
            return HttpResponseRedirect(CODE_PATH + query)
        return super().login(request, extra_context)


def audit_sign_out(sender, request, user, **kwargs):
    """Connected to user_logged_out: both the admin's own log-out and the code pages' Sign out."""
    if not settings.ADMIN_TWO_STEP_REQUIRED or user is None or not user.is_staff:
        return
    identity = identity_of(user)
    if identity is None:
        return
    audit.record(
        user,
        "admin_sign_out",
        target_type=audit.IDENTITY_TARGET,
        target_id=identity.pk,
        detail={"surface": two_step.SURFACE_DJANGO_ADMIN},
    )
