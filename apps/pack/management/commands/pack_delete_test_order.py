"""Delete NAMED test Pack orders, and their member when nothing else uses it (ClickUp z8uq9mfztm).

    manage.py pack_delete_test_order --site SITE --order SMP00001 [--order SMP00002 ...]   (dry run)
    manage.py pack_delete_test_order --site SITE --order SMP00001 --apply
    ... --actor USERNAME     (optional: the staff user, recorded on the audit entry)

SITE is the site's public id; each ORDER is a Pack order's order number or public id. Unlike
`pack_remove_test_member` (which only blanks), this removes the rows. STAGING AND LOCAL ONLY: it
refuses, writing nothing, unless DEBUG is on or Railway names the environment "staging", and it
refuses on a Railway environment named "production" whatever DEBUG says.

It refuses all of the orders, writing nothing, when the site is not found, any named order is not
found, or any is on another site. No bulk mode, no "all". Without --apply it runs the whole
deletion inside a transaction, prints the counts per table and rolls back. With --apply it
commits, in one transaction, with one `pack_test_order_deleted` audit entry (ids and counts
only; audit entries are never deleted).

Deletion order is PROTECT-safe. For the named orders: proof decisions, proof submissions, order
events, agreement acceptances, enrollment authorizations, Sante registration events, registered
notices and registrations, checkout tokens, Pack visits, Meta order contexts, then the orders.
Then each order's member, unless something else still uses it (another Pack order, a registration
of another order, a shop order as buyer or credited member): such a member is kept and said so.
A member that goes takes its sessions, sign-in codes, recovery checks, contact audits and the
security holds on them, address events, order logs, notifications, buyer notices and lesson
progress first.

The append-only tables refuse every delete through their model and their default manager. This
command is the one sanctioned exception, through the plain base manager (`_gone`), as the
retention code does; nothing else was loosened. Meta delivery rows are kept: they hold no foreign
key and no personal data, and are only counted. Proof files are deleted from the private store
after the commit; a file that fails is logged by row id only, never by key. Run again, the orders
are gone, so it refuses with "No such order".
"""
import json
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.accounts.management.commands import _shared
from apps.common import private_files
from apps.members.models import (
    Member, MemberAddressEvent, MemberContactAudit, MemberNotification, MemberOrderLog,
    MemberOtpChallenge, MemberRecoveryCheck, MemberSecurityHold, MemberSession,
)
from apps.meta_ads.models import MetaDelivery, MetaOrderContext
from apps.operators import audit
from apps.pack.models import (
    CheckoutToken, PackAgreementAcceptance, PackBuyerNotice, PackEnrollmentAuthorization, PackOrder,
    PackOrderEvent, PackProofDecision, PackProofSubmission, PackRegisteredNotice, PackVisit,
    SanteRegistration, SanteRegistrationEvent,
)
from apps.shop.models import ShopOrder
from apps.sites.models import Site
from apps.training.models import LessonProgress

logger = logging.getLogger(__name__)


def environment_allows():
    """True on a local run (DEBUG) or on the Railway environment named "staging". Railway sets
    RAILWAY_ENVIRONMENT_NAME itself (no variable change). Unknown or "production" refuses."""
    name = (getattr(settings, "RAILWAY_ENVIRONMENT_NAME", "") or "").strip().lower()
    if name == "production":
        return False
    return bool(settings.DEBUG) or name == "staging"


def _gone(queryset):
    """Delete the rows of a queryset through the plain base manager and report how many. The
    append-only models refuse this through `objects`; this command is the only caller allowed."""
    model = queryset.model
    scoped = model._base_manager.filter(pk__in=queryset.values("pk"))
    count = scoped.count()
    scoped.delete()
    return count


def _delete_files(files):
    """Each proof file from the private store, after the commit. Never logs a key."""
    for pk, key in files:
        try:
            private_files.delete(key)
        except private_files.StoreError:
            logger.error("Test order deletion: a proof file could not be deleted (row %s).", pk)


