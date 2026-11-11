"""Creating a member and changing a member's contacts. Other apps call these functions, never
the tables. There is no public sign-up: the order slice creates the member."""
from django.conf import settings
from django.db import IntegrityError, transaction

from apps.common.errors import ApiError
from apps.members import contacts, sessions
from apps.members.models import ONBOARDING, PENDING, Member
from apps.sites.models import Site

IN_USE = {
    "mobile": "This mobile number is already registered.",
    "email": "This email is already registered.",
}
NAME_ERROR = "Enter the member's full name."


def enabled():
    return settings.MEMBERS_ENABLED


def _in_use(field):
    return ApiError("contact_in_use", IN_USE[field], status=409, fields={field: [IN_USE[field]]})


def _taken_here(site, field, value, exclude=None):
    rows = Member.objects.filter(site=site, **{field: value})
    return rows.exclude(pk=exclude.pk).exists() if exclude else rows.exists()


def _held_elsewhere(site, field, value):
    """A member of another live site holds this contact. The refusal never names the site."""
    return (
        Member.objects.filter(site__deleted_at__isnull=True, **{field: value})
        .exclude(site=site)
        .exists()
    )


def _refuse_if_in_use(site, field, value, exclude=None):
    if _taken_here(site, field, value, exclude) or _held_elsewhere(site, field, value):
        raise _in_use(field)


def _violated(exc):
    text = str(exc)
    if "members_member_site_mobile" in text or "members_member.mobile" in text:
        return _in_use("mobile")
    if "members_member_site_email" in text or "members_member.email" in text:
        return _in_use("email")
    return None


def create_member(site, *, full_name, mobile, email, source=ONBOARDING):
    """A new member of this site, both contacts pending. Raises ApiError: invalid_request for a
    bad value, contact_in_use (409) when the mobile or the email is already a member here, or of
    another live site, and not_found when the site is gone."""
    name = (full_name or "").strip()
    if not 1 <= len(name) <= 120:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"full_name": [NAME_ERROR]},
        )
    mobile, email = contacts.clean_mobile(mobile), contacts.clean_email(email)
    with transaction.atomic():
        locked = Site.objects.select_for_update().filter(pk=site.pk, deleted_at__isnull=True).first()
        if locked is None:
            raise ApiError("not_found", "Not found.", status=404)
        _refuse_if_in_use(locked, "mobile", mobile)
        _refuse_if_in_use(locked, "email", email)
        try:
            with transaction.atomic():
                return Member.objects.create(
                    site=locked, full_name=name, mobile=mobile, email=email, source=source
                )
        except IntegrityError as exc:
            raise _violated(exc) or exc


def change_contacts(member, *, mobile=None, email=None):
    """Replace the member's mobile and/or email. A new contact is pending until a code sent to it
    is used, and every session of the member is revoked. Returns the member."""
    if mobile is None and email is None:
        return member
    mobile = contacts.clean_mobile(mobile) if mobile is not None else None
    email = contacts.clean_email(email) if email is not None else None
    with transaction.atomic():
        locked = Member.objects.select_for_update().get(pk=member.pk)
        fields = ["updated_at"]
        for field, value in (("mobile", mobile), ("email", email)):
            if value is None or value == getattr(locked, field):
                continue
            _refuse_if_in_use(locked.site, field, value, exclude=locked)
            setattr(locked, field, value)
            setattr(locked, f"{field}_state", PENDING)
            setattr(locked, f"{field}_verified_at", None)
            fields += [field, f"{field}_state", f"{field}_verified_at"]
        if len(fields) > 1:
            try:
                with transaction.atomic():
                    locked.save(update_fields=fields)
            except IntegrityError as exc:
                raise _violated(exc) or exc
            sessions.revoke_sessions(locked)
    return locked
