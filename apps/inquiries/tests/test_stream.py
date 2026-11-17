"""LIVE-INBOX stream: the access rules before a stream starts, the wire format, the cap, the
lifetime and the cleanup. Redis is replaced by a small fake with the calls the view makes."""
import asyncio
import json
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.accounts.tests.base import ORIGIN
from apps.inquiries import live, stream
from apps.inquiries.models import InquiryChannelSettings  # noqa: F401
from apps.inquiries.tests.test_stored import StoredOwnerTestCase


class FakePubSub:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.channels = []
        self.closed = False

    async def subscribe(self, channel):
        self.channels.append(channel)

    async def get_message(self, ignore_subscribe_messages=True, timeout=0):
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def aclose(self):
        self.closed = True


class FakeRedis:
    """Slots live in one shared dict so several streams of one account see each other."""
    slots = {}

    def __init__(self):
        self.ps = FakePubSub()
        self.closed = False
        self.connection_pool = SimpleNamespace(connection_kwargs={})

    def pubsub(self):
        return self.ps

    async def eval(self, script, numkeys, key, now, expiry, limit, member, ttl):
        members = FakeRedis.slots.setdefault(key, {})
        for old, until in list(members.items()):
            if until <= float(now):
                del members[old]
        if len(members) >= int(limit):
            return 0
        members[member] = float(expiry)
        return 1

    async def zadd(self, key, mapping):
        FakeRedis.slots.setdefault(key, {}).update(mapping)

    async def zrem(self, key, member):
        FakeRedis.slots.get(key, {}).pop(member, None)

    async def aclose(self):
        self.closed = True


def drain(response):
    """The open response's chunks until it stops, with the stream's own sleeps cut short."""
    async def collect():
        return [part async for part in response.streaming_content]
    return asyncio.run(collect())


