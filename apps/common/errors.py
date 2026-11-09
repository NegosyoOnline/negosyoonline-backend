"""The single error shape of the API and the handlers that produce it."""
import logging

from django.http import Http404
from ninja.errors import AuthenticationError, HttpError, ValidationError

logger = logging.getLogger(__name__)

STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    429: "rate_limited",
}


class ApiError(Exception):
    """Raise from a view or service to answer with a specific code and status."""

    def __init__(self, code, message, *, status=400, fields=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.fields = fields


def error_body(code, message, fields=None):
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return {"error": error}


def register_error_handlers(api):
    @api.exception_handler(ApiError)
    def on_api_error(request, exc):
        return api.create_response(
            request, error_body(exc.code, exc.message, exc.fields), status=exc.status
        )

    @api.exception_handler(ValidationError)
    def on_validation_error(request, exc):
        fields = {}
        for item in exc.errors:
            location = item.get("loc") or ("request",)
            fields.setdefault(str(location[-1]), []).append(item.get("msg", "Invalid value."))
        return api.create_response(
            request,
            error_body("invalid_request", "Some values are missing or invalid.", fields),
            status=400,
        )

    @api.exception_handler(AuthenticationError)
    def on_authentication_error(request, exc):
        return api.create_response(
            request, error_body("unauthenticated", "Sign in to continue."), status=401
        )

    @api.exception_handler(HttpError)
    def on_http_error(request, exc):
        code = STATUS_CODES.get(exc.status_code, "error")
        return api.create_response(
            request, error_body(code, str(exc)), status=exc.status_code
        )

    @api.exception_handler(Http404)
    def on_not_found(request, exc):
        return api.create_response(
            request, error_body("not_found", "Not found."), status=404
        )

    @api.exception_handler(Exception)
    def on_unexpected(request, exc):
        logger.exception("Unhandled API error")
        return api.create_response(
            request, error_body("server_error", "Something went wrong."), status=500
        )
