"""The Pack files of one ended site, as one ZIP for the Subscriber (ClickUp z8uq9mbu0p, plan
2026-11-13-export-pack-proofs, branch A).

Support-run only (`manage.py export_pack_files`); nothing here is a route. The archive holds the
Subscriber's own Pack rows as plain values and the proof images as files, with a manifest of sha256
values computed on the bytes read from the private store. It never holds a file key, a token, a
hash other than sha256, a visitor address, a row number, `actor_ref` or a full account number.

Inside the 30-day post-service window (`window_error`) the command runs on its own. A person's own
access or deletion request on a site that is still served runs with `basis="request"`, which skips
the window and nothing else (D3; the one place that differs is `window_error`).
"""
import hashlib
import json
import os
import tempfile
import zipfile
from contextlib import contextmanager


from apps.billing import plan
from apps.billing import services as billing
from apps.common import private_files
from apps.pack import portal
from apps.pack import retention as pack_retention
from apps.pack.models import (
    PackAgreementAcceptance, PackEnrollmentAuthorization, PackOrder, PackOrderEvent,
    PackProofDecision, PackProofSubmission,
)
from apps.sites.models import Site

MAX_ARCHIVE_BYTES = 200 * 1024 * 1024  # D4: above this the export is refused; no parts option
FORMAT = 1
WINDOW, REQUEST = "window", "request"
BASES = (WINDOW, REQUEST)
_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)  # fixed, so the same files make the same bytes


class ExportRefused(Exception):
    """The export may not run, or may not finish. The message is for the operator."""


def window_error(site, now, basis=WINDOW):
    """Why `site` is not exportable at `now`, or "" when it is. The window is the 30 days from the
    end of service, and closes no later than the deletion point (a site that never paid has its
    deletion point exactly at the end of the 30 days). `basis="request"` has no window."""
    if basis == REQUEST:
        return ""
    found = billing.end_dates(site, now)
    if found is None:
        return "This site is not in its export window (its service has not ended)."
    service_end, deletion_due = found
    closes = service_end + plan.DOWNLOAD_WINDOW
    if now < service_end or now > closes or (deletion_due is not None and now >= deletion_due):
        return "This site is outside its 30-day export window."
    return ""


def _iso(moment):
    return moment.isoformat() if moment else None


def _window_block(site, now):
    found = billing.end_dates(site, now)
    if found is None:
        return None
    service_end = found[0]
    closes = service_end + plan.DOWNLOAD_WINDOW
    return {
        "service_ended_at": _iso(service_end), "window_closes_at": _iso(closes),
        "service_ended_at_manila": portal.manila(service_end).isoformat(),
        "window_closes_at_manila": portal.manila(closes).isoformat(),
    }


def _pack_json(site, proofs_info):
    """The Subscriber's Pack rows for this one site; `proofs_info` maps a proof public id to
    (entry name or None, sha256 or "", reason)."""
    sites = Site.objects.filter(pk=site.pk)
    base = pack_retention.export_packs(sites)
    proofs = []
    for item in base[pack_retention.PROOFS]:
        entry, digest, reason = proofs_info[item["id"]]
        extra = {"file": entry, "sha256": digest}
        if entry is None:
            extra["no_file_reason"] = reason
        proofs.append({**item, **extra})
    order_ids = dict(PackOrder.objects.filter(site=site).values_list("pk", "public_id"))
    proof_ids = dict(PackProofSubmission.objects.filter(site=site).values_list("pk", "public_id"))
    return {
        "site": site.public_id,
        "registrations": base[pack_retention.REGISTRATIONS],
        "orders": base[pack_retention.ORDERS],
        "proofs": proofs,
        "decisions": [
            {
                "proof": proof_ids[row.submission_id], "order": order_ids[row.order_id],
                "decision": row.decision, "reason_code": row.reason_code,
                "other_text": row.other_text, "actor_type": row.actor_type,
                "decided_at": _iso(row.decided_at),
            }
            for row in PackProofDecision.objects.filter(site=site).order_by("pk")
        ],
        "order_events": [
            {
                "order": order_ids[row.order_id], "kind": row.kind, "actor_type": row.actor_type,
                "at": _iso(row.created_at),
            }
            for row in PackOrderEvent.objects.filter(site=site).order_by("pk")
        ],
        "agreement_acceptances": [
            {
                "order": order_ids[row.order_id], "agreement_version": row.version.version_label,
                "agreement_title": row.version.title, "sha256": row.sha256,
                "accepted_at": _iso(row.accepted_at),
            }
            for row in PackAgreementAcceptance.objects.filter(site=site)
            .select_related("version").order_by("pk")
        ],
        "enrollment_authorizations": [
            {
                "order": order_ids[row.order_id], "text_version": row.text_version,
                "text": row.text, "accepted_at": _iso(row.accepted_at),
            }
            for row in PackEnrollmentAuthorization.objects.filter(site=site).order_by("pk")
        ],
    }


