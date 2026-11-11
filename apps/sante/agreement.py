"""The central Membership Agreement (Coach Ed 2026-10-31), ported from TWCinc
negosyoonline_sante/agreements.py (without TWCinc's Santé authorization letter: Coach Ed ruled
2026-11-11 it is not needed). One pointer, MembershipAgreement.active_version, is the whole
distribution: every Santé site reads it, nothing is copied. Only the Santé catalog owner site
is the writer, and only an operator acts for it (a command; there is no Subscriber path and no
admin screen). Publishing needs a draft version and an operator.

The PDF stays in the private store under a new key per upload and is checked against its SHA-256
on every read. Behind SANTE_AGREEMENT_ENABLED.
Lock order: the catalog owner row, then its Site row, then the MembershipAgreement row, then a
version row.
"""
import hashlib
import logging
from datetime import date

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing import services as billing
from apps.common import private_files
from apps.operators import audit
from apps.sante import services as sante
from apps.sante.models import MembershipAgreement, MembershipAgreementVersion
from apps.sites.models import Site

logger = logging.getLogger(__name__)

FOLDER = "sante/agreement"
MAX_BYTES = 20 * 1024 * 1024
MAX_REASON = 500
PDF = "application/pdf"
TARGET = "sante_agreement_version"

NOT_OPERATOR = "Only an active staff operator can manage the Membership Agreement."
NO_OWNER = "Set the Santé catalog owner before managing the Membership Agreement."
BILLING_ONLY = (
    "The catalog owner site cannot change the Membership Agreement while it is suspended, "
    "cancelled or deactivated."
)
NOT_FOUND = "This agreement version was not found."
TAKEN = "This version already exists. Use a new version."


class AgreementError(Exception):
    """A refusal the operator can read. The message never holds file contents."""


def enabled():
    return settings.SANTE_AGREEMENT_ENABLED


def _agreement():
    row, _ = MembershipAgreement.objects.get_or_create(pk=1)
    return row


def _snapshot_operator(operator):
    if operator is None or not (operator.is_active and operator.is_staff):
        raise AgreementError(NOT_OPERATOR)
    return operator


def _writer_site(*, lock=False):
    """The catalog owner site, resolved here and never chosen by the caller. With lock, inside a
    transaction, the owner row and then the site row are held and the site is checked again."""
    if lock:
        sante.SanteCatalogOwner.objects.get_or_create(pk=sante.OWNER_PK)
        sante.SanteCatalogOwner.objects.select_for_update().get(pk=sante.OWNER_PK)
    site = sante.catalog_owner_site()
    if site is None:
        raise AgreementError(NO_OWNER)
    if lock:
        site = Site.objects.select_for_update().get(pk=site.pk)
    if not sante.is_catalog_writer(site):
        raise AgreementError(NO_OWNER)
    if billing.position(site).billing_only:
        raise AgreementError(BILLING_ONLY)
    return site


def _clean_meta(title, version_label, effective_date):
    title, version_label = (title or "").strip(), (version_label or "").strip()
    if not 1 <= len(title) <= 160:
        raise AgreementError("Enter the agreement title, up to 160 characters.")
    if not 1 <= len(version_label) <= 32:
        raise AgreementError("Enter the version, up to 32 characters.")
    if not isinstance(effective_date, date):
        raise AgreementError("Enter the effective date as YYYY-MM-DD.")
    return title, version_label, effective_date


def _label_taken(label, exclude=None):
    rows = MembershipAgreementVersion.objects.filter(agreement_id=1, version_label=label)
    return (rows.exclude(pk=exclude) if exclude is not None else rows).exists()


def _check_file(content, allowed, what):
    """(bytes, content type) of an allowed file. The type is the one the first bytes show."""
    content_type = private_files.sniff(content[:16])
    upload = SimpleUploadedFile("file", content, content_type=content_type or "application/octet-stream")
    try:
        return private_files.check(upload, allowed=allowed, max_bytes=MAX_BYTES)
    except private_files.FileRefused as refused:
        if refused.reason == private_files.TOO_LARGE:
            raise AgreementError("The file is over 20 MB.") from None
        raise AgreementError(f"The {what} is not an allowed file type.") from None


def create_draft(operator, *, title, version_label, effective_date, content):
    _snapshot_operator(operator)
    _writer_site()
    title, version_label, effective_date = _clean_meta(title, version_label, effective_date)
    if _label_taken(version_label):
        raise AgreementError(TAKEN)
    data, content_type = _check_file(content, (PDF,), "agreement")
    key = private_files.save(FOLDER, data, content_type)
    try:
        with transaction.atomic():
            site = _writer_site(lock=True)  # the upload took time: refused here, file discarded below
            created = MembershipAgreementVersion.objects.create(
                agreement=_agreement(), master_site=site, title=title,
                version_label=version_label, effective_date=effective_date, storage_key=key,
                sha256=hashlib.sha256(data).hexdigest(), size=len(data),
            )
            audit.record(operator, "sante_agreement_version_created", target_type=TARGET,
                         target_id=created.pk, detail={"version_label": version_label})
            return created
    except IntegrityError:  # two requests with one label both passed the check
        private_files.discard(key)
        raise AgreementError(TAKEN) from None
    except Exception:
        private_files.discard(key)
        raise


