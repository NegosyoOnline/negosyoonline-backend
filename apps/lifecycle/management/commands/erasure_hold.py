"""Place or release a hold that stops the erasure of one site.

    manage.py erasure_hold SITE --actor USERNAME --reason-code legal --reference TICKET-123
    manage.py erasure_hold SITE --actor USERNAME --release

The reason code is legal, fraud, dispute, regulator or other. The reference is a number or ticket
reference, never a name or an address. A held site is never erased, and the Pack purge leaves its
buyers alone, until the hold is released. Needs SITE_ERASURE_ENABLED. See the plan, section 5.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.management.commands import _shared
from apps.common.errors import ApiError
from apps.lifecycle import workflow
from apps.lifecycle.management.commands.erasure_propose import refusal


class Command(BaseCommand):
    help = "Place or release an erasure hold on a site."

    def add_arguments(self, parser):
        parser.add_argument("site", metavar="SITE")
        parser.add_argument("--actor", required=True, help="the staff user")
        parser.add_argument("--reason-code", default="", help="legal, fraud, dispute, regulator, other")
        parser.add_argument("--reference", default="", help="a number or ticket reference")
        parser.add_argument("--release", action="store_true", help="release the open hold")

    def handle(self, *args, site, actor, reason_code, reference, release, **options):
        operator = _shared.staff_user(actor)
        now = timezone.now()
        try:
            if release:
                workflow.release_hold(operator, site, now=now)
                self.stdout.write(f"Released the hold on site {site}.")
            else:
                workflow.place_hold(
                    operator, site, reason_code=reason_code, reference=reference, now=now
                )
                self.stdout.write(f"Placed a hold on site {site}.")
        except ApiError as error:
            raise refusal(error) from None