def _entry(archive, name, data, *, deflate):
    info = zipfile.ZipInfo(name, _ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED
    info.external_attr = 0o600 << 16
    archive.writestr(info, data)


def _json_bytes(value):
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"


@contextmanager
def build_site_archive(site, now, *, basis=WINDOW):
    """Build the ZIP in a private temporary file and yield `(path, summary)`; the file is removed
    on exit. Raises ExportRefused (window, size) or private_files.StoreError (a file could not be
    read: nothing is yielded, so no half export is ever handed over). One read per image."""
    handle, path = tempfile.mkstemp(prefix="pack-export-", suffix=".zip")  # mode 0600
    os.close(handle)
    try:
        summary = _fill(path, site, now, basis)
        yield path, summary
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _fill(path, site, now, basis):
    root = f"pack-export-{site.public_id}"
    proofs = list(
        PackProofSubmission.objects.filter(site=site).select_related("order").order_by("order__pk", "seq")
    )
    info, files, no_file, warnings, total = {}, [], [], [], 0
    with zipfile.ZipFile(path, "w") as archive:
        for proof in proofs:
            if not proof.file_key:
                info[proof.public_id] = (None, "", "key_empty")
                no_file.append({"proof": proof.public_id, "reason": "key_empty"})
                continue
            content = private_files.read(proof.file_key)  # StoreError ends the whole export
            total += len(content)
            if total > MAX_ARCHIVE_BYTES:
                raise ExportRefused(
                    "The archive would be larger than the 200 MiB limit. Nothing was written. "
                    "Tell the Supervisor: a part-by-part export is not built."
                )
            ext = _EXT.get(proof.content_type) or proof.file_key.rsplit(".", 1)[-1]
            name = f"proofs/{proof.order.order_number}-{proof.seq}.{ext}"
            digest = hashlib.sha256(content).hexdigest()
            _entry(archive, f"{root}/{name}", content, deflate=False)
            info[proof.public_id] = (name, digest, "")
            files.append({
                "file": name, "proof": proof.public_id, "order": proof.order.order_number,
                "content_type": proof.content_type, "bytes": len(content), "sha256": digest,
            })
            if proof.size != len(content):
                warnings.append({
                    "proof": proof.public_id, "warning": "stored_size_differs",
                    "stored_size": proof.size, "bytes_read": len(content),
                })
        pack = _json_bytes(_pack_json(site, info))
        _entry(archive, f"{root}/pack.json", pack, deflate=True)
        counts = {
            "orders": len(_orders_of(site)), "proofs": len(proofs), "files": len(files),
            "bytes": total,
        }
        manifest = {
            "format": FORMAT, "site": site.public_id, "basis": basis, "exported_at": now.isoformat(),
            "window": _window_block(site, now),
            "pack_json": {"file": "pack.json", "sha256": hashlib.sha256(pack).hexdigest()},
            "files": files, "no_file": no_file, "counts": counts, "warnings": warnings,
        }
        _entry(archive, f"{root}/manifest.json", _json_bytes(manifest), deflate=True)
    if os.path.getsize(path) > MAX_ARCHIVE_BYTES:
        raise ExportRefused("The archive is larger than the 200 MiB limit. Nothing was written.")
    sha = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            sha.update(block)
    return {
        "counts": counts, "bytes": total, "archive_bytes": os.path.getsize(path),
        "archive_sha256": sha.hexdigest(), "warnings": len(warnings), "basis": basis,
    }


def _orders_of(site):
    return list(PackOrder.objects.filter(site=site).values_list("pk", flat=True))

__all__ = ["ExportRefused", "build_site_archive", "window_error", "MAX_ARCHIVE_BYTES", "BASES"]
