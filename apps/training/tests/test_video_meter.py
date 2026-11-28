"""The video delivery meter (V-1): the Bunny statistics read, the daily task and the meters `video`
block. Every Bunny call is mocked; no test reaches the network and the key is a test value."""
import io
import json
import urllib.error
from datetime import date, timedelta
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.billing import clock, meters
from apps.billing.models import Subscription
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.base import make_account, make_site
from apps.training import stream, tasks, video_meter
from apps.training.models import StreamLibrary

STATS_KEY = "stats-local-test-key"
URLOPEN = "apps.training.stream.urllib.request.urlopen"
GB = 1_000_000_000


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(body):
    return FakeResponse(json.dumps(body).encode())


@override_settings(BUNNY_STATS_API_KEY=STATS_KEY, BUNNY_STATS_API_BASE="https://stats.test")
class BandwidthCallTests(SimpleTestCase):
    def test_it_reads_total_bandwidth_with_the_key_in_the_header_only(self):
        with mock.patch(URLOPEN, return_value=answer({"TotalBandwidthUsed": 514215.0, "other": 1})) as call:
            used = stream.pull_zone_bandwidth(321, date(2026, 11, 1), date(2026, 11, 9))
        self.assertEqual(used, 514215)
        request = call.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://stats.test/statistics?pullZone=321&dateFrom=2026-11-01&dateTo=2026-11-09"
        )
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Accesskey"), STATS_KEY)
        self.assertNotIn(STATS_KEY, request.full_url)

    def test_a_failure_carries_the_status_and_never_the_key(self):
        error = urllib.error.HTTPError("https://stats.test/x", 401, "no", {}, None)
        with mock.patch(URLOPEN, side_effect=error), self.assertLogs("apps.training.stream", "WARNING") as logs:
            with self.assertRaises(stream.StreamError) as raised:
                stream.pull_zone_bandwidth(1, date(2026, 11, 1), date(2026, 11, 2))
        self.assertEqual(raised.exception.status, 401)
        self.assertNotIn(STATS_KEY, str(raised.exception) + "".join(logs.output))

    def test_no_answer_and_a_wrong_answer_are_stream_errors(self):
        with mock.patch(URLOPEN, side_effect=urllib.error.URLError("down")):
            with self.assertRaises(stream.StreamError):
                stream.pull_zone_bandwidth(1, date(2026, 11, 1), date(2026, 11, 2))
        for body in ({"TotalBandwidthUsed": "lots"}, {}, {"TotalBandwidthUsed": -1}, [1]):
            with mock.patch(URLOPEN, return_value=answer(body)), self.assertRaises(stream.StreamError):
                stream.pull_zone_bandwidth(1, date(2026, 11, 1), date(2026, 11, 2))

    @override_settings(BUNNY_STATS_API_KEY="")
    def test_with_no_key_nothing_is_called(self):
        with mock.patch(URLOPEN) as call, self.assertRaises(stream.StreamError):
            stream.pull_zone_bandwidth(1, date(2026, 11, 1), date(2026, 11, 2))
        call.assert_not_called()


@override_settings(BILLING_ENABLED=True)
class MeterCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.subscription = Subscription.objects.get(site=self.site)
        self.row = StreamLibrary.objects.create(
            site=self.site, library_id=816560, cdn_host="vz-test.b-cdn.net", key_ref="ANA", pull_zone_id=321
        )
        self.start, self.end, _ = meters.period_at(self.subscription, self.subscription.trial_start_at + timedelta(days=2))
        self.now = self.subscription.trial_start_at + timedelta(days=2)


