"""The central Membership Agreement (Coach Ed 2026-10-31), ported from TWCinc
negosyoonline_sante/agreements.py. One pointer, MembershipAgreement.active_version, is the whole
distribution: every Santé site reads it, nothing is copied. Only the Santé catalog owner site
is the writer, and only an operator acts for it (a command; there is no Subscriber path and no
admin screen). Publishing needs Santé's written authorization, recorded and verified first.

The PDF stays in the private store under a new key per upload and is checked against its SHA-256
on every read. Behind SANTE_AGREEMENT_ENABLED.
Lock order: the catalog owner row, then its Site row, then the MembershipAgreement row, then a
version or authorization row.
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
from apps.sante.models import (
    AgreementPublishAuthorization, MembershipAgreement, MembershipAgreementVersion,
)
from apps.sites.models import Site

logger = logging.getLogger(__name__)

FOLDER = "sante/agreement"
EVIDENCE_FOLDER = "sante/agreement-evidence"
MAX_BYTES = 20 * 1024 * 1024
PDF = "application/pdf"
EVIDENCE_TYPES = (PDF, "image/jpeg", "image/png")
TARGET = "sante_agreement_version"
AUTH_TARGET = "sante_agreement_authorization"

NOT_OPERATOR = "Only an active staff operator can manage the Membership Agreement."
NO_OWNER = "Set the Santé catalog owner before managing the Membership Agreement."
BILLING_ONLY = (
    "The catalog owner site cannot change the Membership Agreement while it is suspended, "
    "cancelled or deactivated."
)
NOT_AUTHORIZED = "Publishing is locked until the Santé authorization is recorded and verified."
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


def publishing_authorized():
    return AgreementPublishAuthorization.objects.filter(
        agreement_id=1, verified_at__isnull=False, revoked_at__isnull=True
    ).exists()


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


def publish(operator, version_label):
    """The only writer of the pointer. There is no flag or route around the authorization."""
    _snapshot_operator(operator)
    with transaction.atomic():
        _writer_site(lock=True)
        row = MembershipAgreement.objects.select_for_update().get_or_create(pk=1)[0]
        # Read under a row lock, so a revoke and a publish serialize: one waits for the other.
        authorization = (
            AgreementPublishAuthorization.objects.select_for_update()
            .filter(agreement_id=1, verified_at__isnull=False, revoked_at__isnull=True).first()
        )
        if authorization is None:
            raise AgreementError(NOT_AUTHORIZED)
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
            detail={"version_label": version.version_label, "sha256": version.sha256,
                    "previous_version_id": row.active_version_id,
                    "authorization_id": authorization.pk},
        )
    version.refresh_from_db()
    return version


# ---- Santé's written authorization (evidence kept in the private store) ----

def record_authorization(operator, *, content, document_date, note, covers_pdf_hosting,
                         covers_branding, covers_online_acceptance):
    """The evidence goes to the private store under a new key; publishing stays locked until
    verify_authorization runs."""
    _snapshot_operator(operator)
    note = (note or "").strip()
    if not note:
        raise AgreementError("Record what authorizes publishing, such as the letter's reference.")
    if len(note) > 255:
        raise AgreementError("The note is over 255 characters.")
    if not isinstance(document_date, date):
        raise AgreementError("Enter the document date as YYYY-MM-DD.")
    data, content_type = _check_file(content, EVIDENCE_TYPES, "evidence")
    key = private_files.save(EVIDENCE_FOLDER, data, content_type)
    try:
        with transaction.atomic():
            row = AgreementPublishAuthorization.objects.create(
                agreement=_agreement(), document_date=document_date, note=note,
                covers_pdf_hosting=covers_pdf_hosting, covers_branding=covers_branding,
                covers_online_acceptance=covers_online_acceptance, evidence_key=key,
                evidence_sha256=hashlib.sha256(data).hexdigest(), evidence_size=len(data),
                recorded_by=operator,
            )
            audit.record(operator, "sante_agreement_authorization_recorded",
                         target_type=AUTH_TARGET, target_id=row.pk, reason=note)
    except Exception:
        private_files.discard(key)
        raise
    return row


def evidence_bytes(row):
    """The evidence file if it still matches its checksum, else None."""
    try:
        data = private_files.read(row.evidence_key)
    except Exception:  # noqa: BLE001
        logger.warning("Membership agreement evidence unreadable (authorization %s).", row.pk)
        return None
    return data if hashlib.sha256(data).hexdigest() == row.evidence_sha256 else None


def _cannot_verify(row):
    if row.verified_at is not None or row.revoked_at is not None:
        return "This authorization has already been decided."
    if not (row.covers_pdf_hosting and row.covers_branding and row.covers_online_acceptance):
        return "It must cover PDF hosting, Santé branding and online acceptance."
    if evidence_bytes(row) is None:
        return "The stored evidence could not be verified."
    return ""


def verify_authorization(operator, pk):
    _snapshot_operator(operator)
    with transaction.atomic():
        row = AgreementPublishAuthorization.objects.select_for_update().filter(pk=pk).first()
        if row is None:
            raise AgreementError("There is no such authorization.")
        reason = _cannot_verify(row)
        if reason:
            raise AgreementError(reason)
        AgreementPublishAuthorization.objects.filter(pk=pk, verified_at__isnull=True).update(
            verified_at=timezone.now(), verified_by=operator
        )
        audit.record(operator, "sante_agreement_authorization_verified",
                     target_type=AUTH_TARGET, target_id=pk)


def revoke_authorization(operator, pk, *, reason):
    """active_version then answers None, which closes membership checkout. Published history
    stays."""
    _snapshot_operator(operator)
    reason = (reason or "").strip()
    if not reason:
        raise AgreementError("Record why this authorization ended.")
    if len(reason) > 255:
        raise AgreementError("The reason is over 255 characters.")
    with transaction.atomic():
        row = AgreementPublishAuthorization.objects.select_for_update().filter(pk=pk).first()
        if row is None:
            raise AgreementError("There is no such authorization.")
        if row.revoked_at is not None:
            raise AgreementError("This authorization has already ended.")
        AgreementPublishAuthorization.objects.filter(pk=pk, revoked_at__isnull=True).update(
            revoked_at=timezone.now(), revoked_by=operator, revoked_reason=reason
        )
        audit.record(operator, "sante_agreement_authorization_revoked",
                     target_type=AUTH_TARGET, target_id=pk, reason=reason)


# ---- what buyers read ----

def active_version():
    """The one published version Santé sites use, or None. A withdrawn or missing authorization
    answers None, which closes membership checkout through this one line."""
    if not publishing_authorized():
        return None
    row = MembershipAgreement.objects.select_related("active_version").filter(pk=1).first()
    return row.active_version if row else None


def published_bytes(version_id):
    """(version, bytes) of a published version, or None. A draft never leaves through here.
    Older published versions stay readable after a newer one. Once the authorization is withdrawn
    nothing is served (Coach Ed 2026-10-31): the file and rows are kept as evidence."""
    if not publishing_authorized():
        return None
    version = MembershipAgreementVersion.objects.filter(
        pk=version_id, state=MembershipAgreementVersion.PUBLISHED
    ).first()
    data = read_checked(version) if version else None
    return (version, data) if data is not None else None


def status():
    """Plain lines for the operator: versions and authorizations. No file contents."""
    row = _agreement()
    lines = [f"Authorized to publish: {'yes' if publishing_authorized() else 'no'}"]
    for v in row.versions.all():
        lines.append(
            f"version {v.version_label} [{v.state}]{' ACTIVE' if v.pk == row.active_version_id else ''}"
            f" effective {v.effective_date} sha256 {v.sha256}"
        )
    for a in row.authorizations.all():
        stage = "revoked" if a.revoked_at else ("verified" if a.verified_at else "recorded")
        lines.append(f"authorization {a.pk} [{stage}] document {a.document_date}: {a.note}")
    return lines