class StreamRouteTests(StoredOwnerTestCase):
    def setUp(self):
        super().setUp()
        FakeRedis.slots = {}
        self.url = self.base + "stream/"
        self.fakes = []
        patcher = mock.patch.object(stream.aredis.Redis, "from_url", side_effect=self.make_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_client(self, *args, **kwargs):
        client = FakeRedis()
        self.fakes.append(client)
        return client

    def open(self, client=None, **extra):
        return (client or self.client).get(self.url, secure=True, HTTP_ORIGIN=ORIGIN, **extra)

    def test_switch_off_and_missing_tables_are_404(self):
        with override_settings(INQUIRIES_ENABLED=False, REDIS_URL="redis://x"):
            self.error(self.open(), 404, "not_found")
        with override_settings(REDIS_URL="redis://x"), mock.patch("apps.inquiries.api_owner.tables_exist", return_value=False):
            self.error(self.open(), 404, "not_found")

    @override_settings(REDIS_URL="redis://x")
    def test_signed_out_is_401(self):
        self.client.cookies.clear()
        self.error(self.open(), 401, "unauthenticated")

    @override_settings(REDIS_URL="redis://x")
    def test_a_foreign_site_is_404(self):
        self.error(self.client.get(f"/v1/sites/{self.ben_site_id}/inquiries/stream/", secure=True), 404, "not_found")

    @override_settings(REDIS_URL="redis://x")
    def test_billing_only_is_403(self):
        with mock.patch("apps.sites.api.billing.position", return_value=SimpleNamespace(billing_only=True)):
            self.error(self.open(), 403, "billing_only")

    @override_settings(REDIS_URL="")
    def test_no_redis_is_503_before_streaming(self):
        self.error(self.open(), 503, "stream_unavailable")

    @override_settings(REDIS_URL="redis://x")
    def test_a_redis_that_fails_is_503_and_its_client_is_closed(self):
        with mock.patch.object(FakeRedis, "eval", side_effect=OSError("down")):
            self.error(self.open(), 503, "stream_unavailable")
        self.assertTrue(self.fakes[0].closed)

    @override_settings(REDIS_URL="redis://x")
    def test_headers_and_cors_for_the_dash_origin(self):
        response = self.open()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertEqual(response["Cache-Control"], "no-cache, no-transform")
        self.assertEqual(response["X-Accel-Buffering"], "no")
        self.assertNotIn("Content-Length", response)
        self.assertEqual(response["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
        # Not closed here: the test client's close of a streaming response runs request_finished,
        # which closes the test's own database connection. Closing is tested on _Stream itself.

    @override_settings(REDIS_URL="redis://x")
    def test_the_sixth_open_stream_of_an_account_is_429_and_a_closed_one_frees_a_slot(self):
        opened = [self.open() for _ in range(stream.MAX_STREAMS)]
        self.assertTrue(all(r.status_code == 200 for r in opened))
        self.error(self.open(), 429, "too_many_streams")
        self.assertTrue(self.fakes[-1].closed)
        # A slot that is released (or has expired) lets the next stream in.
        key = next(iter(FakeRedis.slots))
        FakeRedis.slots[key].pop(next(iter(FakeRedis.slots[key])))
        self.assertEqual(self.open().status_code, 200)


class StreamBodyTests(SimpleTestCase):
    """The generator itself, with the sign-in check replaced and the clocks shortened."""

    def run_stream(self, scenario, *, ping=0.05, lifetime=5, access=(7, None)):
        client = FakeRedis()
        FakeRedis.slots = {}
        result = []

        async def main():
            body = stream._Stream(SimpleNamespace(), "site_x", 7, client, "m1")
            await client.zadd(stream._slot_key(7), {"m1": 9e12})
            await scenario(body, client, result)
            await body.aclose()
        with mock.patch.object(stream, "PING_SECONDS", ping), mock.patch.object(stream, "LIFETIME_SECONDS", lifetime), \
                mock.patch.object(stream, "_access", return_value=access):
            asyncio.run(main())
        return client, result

    def test_retry_ready_then_pings_and_events_in_the_contract_format(self):
        async def scenario(body, client, result):
            for _ in range(2):
                result.append(await body.__anext__())
            result.append(await body.__anext__())  # a ping
            await client.ps.queue.put({"type": "message", "data": json.dumps({
                "type": "conversation_updated", "ref": "wc_a", "last_message_at": "2026-11-17T11:03:00Z",
                "kind": "message", "body": "PRIVATE TEXT", "psid": "123",
            })})
            await client.ps.queue.put({"type": "message", "data": json.dumps({"type": "message_status", "ref": "wc_a", "x": 1})})
            while len(result) < 5:
                result.append(await body.__anext__())
        client, result = self.run_stream(scenario)
        self.assertEqual(result[0], b"retry: 5000\n\n")
        self.assertEqual(result[1], b'event: ready\ndata: {"v":1}\n\n')
        self.assertEqual(client.ps.channels, ["nego:inbox:site:site_x"])
        text = b"".join(result)
        self.assertIn(b": ping\n\n", text)
        self.assertIn(
            b'event: conversation_updated\ndata: {"ref":"wc_a","last_message_at":"2026-11-17T11:03:00Z","kind":"message"}\n\n', text,
        )
        self.assertIn(b'event: message_status\ndata: {"ref":"wc_a"}\n\n', text)
        self.assertNotIn(b"PRIVATE", text)
        self.assertNotIn(b"psid", text)
        self.assertNotIn(b"id:", text)

    def test_junk_on_the_channel_is_ignored(self):
        async def scenario(body, client, result):
            await body.__anext__(); await body.__anext__()
            for raw in ("not json", json.dumps({"type": "other", "ref": "x"}), json.dumps({"type": "message_status", "ref": 5})):
                await client.ps.queue.put({"type": "message", "data": raw})
            result.append(await body.__anext__())
        _, result = self.run_stream(scenario)
        self.assertEqual(result, [b": ping\n\n"])

    def test_a_ping_after_the_session_ended_ends_the_stream_and_frees_the_slot(self):
        async def scenario(body, client, result):
            await body.__anext__(); await body.__anext__()
            with self.assertRaises(StopAsyncIteration):
                await body.__anext__()
        client, _ = self.run_stream(scenario, access=(None, object()))
        self.assertTrue(client.closed and client.ps.closed)
        self.assertEqual(FakeRedis.slots[stream._slot_key(7)], {})

    def test_it_ends_after_its_lifetime(self):
        async def scenario(body, client, result):
            result.extend([await body.__anext__(), await body.__anext__()])
            with self.assertRaises(StopAsyncIteration):
                while True:
                    await body.__anext__()
        client, _ = self.run_stream(scenario, ping=0.02, lifetime=0.1)
        self.assertTrue(client.closed)

    def test_a_stream_never_started_still_releases_its_slot(self):
        async def scenario(body, client, result):
            pass  # aclose() runs straight after, as the sync close() hook would
        client, _ = self.run_stream(scenario)
        self.assertTrue(client.closed)
        self.assertEqual(FakeRedis.slots[stream._slot_key(7)], {})

    def test_a_cancelled_stream_releases(self):
        client = FakeRedis()
        FakeRedis.slots = {}

        async def main():
            body = stream._Stream(SimpleNamespace(), "site_x", 7, client, "m1")
            await body.__anext__(); await body.__anext__()
            task = asyncio.ensure_future(body.__anext__())
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.05)
        asyncio.run(main())
        self.assertTrue(client.closed)
        self.assertEqual(FakeRedis.slots[stream._slot_key(7)], {})

    def test_the_sync_close_hook_frees_a_stream_that_never_started(self):
        client = FakeRedis()
        FakeRedis.slots = {stream._slot_key(7): {"m1": 9e12}}

        async def main():
            body = stream._Stream(SimpleNamespace(), "site_x", 7, client, "m1")
            await asyncio.get_running_loop().run_in_executor(None, body.close)  # as Django does, from a thread
            await asyncio.sleep(0.05)
            body.close()  # twice is harmless
        asyncio.run(main())
        self.assertTrue(client.closed)
        self.assertEqual(FakeRedis.slots[stream._slot_key(7)], {})
