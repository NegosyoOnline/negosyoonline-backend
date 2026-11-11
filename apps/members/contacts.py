"""One normalisation for a member's contacts."""
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.common.errors import ApiError
from apps.common.masking import mask_email, mask_mobile
from apps.common.phones import normalise_ph_mobile

MOBILE_ERROR = "Enter a valid Philippine mobile number."
EMAIL_ERROR = "Enter a valid email address."


def _invalid(field, message):
    return ApiError("invalid_request", "Some values are missing or invalid.", fields={field: [message]})


def clean_email(raw, field="email"):
    value = (raw or "").strip().lower()
    try:
        validate_email(value)
    except ValidationError:
        raise _invalid(field, EMAIL_ERROR) from None
    return value


def clean_mobile(raw, field="mobile"):
    try:
        value = normalise_ph_mobile(raw)
    except ValueError:
        raise _invalid(field, MOBILE_ERROR) from None
    if not value.isascii():  # the pattern's \d takes other scripts' digits; the database check does not
        raise _invalid(field, MOBILE_ERROR)
    return value


def parse_identifier(raw, field="contact"):
    """Sign-in identifier: an '@' means an email, anything else a mobile."""
    value = (raw or "").strip()
    if "@" in value:
        return "email", clean_email(value, field)
    return "mobile", clean_mobile(value, field)


def mask(kind, value):
    return mask_email(value) if kind == "email" else mask_mobile(value)
