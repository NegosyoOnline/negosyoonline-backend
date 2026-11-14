"""Empty ONE test guest shop order of personal data (ClickUp z8uq9mf15g; staging walkthrough cleanup).

    manage.py shop_remove_test_order --site SITE --order ORDER            (dry run)
    manage.py shop_remove_test_order --site SITE --order ORDER --apply
    ... --actor USERNAME     (optional: the staff user, recorded on the audit entry)

SITE is the site's public id; ORDER is the order's public id or its order number (SO00001). The
order row, its lines, totals, status and events stay. What identifies the person goes: the
receiver name, mobile, contact email and delivery address of the order, the reference number,
account name and IP of its proof, and the proof file; the order's product reviews are deleted. It
refuses, writing nothing, when the site or order is not found, the order is not on that site, or
the receiver name does not start with "TEST " (any letter case, leading spaces ignored). Without --apply it prints the counts and writes
nothing. With it, everything is written in one transaction with one `shop_test_order_removed`
audit entry (ids and counts only); the proof file is deleted from the private store only after
the commit. Running it again changes nothing. No bulk mode.
"""
import json
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.accounts.management.commands import _shared
from apps.common import private_files
from apps.operators import audit
from apps.shop.models import ProductReview, ShopOrder, ShopProofSubmission
from apps.shop.retention import ORDER_PERSONAL
from apps.sites.models import Site

logger = logging.getLogger(__name__)

TEST_PREFIX = "TEST "


def _is_test_name(name):
    """True when the name, leading whitespace stripped, starts with "TEST " in any letter case."""
    return name.lstrip().upper().startswith(TEST_PREFIX)


PROOF_BLANKED = ("reference_number", "account_name", "ip_advisory")


def _delete_files(proof_ids):
    """Each proof file, then its key; a key stays until its file is gone. Never logs a key."""
    for pk, key in list(
        ShopProofSubmission._base_manager.filter(pk__in=proof_ids).exclude(file_key="")
        .values_list("pk", "file_key")
    ):
        try:
            private_files.delete(key)
        except private_files.StoreError:
            logger.error("Test order removal: a proof file could not be deleted (row %s).", pk)
            continue
        ShopProofSubmission._base_manager.filter(pk=pk).update(file_key="")


class Command(BaseCommand):
    help = (
        "Remove the personal data of one TEST shop order of one site (receiver name starts with "
        '"TEST ", any case). Dry run unless --apply.'
    )

    def add_arguments(self, parser):
        parser.add_argument("--site", required=True, help="the site's public id (site_...)")
        parser.add_argument("--order", required=True, help="the order's public id or order number")
        parser.add_argument("--apply", action="store_true", help="write; without it, a dry run")
        parser.add_argument("--actor", default="", help="the staff user, for the audit record")

    def handle(self, *args, site, order, apply, actor="", **options):
        operator = _shared.staff_user(actor) if actor else None
        with transaction.atomic():
            found_site = Site.objects.select_for_update().filter(public_id=site).first()
            if found_site is None:
                raise CommandError("No such site. Nothing was written.")
            found = (
                ShopOrder.objects.select_for_update()
                .filter(Q(public_id=order) | Q(order_number=order), site=found_site).first()
            )
            if found is None:
                if ShopOrder.objects.filter(public_id=order).exclude(site=found_site).exists():
                    raise CommandError("That order is not on that site. Nothing was written.")
                raise CommandError("No such order. Nothing was written.")
            proofs = ShopProofSubmission._base_manager.filter(order=found)
            reviews = ProductReview.objects.filter(order=found)
            personal_left = any(getattr(found, f) for f in ORDER_PERSONAL) or any(
                getattr(p, f) for p in proofs for f in PROOF_BLANKED + ("file_key",)
            )
            if found.receiver_name == "" and not personal_left and not reviews.exists():
                self.stdout.write("That order was already emptied. Nothing was changed.")
                return
            if found.receiver_name != "" and not _is_test_name(found.receiver_name):
                raise CommandError(
                    'The receiver name does not start with "TEST " (any case). Nothing was written.'
                )
            proof_ids = list(proofs.values_list("pk", flat=True))
            counts = {
                "orders_blanked": 1,
                "proofs_blanked": len(proof_ids),
                "proof_files_to_delete": proofs.exclude(file_key="").count(),
                "reviews_deleted": reviews.count(),
            }
            if apply:
                reviews.delete()  # the queryset delete; the model's own delete refuses
                ShopOrder.objects.filter(pk=found.pk).update(
                    receiver_name="", receiver_mobile="", contact_email="", delivery_address={},
                )
                proofs.update(**{f: "" for f in PROOF_BLANKED})  # the file key goes with its file
                audit.record(
                    operator, "shop_test_order_removed", target_type="site",
                    target_id=found_site.public_id, detail={"order": found.public_id, "counts": counts},
                )
                transaction.on_commit(lambda: _delete_files(proof_ids))
        mode = "Applied" if apply else "Dry run (nothing written)"
        self.stdout.write(f"{mode}: site {found_site.public_id}, order {found.public_id}")
        self.stdout.write(json.dumps(counts, indent=2, sort_keys=True))
