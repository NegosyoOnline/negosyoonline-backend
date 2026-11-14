"""Empty ONE test member on ONE site of personal data (ClickUp z8uq9mewx1; Gate 2 test-data cleanup).

    manage.py pack_remove_test_member --site SITE --member MEMBER            (dry run)
    manage.py pack_remove_test_member --site SITE --member MEMBER --apply
    ... --actor USERNAME     (optional: the staff user, recorded on the audit entry)

SITE and MEMBER are public ids. It reuses the blanking of the day-90 purge (`apps.pack.purge`) and
of member disposal (`apps.members.retention`), limited to that one member. The rows stay (members,
orders, proofs, decisions and events, registrations, Meta delivery rows); what identifies the
person goes. It refuses, writing nothing, when the site or member is not found, the member is not
on that site, the full name does not start with "TEST ", or one of the member's Pack orders is on
another site. Without --apply it prints the counts and writes nothing. With it, everything is
written in one transaction with one `pack_test_member_removed` audit entry (ids and counts only);
the member's order-log rows (`member_order_logs`, apps/members/order_log.py) are deleted and counted; the proof files are deleted from the private store only after the commit. Running it again changes
nothing. No bulk mode.
"""
import json
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.management.commands import _shared
from apps.common import private_files
from apps.members import retention as member_retention
from apps.members.models import Member
from apps.meta_ads.models import MetaDelivery, MetaOrderContext
from apps.operators import audit
from apps.pack import purge
from apps.pack.models import PackOrder, PackProofSubmission
from apps.sites.models import Site

logger = logging.getLogger(__name__)

TEST_PREFIX = "TEST "


def _delete_files(proof_ids):
    """Each proof file, then its key; a key stays until its file is gone. Never logs a key."""
    for pk, key in list(
        PackProofSubmission._base_manager.filter(pk__in=proof_ids).exclude(file_key="")
        .values_list("pk", "file_key")
    ):
        try:
            private_files.delete(key)
        except private_files.StoreError:
            logger.error("Test member removal: a proof file could not be deleted (row %s).", pk)
            continue
        PackProofSubmission._base_manager.filter(pk=pk).update(file_key="")


class Command(BaseCommand):
    help = "Remove the personal data of one TEST member of one site. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--site", required=True, help="the site's public id (site_...)")
        parser.add_argument("--member", required=True, help="the member's public id")
        parser.add_argument("--apply", action="store_true", help="write; without it, a dry run")
        parser.add_argument("--actor", default="", help="the staff user, for the audit record")

    def handle(self, *args, site, member, apply, actor="", **options):
        operator = _shared.staff_user(actor) if actor else None
        with transaction.atomic():
            found_site = Site.objects.select_for_update().filter(public_id=site).first()
            if found_site is None:
                raise CommandError("No such site. Nothing was written.")
            found = Member.objects.select_for_update().filter(public_id=member).first()
            if found is None:
                raise CommandError("No such member. Nothing was written.")
            if found.site_id != found_site.pk:
                raise CommandError("That member is not on that site. Nothing was written.")
            already = found.full_name == ""
            if not already and not found.full_name.startswith(TEST_PREFIX):
                raise CommandError('The member\'s name does not start with "TEST ". Nothing was written.')
            orders = PackOrder.objects.filter(member=found)
            if orders.exclude(site=found_site).exists():
                raise CommandError("A Pack order of that member is not on this site. Nothing was written.")
            if already:
                self.stdout.write("That member was already emptied. Nothing was changed.")
                return
            order_ids = list(orders.values_list("pk", flat=True))
            proof_ids = list(
                PackProofSubmission._base_manager.filter(order_id__in=order_ids).values_list("pk", flat=True)
            )
            contexts = MetaOrderContext.objects.filter(order_id__in=order_ids)
            deliveries_kept = MetaDelivery.objects.filter(site=found_site).count()
            files = PackProofSubmission._base_manager.filter(pk__in=proof_ids).exclude(file_key="").count()

            counts = {"proof_files_to_delete": files, "meta_delivery_rows_kept": deliveries_kept}
            # The blanking itself; a dry run counts without writing, then rolls back.
            counts.update(purge._clear_details(found_site.pk, timezone.now(), member_id=found.pk))
            counts["meta_order_contexts"] = contexts.count()
            contexts.delete()
            counts.update(member_retention.blank_members(
                Site.objects.filter(pk=found_site.pk), ignore_switch=True, only_member=found,
            ))
            if apply:
                audit.record(
                    operator, "pack_test_member_removed", target_type="site",
                    target_id=found_site.public_id, detail={"member": found.public_id, "counts": counts},
                )
                transaction.on_commit(lambda: _delete_files(proof_ids))
            else:
                transaction.set_rollback(True)
        mode = "Applied" if apply else "Dry run (nothing written)"
        self.stdout.write(f"{mode}: site {found_site.public_id}, member {found.public_id}")
        self.stdout.write(json.dumps(counts, indent=2, sort_keys=True))
