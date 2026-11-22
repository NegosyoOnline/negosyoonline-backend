"""Name the store whose shared courses and media other Santé stores may copy.

    manage.py sante_content_source --operator USERNAME --site site_...
    manage.py sante_content_source --operator USERNAME --clear

SITE is the live Santé site's public id. With no source set, the "From Live4More" lists are
empty. One admin audit entry is written, action `sante_content_source_set`.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.sante import services
from apps.sites.models import Site


class Command(BaseCommand):
    help = "Set or clear the Santé content source store."

    def add_arguments(self, parser):
        parser.add_argument("--operator", required=True, help="the staff user running this, for the audit record")
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--site", help="the source site's public id (site_...)")
        group.add_argument("--clear", action="store_true", help="remove the source")

    def handle(self, *args, operator, site=None, clear=False, **options):
        staff = get_user_model().objects.filter(username=operator, is_active=True, is_staff=True).first()
        if staff is None:
            raise CommandError("--operator must be an active staff user.")
        found = None
        if not clear:
            found = Site.objects.filter(public_id=site, deleted_at__isnull=True).first()
            if found is None:
                raise CommandError("No such site.")
        try:
            services.set_content_source(found, operator=staff)
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            "The content source is cleared." if clear else f"The content source is now {found.public_id}."
        )
