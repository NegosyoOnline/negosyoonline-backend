"""Give a lesson whose legacy video_url is a Bunny link in THIS store's own library its Stream fields.

    manage.py training_stream_backfill --site site_... [--apply]

Dry run by default: prints what it would do and writes nothing. --apply writes. A lesson qualifies when
it has no Stream video of its own (no stream or pending video), its video_url is a Bunny embed or play
link, and the link's library id equals the library id of the store's StreamLibrary row. video_url is
never touched and nothing is deleted. With --apply the video is asked about at Bunny: ready ->
stream fields set and status ready (with its size); Bunny says it is still on its way or failed -> that status;
no answer (no key, Bunny down) -> "processing". The dash's usual refresh (GET .../video/) settles it later. The legacy link keeps playing until the
Stream video is ready. The training app has no audit log, so the printed lines are the record.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.training import stream, video
from apps.training.models import Lesson, StreamLibrary

from .training_video_audit import find_site


def candidates(site, library_id):
    lessons = Lesson._base_manager.filter(
        module__course__site=site, stream_video_id="", stream_pending_video_id=""
    ).exclude(video_url="").select_related("module__course").order_by("module__course__pk", "module__pk", "order", "pk")
    for lesson in lessons:
        parsed = video.parse_bunny_link(lesson.video_url)
        if parsed and parsed[0] == library_id:
            yield lesson, parsed[1]


class Command(BaseCommand):
    help = "Backfill Stream fields from a legacy Bunny link in the store's own library. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--site", required=True, help="the store's public id or address")
        parser.add_argument("--apply", action="store_true", help="write the changes (default: print only)")

    def handle(self, *args, site, apply=False, **options):
        site = find_site(site)
        row = StreamLibrary.objects.filter(site=site).first()
        if row is None:
            raise CommandError("This store has no Stream library set (stream_library set).")
        found = list(candidates(site, row.library_id))
        mode = "APPLY" if apply else "DRY RUN"
        self.stdout.write(f"{mode}: {site.public_id}, library {row.library_id}, {len(found)} lesson(s) match")
        library = None
        for lesson, guid in found:
            if not apply:
                self.stdout.write(f"would set {lesson.public_id} ({lesson.title}) -> library {row.library_id} video {guid}")
                continue
            if library is None:
                try:
                    library = stream.library_from_row(row)
                except stream.NotConfigured:
                    library = False
            status, size = self._ask(library, guid)
            with transaction.atomic():
                fresh = Lesson._base_manager.select_for_update().get(pk=lesson.pk)
                if fresh.stream_video_id or fresh.stream_pending_video_id:
                    self.stdout.write(f"skipped {lesson.public_id}: a Stream video was set meanwhile")
                    continue
                fresh.stream_library_id, fresh.stream_video_id, fresh.stream_status = row.library_id, guid, status
                if status == video.READY:
                    fresh.stream_bytes, fresh.needs_video = size, False
                fresh.save()
            self.stdout.write(f"set {lesson.public_id} ({lesson.title}) -> library {row.library_id} video {guid} status {status}")

    def _ask(self, library, guid):
        if not library:
            return video.PROCESSING, 0
        try:
            answer = stream.get_video(library, guid)
        except stream.StreamError:
            return video.PROCESSING, 0
        status = stream.status_name(answer["status"])
        return status, (answer["storage_size"] if status == video.READY else 0)
