"""Read-only: one row per lesson saying what video it has. Writes nothing.

    manage.py training_video_audit [--site site_... | <subdomain/address>]

Columns (tab separated): store, course, course_published, module, lesson, video_kind, library_id, video_guid.
video_kind is stream-ready, stream-pending, bunny-embed-link, youtube, vimeo, other-link or none. For a
bunny-embed-link the library id and video guid are parsed from the link; for a Stream video they are the
lesson's own. Course published = shown to members (on and not archived).
"""
from django.core.management.base import BaseCommand, CommandError

from apps.sites.models import Site
from apps.training import video
from apps.training.models import Lesson

COLUMNS = ["store", "course", "course_published", "module", "lesson", "video_kind", "library_id", "video_guid"]


def find_site(wanted):
    """A site by public id, by its subdomain name (or the full host's first label), or by its own domain."""
    from apps.sites.models import SiteCustomHost, Subdomain

    wanted = wanted.strip().lower()
    site = Site.objects.filter(public_id=wanted).first()
    if site is None:
        row = SiteCustomHost.objects.filter(host=wanted).select_related("site").first()
        site = row.site if row else None
    if site is None:
        row = Subdomain.objects.filter(name=wanted.split(".")[0], site__isnull=False).select_related("site").first()
        site = row.site if row else None
    if site is None:
        raise CommandError("No such site.")
    return site


def rows(site=None):
    lessons = Lesson._base_manager.select_related("module__course__site").order_by(
        "module__course__site__public_id", "module__course__order", "module__course__pk", "module__order", "module__pk",
        "order", "pk",
    )
    if site is not None:
        lessons = lessons.filter(module__course__site=site)
    for lesson in lessons:
        module, course = lesson.module, lesson.module.course
        kind = video.video_kind(lesson)
        library, guid = "", ""
        if kind.startswith("stream"):
            library = lesson.stream_library_id or lesson.stream_pending_library_id or ""
            guid = lesson.stream_video_id or lesson.stream_pending_video_id
        elif kind == "bunny-embed-link":
            library, guid = video.parse_bunny_link(lesson.video_url)
        yield [
            f"{course.site.public_id} ({course.site.display_name})", course.title, "yes" if course.active and not course.archived_at else "no",
            module.title, lesson.title, kind, str(library), guid,
        ]


class Command(BaseCommand):
    help = "Read-only: list every lesson with the kind of video it has."

    def add_arguments(self, parser):
        parser.add_argument("--site", help="one store: its public id or its address; all stores when omitted")

    def handle(self, *args, site=None, **options):
        chosen = find_site(site) if site else None
        self.stdout.write("\t".join(COLUMNS))
        for row in rows(chosen):
            self.stdout.write("\t".join(value.replace("\t", " ").replace("\n", " ") for value in row))
