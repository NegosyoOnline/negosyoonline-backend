"""The admin lists. hq is their only door.

They are mounted only in the hq URL set, and the user they see is the one HqUserMiddleware took
from an hq session that passed both steps. There is no password sign-in: the admin's own login
page sends the browser to hq, and its log-out is hq's sign-out.
"""
from django.contrib import admin
from django.http import HttpResponseRedirect

from apps.operators import hq_views


class HqAdminSite(admin.AdminSite):
    site_header = "NegosyoOnline admin"
    site_title = "NegosyoOnline admin"
    index_title = "Admin lists"

    def has_permission(self, request):
        session = getattr(request, "hq_session", None)
        return (
            hq_views.is_verified(session)
            and request.user.pk == session.identity.user_id
            and super().has_permission(request)
        )

    def login(self, request, extra_context=None):
        # hq's first page sends on to the sign-in page or the second step, as needed.
        return HttpResponseRedirect(hq_views.HOME)

    def logout(self, request, extra_context=None):
        return hq_views.sign_out(request)

    def password_change(self, request, extra_context=None):
        return hq_views.not_found(request)

    def password_change_done(self, request, extra_context=None):
        return hq_views.not_found(request)