@override_settings(VIDEO_METER_ENABLED=True, BUNNY_STATS_API_KEY=STATS_KEY)
class TaskTests(MeterCase):
    def run_with(self, value=2 * GB, **kwargs):
        with mock.patch.object(stream, "pull_zone_bandwidth", return_value=value, **kwargs) as call:
            return video_meter.refresh_all(self.now), call

    def test_it_keeps_the_latest_reading_for_the_current_period(self):
        counts, call = self.run_with()
        self.assertEqual(counts, {"read": 1, "skipped": 0, "failed": 0})
        self.row.refresh_from_db()
        self.assertEqual((self.row.delivered_bytes, self.row.delivered_period_start, self.row.delivered_read_at),
                         (2 * GB, self.start, self.now))
        pull_zone, date_from, date_to = call.call_args.args
        self.assertEqual(pull_zone, 321)
        self.assertEqual(date_from, self.start.astimezone(clock.MANILA).date())
        self.assertEqual(date_to, self.now.astimezone(clock.MANILA).date())  # not past today
        self.run_with(3 * GB)
        self.row.refresh_from_db()
        self.assertEqual(self.row.delivered_bytes, 3 * GB)  # the latest, not a sum

    @override_settings(VIDEO_METER_ENABLED=False)
    def test_switched_off_it_makes_no_call(self):
        with mock.patch.object(stream, "pull_zone_bandwidth") as call:
            self.assertEqual(video_meter.refresh_all(self.now), {"skipped": "switched off"})
            self.assertEqual(tasks.video_meter_run(), {"skipped": "switched off"})
        call.assert_not_called()

    @override_settings(BUNNY_STATS_API_KEY="")
    def test_an_unset_key_skips_quietly_with_a_log_line_and_no_error(self):
        with mock.patch.object(stream, "pull_zone_bandwidth") as call, self.assertLogs("apps.training.video_meter", "INFO") as logs:
            self.assertEqual(video_meter.refresh_all(self.now), {"skipped": "no statistics key"})
        call.assert_not_called()
        self.assertIn("BUNNY_STATS_API_KEY is not set", logs.output[0])

    def test_a_library_with_no_pull_zone_is_left_alone(self):
        StreamLibrary.objects.update(pull_zone_id=None)
        with mock.patch.object(stream, "pull_zone_bandwidth") as call:
            self.assertEqual(video_meter.refresh_all(self.now), {"read": 0, "skipped": 0, "failed": 0})
        call.assert_not_called()

    def test_a_failed_read_keeps_the_last_reading_and_does_not_raise(self):
        self.run_with(GB)
        with mock.patch.object(stream, "pull_zone_bandwidth", side_effect=stream.StreamError("x")):
            self.assertEqual(video_meter.refresh_all(self.now), {"read": 0, "skipped": 0, "failed": 1})
            self.assertEqual(tasks.video_meter_run(), {"read": 0, "skipped": 0, "failed": 1})
        self.row.refresh_from_db()
        self.assertEqual(self.row.delivered_bytes, GB)

    def test_one_failing_store_does_not_stop_the_next(self):
        other = Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="bens", display_name="B")["id"])
        second = StreamLibrary.objects.create(site=other, library_id=999, cdn_host="h.b-cdn.net", key_ref="BEN", pull_zone_id=7)

        def read(pull_zone, *_):
            if pull_zone == 321:
                raise RuntimeError("boom")
            return 5

        with mock.patch.object(stream, "pull_zone_bandwidth", side_effect=read):
            self.assertEqual(video_meter.refresh_all(self.now), {"read": 1, "skipped": 0, "failed": 1})
        second.refresh_from_db()
        self.assertEqual(second.delivered_bytes, 5)

    def test_a_store_with_no_subscription_is_skipped(self):
        StreamLibrary.objects.all().delete()
        bare = make_site(make_account("bare@example.test"), "bare-store")  # written directly: no subscription
        StreamLibrary.objects.create(site=bare, library_id=55, cdn_host="h.b-cdn.net", key_ref="ANA", pull_zone_id=9)
        with mock.patch.object(stream, "pull_zone_bandwidth") as call:
            self.assertEqual(video_meter.refresh_all(self.now), {"read": 0, "skipped": 1, "failed": 0})
        call.assert_not_called()


class BlockTests(MeterCase):
    def read(self):
        return meters.read(self.subscription, self.now)

    def reading(self, used=2 * GB, start=None):
        StreamLibrary.objects.update(
            delivered_bytes=used, delivered_period_start=start or self.start, delivered_read_at=self.now
        )

    @override_settings(VIDEO_METER_ENABLED=False)
    def test_switched_off_there_is_no_video_block(self):
        self.reading()
        self.assertEqual(set(self.read()), {"storage", "orders"})

    @override_settings(VIDEO_METER_ENABLED=True)
    def test_the_block_is_used_against_fifty_gb_with_the_reading_time(self):
        self.reading(40 * GB)
        self.assertEqual(
            self.read()["video"],
            {"used_bytes": 40 * GB, "limit_bytes": 50 * GB, "read_at": self.now,
             "period_start": self.start, "period_end": self.end},
        )

    @override_settings(VIDEO_METER_ENABLED=True)
    def test_no_reading_or_an_old_period_reads_zero_with_no_time(self):
        zero = {"used_bytes": 0, "limit_bytes": 50 * GB, "read_at": None, "period_start": self.start, "period_end": self.end}
        self.assertEqual(self.read()["video"], zero)  # the task has not run
        self.reading(9 * GB, start=self.start - timedelta(days=30))
        self.assertEqual(self.read()["video"], zero)  # last period's reading is not this period's
        StreamLibrary.objects.all().delete()
        self.assertEqual(self.read()["video"], zero)  # no library at all

    @override_settings(VIDEO_METER_ENABLED=True)
    def test_another_stores_reading_is_never_shown(self):
        other = Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="bens", display_name="B")["id"])
        StreamLibrary.objects.create(
            site=other, library_id=999, cdn_host="h.b-cdn.net", key_ref="BEN", pull_zone_id=7,
            delivered_bytes=7 * GB, delivered_period_start=self.start, delivered_read_at=self.now,
        )
        self.assertEqual(self.read()["video"]["used_bytes"], 0)

    @override_settings(VIDEO_METER_ENABLED=True, BILLING_ENABLED=True)
    def test_the_route_answers_the_block_and_without_the_switch_omits_the_key(self):
        self.reading(GB)
        path = f"/v1/sites/{self.site_id}/billing/meters/"
        with mock.patch("apps.billing.api.timezone") as clock_patch:
            clock_patch.now.return_value = self.now
            body = self.get(path).json()
            self.assertEqual(body["video"]["used_bytes"], GB)
            self.assertEqual(set(body["video"]), {"used_bytes", "limit_bytes", "read_at", "period_start", "period_end"})
            with override_settings(VIDEO_METER_ENABLED=False):
                self.assertEqual(set(self.get(path).json()), {"storage", "orders"})