class Command(BaseCommand):
    help = "Delete named TEST Pack orders of one site, staging and local only. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--site", required=True, help="the site's public id (site_...)")
        parser.add_argument(
            "--order", action="append", required=True, dest="orders",
            help="a Pack order's number or public id; repeat for more than one",
        )
        parser.add_argument("--apply", action="store_true", help="write; without it, a dry run")
        parser.add_argument("--actor", default="", help="the staff user, for the audit record")

    def handle(self, *args, site, orders, apply, actor="", **options):
        if not environment_allows():
            raise CommandError("This command runs on staging and local only. Nothing was written.")
        operator = _shared.staff_user(actor) if actor else None
        with transaction.atomic():
            found_site = Site.objects.select_for_update().filter(public_id=site).first()
            if found_site is None:
                raise CommandError("No such site. Nothing was written.")
            found = self._resolve(found_site, orders)
            counts, kept, deleted_members, files = self._delete(found_site, found)
            if apply:
                audit.record(
                    operator, "pack_test_order_deleted", target_type="site",
                    target_id=found_site.public_id,
                    detail={
                        "orders": [o.public_id for o in found],
                        "members_deleted": deleted_members,
                        "members_kept": kept,
                        "counts": counts,
                    },
                )
                transaction.on_commit(lambda: _delete_files(files))
            else:
                transaction.set_rollback(True)
        mode = "Applied" if apply else "Dry run (nothing written)"
        self.stdout.write(f"{mode}: site {found_site.public_id}, orders {', '.join(o.public_id for o in found)}")
        for public_id in kept:
            self.stdout.write(f"Member {public_id} is kept: something else still uses it.")
        self.stdout.write(json.dumps(counts, indent=2, sort_keys=True))

    def _resolve(self, site, references):
        """The named orders, each on this site, or a refusal of all of them."""
        found, missing, elsewhere = [], [], []
        for reference in dict.fromkeys(r.strip() for r in references):
            match = Q(public_id=reference) | Q(order_number=reference)
            order = PackOrder.objects.select_for_update().filter(match, site=site).first()
            if order is not None:
                if order not in found:
                    found.append(order)
            elif PackOrder.objects.filter(match).exclude(site=site).exists():
                elsewhere.append(reference)
            else:
                missing.append(reference)
        if elsewhere:
            raise CommandError("An order is not on that site. Nothing was written.")
        if missing:
            raise CommandError("No such order. Nothing was written.")
        return found

    def _delete(self, site, orders):
        order_ids = [o.pk for o in orders]
        by_order = {"order_id__in": order_ids}
        counts = {"pack_orders": len(order_ids)}
        proofs = PackProofSubmission._base_manager.filter(**by_order)
        files = list(proofs.exclude(file_key="").values_list("pk", "file_key"))
        counts["proof_files_to_delete"] = len(files)
        # What can still point at an order or its member goes first, leaf to root.
        registrations = SanteRegistration.objects.filter(pack_order_id__in=order_ids)
        steps = [
            ("pack_proof_decisions", PackProofDecision._base_manager.filter(**by_order)),
            ("pack_proof_submissions", proofs),
            ("pack_order_events", PackOrderEvent._base_manager.filter(**by_order)),
            ("pack_agreement_acceptances", PackAgreementAcceptance._base_manager.filter(**by_order)),
            ("pack_enrollment_authorizations", PackEnrollmentAuthorization._base_manager.filter(**by_order)),
            (
                "sante_registration_events",
                SanteRegistrationEvent._base_manager.filter(registration__in=registrations),
            ),
            ("pack_registered_notices", PackRegisteredNotice.objects.filter(registration__in=registrations)),
            ("sante_registrations", registrations),
            ("checkout_tokens", CheckoutToken.objects.filter(site=site, order_ref__in=order_ids)),
            ("pack_visits", PackVisit.objects.filter(order_id__in=order_ids)),
            ("meta_order_contexts", MetaOrderContext.objects.filter(order_id__in=order_ids)),
        ]
        for name, queryset in steps:
            counts[name] = _gone(queryset)
        # Meta deliveries stay (no foreign key, no personal data); their event id ends in the
        # public id of the order. Counted before the orders go.
        counts["meta_delivery_rows_kept"] = sum(
            MetaDelivery.objects.filter(site=site, event_id__endswith=f"_{o.public_id}").count()
            for o in orders
        )
        member_ids = list(dict.fromkeys(o.member_id for o in orders))
        _gone(PackOrder.objects.filter(pk__in=order_ids))
        kept, deleted = [], []
        counts["members_deleted"] = 0
        for member in Member.objects.select_for_update().filter(pk__in=member_ids).order_by("pk"):
            if self._in_use(member):
                kept.append(member.public_id)
                continue
            deleted.append(member.public_id)
            counts["members_deleted"] += 1
            for name, count in self._delete_member(member).items():
                counts[name] = counts.get(name, 0) + count
        counts["members_kept"] = len(kept)
        return counts, kept, deleted, files

    @staticmethod
    def _in_use(member):
        return (
            PackOrder.objects.filter(member=member).exists()
            or SanteRegistration.objects.filter(member=member).exists()
            or ShopOrder.objects.filter(Q(member=member) | Q(credited_member=member)).exists()
        )

    @staticmethod
    def _delete_member(member):
        by_member = {"member": member}
        audits = MemberContactAudit.objects.filter(**by_member)
        counts = {}
        for name, queryset in [
            ("member_security_holds", MemberSecurityHold.objects.filter(Q(member=member) | Q(audit__in=audits))),
            ("member_contact_audits", audits),
            ("member_recovery_checks", MemberRecoveryCheck.objects.filter(**by_member)),
            ("member_sessions", MemberSession.objects.filter(**by_member)),
            ("member_otp_challenges", MemberOtpChallenge.objects.filter(**by_member)),
            ("member_notifications", MemberNotification.objects.filter(**by_member)),
            ("member_order_logs", MemberOrderLog.objects.filter(**by_member)),
            ("member_address_events", MemberAddressEvent.objects.filter(**by_member)),
            ("pack_buyer_notices", PackBuyerNotice.objects.filter(**by_member)),
            ("lesson_progress", LessonProgress.objects.filter(**by_member)),
        ]:
            counts[name] = _gone(queryset)
        _gone(Member.objects.filter(pk=member.pk))
        return counts
