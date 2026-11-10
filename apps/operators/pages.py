"""The admin sign-in pages are rendered without the request: no context processor runs."""
from django.http import HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.template.loader import render_to_string


class SeeOther(HttpResponseRedirect):
    """The answer to a form that worked, so a refresh never sends it again."""

    status_code = 303


def page(request, template, context=None, status=200):
    html = render_to_string(template, {"csrf_token": get_token(request), **(context or {})})
    return HttpResponse(html, status=status)
