"""The central Membership Agreement (Coach Ed 2026-10-31): one PDF, published by an operator
with a command, shown to the buyers of every Santé site. The file lives in the private store
under a new key per upload and is checked against its SHA-256 on every read.

Write through these functions, never the tables. Behind SANTE_AGREEMENT_ENABLED.
Lock order: the MembershipAgreement row, then a version row.
"""
import hashlib
import logging
import re
from datetime import date

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common import private_files
from apps.operators import audit
from apps.sante.models import MembershipAgreement, MembershipAgreementVersion

logger = logging.getLogger(__name__)

FOLDER = "sante/agreement"
MAX_BYTES = 20 * 1024 * 1024
PDF = "application/pdf"
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
MAX_REASON = 500
TARGET = "sante_agreement_version"


class AgreementError(Exception):
    """A refusal the operator can read. The message never holds file contents."""


def enabled():
    return settings.SANTE_AGREEMENT_ENABLED


def _agreement():
    row, _ = MembershipAgreement.objects.get_or_create(pk=1)
    return row


def create_draft(*, label, effective_date, content):
    """Store `content` (bytes) as a new draft version. Returns the version."""
    label = (label or "").strip()
    if not LABEL_RE.fullmatch(label):
        raise AgreementError("The version label is 1 to 32 letters, digits, dots, dashes or underscores.")
    if not isinstance(effective_date, date):
        raise AgreementError("Enter the effective date as YYYY-MM-DD.")
    if MembershipAgreementVersion.objects.filter(agreement_id=1, label=label).exists():
        raise AgreementError("This version label already exists. Use a new one.")
    upload = SimpleUploadedFile("agreement.pdf", content, content_type=PDF)
    try:
        data, content_type = private_files.check(upload, allowed=(PDF,), max_bytes=MAX_BYTES)
    except private_files.FileRefused as refused:
        raise AgreementError(
            "The file is over 20 MB." if refused.reason == private_files.TOO_LARGE
            else "The file is not a PDF."
        ) from None
    key = private_files.save(FOLDER, data, content_type)
    try:
        with transaction.atomic():
            return MembershipAgreementVersion.objects.create(
                agreement=_agreement(), label=label, effective_date=effective_date,
                sha256=hashlib.sha256(data).hexdigest(), size=len(data), file_key=key,
            )
    except IntegrityError:  # another draft took the label after the check
        private_files.discard(key)
        raise AgreementError("This version label already exists. Use a new one.") from None
    except Exception:
        private_files.discard(key)
        raise


def read_checked(version):
    """The version's bytes if the stored file still matches its SHA-256, else None."""
    try:
        data = private_files.read(version.file_key)
    except Exception:  # noqa: BLE001 - a missing or unreachable object reads as unavailable
        logger.warning("Membership agreement file unreadable (version %s).", version.pk)
        return None
    if hashlib.sha256(data).hexdigest() != version.sha256:
        logger.error("Membership agreement checksum mismatch (version %s).", version.pk)
        return None
    return data


def publish(label, *, reason):
    """Make the draft `label` the active version. The reason (who authorized it and where it is
    filed) is kept in the operator audit log. Returns the version."""
    reason = (reason or "").strip()
    if not reason:
        raise AgreementError("Record who authorized this and where it is filed (--reason).")
    if len(reason) > MAX_REASON:
        raise AgreementError(f"The reason is over {MAX_REASON} characters.")
    with transaction.atomic():
        agreement = MembershipAgreement.objects.select_for_update().get_or_create(pk=1)[0]
        version = (
            MembershipAgreementVersion.objects.select_for_update()
            .filter(agreement=agreement, label=(label or "").strip()).first()
        )
        if version is None:
            raise AgreementError("There is no such version.")
        if version.state != MembershipAgreementVersion.DRAFT:
            raise AgreementError("This version is already published.")
        if read_checked(version) is None:
            raise AgreementError("The stored file could not be verified. Create the draft again.")
        now = timezone.now()
        version.state, version.published_at = MembershipAgreementVersion.PUBLISHED, now
        version.save(update_fields=["state", "published_at"])
        previous = agreement.active_version_id
        agreement.active_version = version
        agreement.save(update_fields=["active_version", "updated_at"])
        audit.record(
            None, "sante_agreement_published", target_type=TARGET, target_id=version.pk, reason=reason,
            detail={"label": version.label, "sha256": version.sha256,
                    "previous_version_id": previous},
        )
    return version


def active_version():
    """The published version buyers see, or None."""
    row = MembershipAgreement.objects.select_related("active_version").filter(pk=1).first()
    return row.active_version if row else None


def published_bytes(version_id):
    """(version, bytes) of a published version, or None. A draft never leaves through here."""
    version = MembershipAgreementVersion.objects.filter(
        pk=version_id, state=MembershipAgreementVersion.PUBLISHED
    ).first()
    data = read_checked(version) if version else None
    return (version, data) if data is not None else None
