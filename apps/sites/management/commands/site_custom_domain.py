"""Attach (or retire) a custom public host on an EXISTING site.

    manage.py site_custom_domain --site-subdomain NAME --host HOST            (dry run, default)
    manage.py site_custom_domain --site-subdomain NAME --host HOST --apply
    manage.py site_custom_domain --site-subdomain NAME --host HOST --retire [--apply]

The site is found from its active subdomain (`NAME`, the platform web address); its id, name,
owner and current hosts are printed so the operator can verify it is the intended site. Without
--apply nothing is written. It never creates a site, copies no data, changes no owner, refuses a
host that belongs to another site, and does nothing when the host is already set. See
docs/runbooks/custom-domain-live4more.md.
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.sites import custom_hosts
from apps.sites.models import Site, SiteCustomHost, Subdomain


class Command(BaseCommand):
    help = "Attach or retire a custom public host on an existing site. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--site-subdomain", required=True, help="the site's active web address name")
        parser.add_argument("--host", required=True, help="the bare host, for example shop.example")
        parser.add_argument("--apply", action="store_true", help="write the change")
        parser.add_argument("--dry-run", action="store_true", help="read only (the default)")
        parser.add_argument("--retire", action="store_true", help="retire the host instead of attaching it")

    def handle(self, *args, site_subdomain, host, apply, dry_run, retire, **options):
        if apply and dry_run:
            raise CommandError("Use --apply or --dry-run, not both.")
        write = apply
        try:
            host = custom_hosts.validated(host)
        except ValidationError as exc:
            raise CommandError(f"Host refused: {'; '.join(exc.messages)}") from None
        site = self.find_site(site_subdomain)
        self.stdout.write(
            f"Site: {site.public_id} | {site.display_name!r} | owner account "
            f"{site.account.public_id} ({site.account.email}) | template {site.template}"
        )
        for row in site.custom_hosts.order_by("pk"):
            self.stdout.write(f"  existing custom host: {row.host} ({row.state})")
        existing = SiteCustomHost.objects.filter(host=host).select_related("site").first()
        if existing is not None and existing.site_id != site.pk:
            raise CommandError(
                f"Refused: {host} already belongs to site {existing.site.public_id}. Nothing changed."
            )
        mode = "APPLY" if write else "DRY RUN (nothing is written)"
        self.stdout.write(f"Mode: {mode}")
        if retire:
            self.retire(existing, host, write)
        else:
            self.attach(site, existing, host, write)

    def find_site(self, name):
        row = (
            Subdomain.objects.filter(name=name.strip().lower(), state=Subdomain.State.ACTIVE)
            .select_related("site", "site__account")
            .first()
        )
        if row is None or row.site is None or row.site.deleted_at is not None:
            raise CommandError("No live site has that active web address. Nothing changed.")
        return row.site

    def attach(self, site, existing, host, write):
        if existing is not None and existing.state == SiteCustomHost.State.ACTIVE:
            self.stdout.write(f"No change: {host} is already the active host of this site.")
            return
        other = site.custom_hosts.filter(state=SiteCustomHost.State.ACTIVE).first()
        if other is not None:
            raise CommandError(
                f"Refused: this site already has the active host {other.host}. Retire it first "
                "with --retire. Nothing changed."
            )
        action = "reactivate" if existing is not None else "attach"
        self.stdout.write(f"Would {action} {host} on this site.")
        if not write:
            return
        with transaction.atomic():
            if existing is not None:
                existing.state = SiteCustomHost.State.ACTIVE
                existing.retired_at = None
                existing.save()
            else:
                SiteCustomHost.objects.create(host=host, site=site)
        self.stdout.write(f"Done: {host} is now the active host of {site.public_id}.")

    def retire(self, existing, host, write):
        if existing is None or existing.state != SiteCustomHost.State.ACTIVE:
            self.stdout.write(f"No change: {host} is not an active host of this site.")
            return
        self.stdout.write(f"Would retire {host}.")
        if not write:
            return
        existing.state = SiteCustomHost.State.RETIRED
        existing.retired_at = timezone.now()
        existing.save()
        self.stdout.write(f"Done: {host} is retired. The platform address keeps working.")
