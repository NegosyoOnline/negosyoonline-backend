"""Put a site on operator hold, take it off, or show it.

    manage.py site_hold --site site_... --on --operator USERNAME
    manage.py site_hold --site site_... --off --operator USERNAME
    manage.py site_hold --show --site site_...
    manage.py site_hold --all-except site_... --on --operator USERNAME

A held site is treated as not served: the public site API, label resolution, custom hosts and
the Pack and shop placement refuse it (every reader of `Site.operator_hold`). Nothing else
changes, and the site's data is untouched. `--all-except` holds every other site that is not
deleted, and only ever turns the hold on. The operator must be an active staff user. The whole
change runs in one transaction with each row locked; each site that changes gets one admin audit
entry, action `site_operator_hold_set`. Output is one line per site: its public id and on or off.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.operators import audit
from apps.sites.models import Site


def _state(value):
    return "on" if value else "off"


class Command(BaseCommand):
    help = "Put a site on operator hold, take it off, or show its state."

    def add_arguments(self, parser):
        parser.add_argument("--operator", help="the staff user running this, for the audit record")
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--site", help="the site's public id (site_...)")
        target.add_argument("--all-except", dest="all_except", help="hold every other site; this one stays as it is")
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--on", action="store_true", help="put on hold")
        mode.add_argument("--off", action="store_true", help="take off hold")
        mode.add_argument("--show", action="store_true", help="print the state, change nothing")

    def handle(self, *args, site=None, all_except=None, on=False, off=False, show=False, operator=None, **options):
        if show:
            if all_except:
                raise CommandError("--show works on one --site.")
            found = self.find(site)
            self.stdout.write(f"{found.public_id} {_state(found.operator_hold)}")
            return
        if all_except and not on:
            raise CommandError("--all-except only works with --on.")
        staff = None
        if operator:
            staff = get_user_model().objects.filter(username=operator, is_active=True, is_staff=True).first()
        if staff is None:
            raise CommandError("--operator must be an active staff user.")
        with transaction.atomic():
            if all_except:
                self.find(all_except)
                pks = list(
                    Site.objects.filter(deleted_at__isnull=True)
                    .exclude(public_id=all_except)
                    .order_by("pk")
                    .values_list("pk", flat=True)
                )
            else:
                pks = [self.find(site).pk]
            for pk in pks:
                self.set_hold(pk, on, staff)

    def set_hold(self, pk, want, staff):
        row = Site.objects.select_for_update().get(pk=pk)
        if row.deleted_at is not None:
            raise CommandError("No such site.")
        if row.operator_hold == want:
            self.stdout.write(f"{row.public_id} {_state(want)} (already, no change)")
            return
        old, row.operator_hold = row.operator_hold, want
        row.save(update_fields=["operator_hold"])
        audit.record(
            staff, "site_operator_hold_set", target_type="site", target_id=row.public_id,
            detail={"old": old, "new": want},
        )
        self.stdout.write(f"{row.public_id} {_state(want)}")

    def find(self, public_id):
        found = Site.objects.filter(public_id=public_id or "", deleted_at__isnull=True).first()
        if found is None:
            raise CommandError("No such site.")
        return found
