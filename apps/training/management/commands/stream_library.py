"""Set or show a store's own Bunny Stream library. Staff only, run by hand.

    manage.py stream_library set --site site_... --library-id 816560 --cdn-host vz-xxxx.b-cdn.net --key-ref LIVE4MORE
    manage.py stream_library show [--site site_...]

--pull-zone-id (optional, the library's Bunny pull zone id) lets the video delivery meter read the store
(VIDEO_METER_ENABLED); left out, an existing value is kept.

--key-ref names the environment variable BUNNY_STREAM_KEY_<KEY_REF> (uppercase letters, digits and
underscore) that holds the library's API key. The command never takes the key, and `show` prints
everything except the key, and only whether that variable is present.
"""
import re

from django.core.management.base import BaseCommand, CommandError

from apps.sites.models import Site
from apps.training import stream, video
from apps.training.models import StreamLibrary

HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,118}[a-z0-9])?(:[0-9]{2,5})?$")


class Command(BaseCommand):
    help = "Set or show a store's own Bunny Stream library (the key is never an argument)."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        setter = sub.add_parser("set")
        setter.add_argument("--site", required=True, help="the store's public id (site_...)")
        setter.add_argument("--library-id", required=True, type=int)
        setter.add_argument("--cdn-host", required=True, help="the library's CDN host name, no scheme")
        setter.add_argument("--key-ref", required=True, help="NAME of the key variable suffix, e.g. LIVE4MORE")
        setter.add_argument("--pull-zone-id", type=int, default=None, help="the library's pull zone id (video meter)")
        shower = sub.add_parser("show")
        shower.add_argument("--site", help="one store; all libraries when omitted")

    def handle(self, *args, action, **options):
        if action == "set":
            self._set(options)
        else:
            self._show(options.get("site"))

    def _set(self, options):
        site = Site.objects.filter(public_id=options["site"], deleted_at__isnull=True).first()
        if site is None:
            raise CommandError("No such site.")
        library_id, host, key_ref = options["library_id"], options["cdn_host"].strip().lower(), options["key_ref"]
        if library_id < 1:
            raise CommandError("--library-id must be a positive number.")
        if not HOST_RE.match(host):
            raise CommandError("--cdn-host must be a host name such as vz-xxxx.b-cdn.net (no scheme, no path).")
        if not stream.key_ref_ok(key_ref):
            raise CommandError("--key-ref may only hold uppercase letters, digits and underscore (40 at most).")
        other = StreamLibrary.objects.filter(library_id=library_id).exclude(site=site).first()
        if other is not None:
            raise CommandError("That library already belongs to another store.")
        values = {"library_id": library_id, "cdn_host": host, "key_ref": key_ref}
        if options.get("pull_zone_id") is not None:
            if options["pull_zone_id"] < 1:
                raise CommandError("--pull-zone-id must be a positive number.")
            values["pull_zone_id"] = options["pull_zone_id"]
        row, _ = StreamLibrary.objects.update_or_create(site=site, defaults=values)
        self._print(row)

    def _show(self, site_id):
        rows = StreamLibrary.objects.select_related("site").order_by("site__public_id")
        if site_id:
            rows = rows.filter(site__public_id=site_id)
        if not rows:
            self.stdout.write("No library is set.")
        for row in rows:
            self._print(row)

    def _print(self, row):
        info = video.library_summary(row)
        self.stdout.write(
            f"site={info['site']} library_id={info['library_id']} cdn_host={info['cdn_host']} "
            f"key_ref={info['key_ref']} key_variable_present={'yes' if info['key_variable_present'] else 'no'} "
            f"pull_zone_id={row.pull_zone_id or 'none'}"
        )
