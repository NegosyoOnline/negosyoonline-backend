"""Delete NAMED test shop orders, and their member when nothing else uses it (ClickUp z8uq9mfzzn).

    manage.py shop_delete_test_order --site SITE --order SO00001 [--order SO00002 ...]   (dry run)
    manage.py shop_delete_test_order --site SITE --order SO00001 --apply
    ... --actor USERNAME     (optional: the staff user, recorded on the audit entry)

SITE is the site's public id; each ORDER is a shop order's number or public id. Unlike
`shop_remove_test_order` (which only blanks), this removes the rows. STAGING AND LOCAL ONLY: it
refuses, writing nothing, unless DEBUG is on or Railway names the environment "staging", and it
refuses on a Railway environment named "production" whatever DEBUG says. The check comes before
any database read.

It refuses all of the orders, writing nothing, when the site is not found, any named order is not
found, or any is on another site. No bulk mode, no "all". Without --apply it runs the whole
deletion inside a transaction, prints the counts per table and rolls back. With --apply it
commits, in one transaction, with one `shop_test_order_deleted` audit entry (ids and counts only;
audit entries are never deleted).

Deletion order is PROTECT-safe. For the named orders: product reviews, proof submissions, order
events, order lines, then the orders. Then each order's member and credited member, unless
something else still uses it (another shop order, a Pack order, a registration): such a member is
kept and said so. A member that goes takes its own rows first, as `pack_delete_test_order` does
(shared in apps/common/test_data_deletion.py). A guest order has no member.

The append-only tables, the shop orders and the reviews refuse every delete through their model
and their default manager. This command is the one sanctioned exception, through the plain base
manager (`gone`, the one named bypass); nothing else was loosened. Proof files are deleted from the
private store after the commit; a file that fails is logged by row id only, never by key. Run
again, the orders are gone, so it refuses with "No such order".
"""
import json
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.accounts.management.commands import _shared
from apps.common import private_files
from apps.common.test_data_deletion import delete_member, environment_allows, gone, member_in_use
from apps.members.models import Member
from apps.operators import audit
from apps.shop.models import ProductReview, ShopOrder, ShopOrderEvent, ShopOrderLine, ShopProofSubmission
from apps.sites.models import Site

logger = logging.getLogger(__name__)


def _delete_files(files):
    """Each proof file from the private store, after the commit. Never logs a key."""
    for pk, key in files:
        try:
            private_files.delete(key)
        except private_files.StoreError:
            logger.error("Test order deletion: a proof file could not be deleted (row %s).", pk)


class Command(BaseCommand):
    help = "Delete named TEST shop orders of one site, staging and local only. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--site", required=True, help="the site's public id (site_...)")
        parser.add_argument(
            "--order", action="append", required=True, dest="orders",
            help="a shop order's number or public id; repeat for more than one",
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
            counts, kept, deleted_members, files = self._delete(found)
            if apply:
                audit.record(
                    operator, "shop_test_order_deleted", target_type="site",
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
            order = ShopOrder.objects.select_for_update().filter(match, site=site).first()
            if order is not None:
                if order not in found:
                    found.append(order)
            elif ShopOrder.objects.filter(match).exclude(site=site).exists():
                elsewhere.append(reference)
            else:
                missing.append(reference)
        if elsewhere:
            raise CommandError("An order is not on that site. Nothing was written.")
        if missing:
            raise CommandError("No such order. Nothing was written.")
        return found

    def _delete(self, orders):
        order_ids = [o.pk for o in orders]
        by_order = {"order_id__in": order_ids}
        counts = {"shop_orders": len(order_ids)}
        proofs = ShopProofSubmission._base_manager.filter(**by_order)
        files = list(proofs.exclude(file_key="").values_list("pk", "file_key"))
        counts["proof_files_to_delete"] = len(files)
        # What can still point at an order goes first, leaf to root: a review points at a line.
        for name, queryset in [
            ("product_reviews", ProductReview._base_manager.filter(**by_order)),
            ("shop_proof_submissions", proofs),
            ("shop_order_events", ShopOrderEvent._base_manager.filter(**by_order)),
            ("shop_order_lines", ShopOrderLine._base_manager.filter(**by_order)),
        ]:
            counts[name] = gone(queryset)
        member_ids = list(dict.fromkeys(
            m for o in orders for m in (o.member_id, o.credited_member_id) if m is not None
        ))
        gone(ShopOrder.objects.filter(pk__in=order_ids))
        kept, deleted = [], []
        counts["members_deleted"] = 0
        for member in Member.objects.select_for_update().filter(pk__in=member_ids).order_by("pk"):
            if member_in_use(member):
                kept.append(member.public_id)
                continue
            deleted.append(member.public_id)
            counts["members_deleted"] += 1
            for name, count in delete_member(member).items():
                counts[name] = counts.get(name, 0) + count
        counts["members_kept"] = len(kept)
        return counts, kept, deleted, files