def _locked_draft(version_label):
    version = (
        MembershipAgreementVersion.objects.select_for_update()
        .filter(agreement_id=1, version_label=(version_label or "").strip()).first()
    )
    if version is None:
        raise AgreementError(NOT_FOUND)
    if version.state != MembershipAgreementVersion.DRAFT:
        raise AgreementError("A published version cannot be changed.")
    return version


def update_draft(operator, version_label, *, title, new_version_label, effective_date):
    _snapshot_operator(operator)
    with transaction.atomic():
        _writer_site(lock=True)
        version = _locked_draft(version_label)
        old = {"title": version.title, "version_label": version.version_label,
               "effective_date": str(version.effective_date)}
        version.title, version.version_label, version.effective_date = _clean_meta(
            title, new_version_label, effective_date
        )
        if _label_taken(version.version_label, exclude=version.pk):
            raise AgreementError(TAKEN)
        try:
            with transaction.atomic():
                version.save()
        except IntegrityError:
            raise AgreementError(TAKEN) from None
        audit.record(operator, "sante_agreement_version_updated", target_type=TARGET,
                     target_id=version.pk, detail={"old": old, "new": {
                         "title": version.title, "version_label": version.version_label,
                         "effective_date": str(version.effective_date)}})
    return version


def delete_draft(operator, version_label):
    _snapshot_operator(operator)
    with transaction.atomic():
        _writer_site(lock=True)
        version = _locked_draft(version_label)
        key = version.storage_key
        audit.record(operator, "sante_agreement_version_deleted", target_type=TARGET,
                     target_id=version.pk, detail={"version_label": version.version_label})
        version.delete()
        transaction.on_commit(lambda: private_files.discard(key))


def read_checked(version):
    """The version's bytes if the stored file still matches its SHA-256, else None."""
    try:
        data = private_files.read(version.storage_key)
    except Exception:  # noqa: BLE001 - a missing or unreachable object reads as unavailable
        logger.warning("Membership agreement file unreadable (version %s).", version.pk)
        return None
    if hashlib.sha256(data).hexdigest() != version.sha256:
        logger.error("Membership agreement checksum mismatch (version %s).", version.pk)
        return None
    return data


def publish(operator, version_label, *, reason):
    """The only writer of the pointer. The reason (who authorized it and where it is filed) is
    kept in the audit row."""
    _snapshot_operator(operator)
    reason = (reason or "").strip()
    if not reason:
        raise AgreementError("Record who authorized this and where it is filed (--reason).")
    if len(reason) > MAX_REASON:
        raise AgreementError(f"The reason is over {MAX_REASON} characters.")
    with transaction.atomic():
        _writer_site(lock=True)
        row = MembershipAgreement.objects.select_for_update().get_or_create(pk=1)[0]
        version = _locked_draft(version_label)
        if read_checked(version) is None:
            raise AgreementError("The stored file could not be verified. Create the draft again.")
        now = timezone.now()
        MembershipAgreementVersion.objects.filter(pk=version.pk, state="draft").update(
            state=MembershipAgreementVersion.PUBLISHED, published_at=now
        )
        MembershipAgreement.objects.filter(pk=row.pk).update(
            active_version_id=version.pk, updated_at=now
        )
        audit.record(
            operator, "sante_agreement_published", target_type=TARGET, target_id=version.pk,
            reason=reason,
            detail={"version_label": version.version_label, "sha256": version.sha256,
                    "previous_version_id": row.active_version_id},
        )
    version.refresh_from_db()
    return version


# ---- what buyers read ----

def active_version():
    """The one published version Santé sites use, or None. None closes membership checkout."""
    row = MembershipAgreement.objects.select_related("active_version").filter(pk=1).first()
    return row.active_version if row else None


def published_bytes(version_id):
    """(version, bytes) of a published version, or None. A draft never leaves through here.
    Older published versions stay readable after a newer one."""
    version = MembershipAgreementVersion.objects.filter(
        pk=version_id, state=MembershipAgreementVersion.PUBLISHED
    ).first()
    data = read_checked(version) if version else None
    return (version, data) if data is not None else None


def status():
    """Plain lines for the operator: the versions. No file contents."""
    row = _agreement()
    lines = []
    for v in row.versions.all():
        lines.append(
            f"version {v.version_label} [{v.state}]{' ACTIVE' if v.pk == row.active_version_id else ''}"
            f" effective {v.effective_date} sha256 {v.sha256}"
        )
    return lines
