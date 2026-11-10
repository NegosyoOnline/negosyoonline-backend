"""Everything the hq host serves. HqHostMiddleware selects this instead of config.urls."""
from django.urls import path, re_path

from apps.operators import hq_views

urlpatterns = [
    path("", hq_views.home),
    path("sign-in/", hq_views.sign_in),
    path("code/", hq_views.code),
    path("verify/", hq_views.verify),
    path("sign-out/", hq_views.sign_out),
    # Matched here rather than left to handler404, so that DEBUG shows the same page.
    re_path(r"^.*$", hq_views.not_found),
]

handler400 = "apps.operators.hq_views.bad_request"
handler403 = "apps.operators.hq_views.forbidden"
handler404 = "apps.operators.hq_views.not_found"
handler500 = "apps.operators.hq_views.server_error"
