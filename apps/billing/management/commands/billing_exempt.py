"""Mark one site as never billed: an approved test site.

    manage.py billing_exempt SITE --actor USERNAME --reason "TEXT"

SITE is the site's public id (site_...). The site is then always served and never charged,
and nothing changes its subscription again. It is set once: no command, screen or endpoint
clears it. It writes one admin audit entry, action `billing_exempt_set`. The reason goes into
that entry, so it must hold no personal value. See docs/runbooks/billing.md.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.billing import services
from apps.sites.models import Site


class Command(BaseCommand):
    help = "Mark one site's subscription as exempt from billing. It cannot be undone."

    def add_arguments(self, parser):
        parser.add_argument("site", help="the site's public id (site_...)")
        parser.add_argument(
            "--actor", required=True, help="the staff user running this command, for the audit record"
        )
        parser.add_argument("--reason", required=True, help="why; no personal value")

    def handle(self, *args, site, actor, reason, **options):
        if not settings.BILLING_ENABLED:
            raise CommandError("BILLING_ENABLED is off in this environment. Nothing was changed.")
        operator = get_user_model().objects.filter(
            username=actor, is_active=True, is_staff=True
        ).first()
        if operator is None:
            raise CommandError("--actor must be an active staff user.")
        found = Site.objects.filter(public_id=site, deleted_at__isnull=True).first()
        if found is None:
            raise CommandError("No such site.")
        try:
            changed = services.set_exempt(found, operator=operator, reason=reason)
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        if changed:
            self.stdout.write(f"Site {found.public_id} is now exempt from billing.")
        else:
            self.stdout.write(f"Site {found.public_id} was already exempt. Nothing was changed.")
