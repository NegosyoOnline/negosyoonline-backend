"""The Login for Business Page fallback of messenger.list_pages, with recorded fake Meta payloads only:
no live Meta, no real token. `messenger._transport` is replaced by a fake that records every call."""
import json
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase, override_settings

from apps.inquiries import messenger, outcomes
from apps.inquiries.models import InquiryConnection
from apps.inquiries.tests.test_messenger import (
    PAGE_A, PAGE_B, USER_TOKEN, MessengerTestCase,
)

PAGE_TOKEN_A = "PAGETOKEN-A-made-up-" + "a" * 30
PAGE_TOKEN_B = "PAGETOKEN-B-made-up-" + "b" * 30
SECRETS = (USER_TOKEN, PAGE_TOKEN_A, PAGE_TOKEN_B, "app-secret-for-tests-only")


def scopes(**by_scope):
    return {"data": {"granular_scopes": [{"scope": k, "target_ids": v} for k, v in by_scope.items()]}}


class FakeGraph:
    def __init__(self, accounts=(), debug=None, pages=None, errors=None):
        self.calls = []
        self.accounts = list(accounts)
        self.debug = debug if debug is not None else scopes()
        self.pages = pages if pages is not None else {
            PAGE_A: {"id": PAGE_A, "name": "Page A", "access_token": PAGE_TOKEN_A},
            PAGE_B: {"id": PAGE_B, "name": "Page B", "access_token": PAGE_TOKEN_B},
        }
        self.errors = errors or {}  # path -> (status, body)

    def __call__(self, method, path, *, params=None, form=None, token=None):
        self.calls.append((method, path, params or {}, token))
        if path in self.errors:
            return self.errors[path]
        if path == "/me/accounts":
            return 200, {"data": self.accounts}
        if path == "/debug_token":
            return 200, self.debug
        row = self.pages.get(path.lstrip("/"))
        if row is None:
            return 400, {"error": {"code": 100}}
        return 200, row

    def paths(self):
        return [p for _, p, *_ in self.calls]


@override_settings(META_MESSENGER_APP_ID="123456", META_MESSENGER_APP_SECRET="app-secret-for-tests-only")
class ListPagesFallbackTests(SimpleTestCase):
    def run_list(self, graph):
        with mock.patch.object(messenger, "_transport", graph), self.assertLogs("apps.inquiries.messenger", level="DEBUG") as logs:
            messenger.logger.warning("sentinel")  # assertLogs needs at least one record
            result = messenger.list_pages(USER_TOKEN)
        for record in logs.output:
            for secret in SECRETS:
                self.assertNotIn(secret, record)
        return result

    def test_me_accounts_pages_are_unchanged_and_debug_token_is_not_called(self):
        graph = FakeGraph(accounts=[{"id": PAGE_A, "name": "Page A", "access_token": PAGE_TOKEN_A}])
        result = self.run_list(graph)
        self.assertEqual(result.code, outcomes.OK)
        self.assertEqual(result.value, [{"id": PAGE_A, "name": "Page A", "access_token": PAGE_TOKEN_A}])
        self.assertEqual(graph.paths(), ["/me/accounts"])

    def test_accounts_rows_without_a_token_fall_back(self):
        graph = FakeGraph(accounts=[{"id": PAGE_A, "name": "Page A"}], debug=scopes(pages_messaging=[PAGE_A]))
        self.assertEqual([p["id"] for p in self.run_list(graph).value], [PAGE_A])

    def test_empty_accounts_use_the_pages_messaging_target_ids(self):
        graph = FakeGraph(debug=scopes(pages_messaging=[PAGE_A, PAGE_B, PAGE_A], pages_show_list=["999"]))
        result = self.run_list(graph)
        self.assertEqual(result.code, outcomes.OK)
        self.assertEqual(result.value, [
            {"id": PAGE_A, "name": "Page A", "access_token": PAGE_TOKEN_A},
            {"id": PAGE_B, "name": "Page B", "access_token": PAGE_TOKEN_B},
        ])
        self.assertEqual(graph.paths(), ["/me/accounts", "/debug_token", f"/{PAGE_A}", f"/{PAGE_B}"])
        debug = graph.calls[1]
        self.assertEqual(debug[2]["input_token"], USER_TOKEN)
        self.assertEqual(debug[2]["access_token"], "123456|app-secret-for-tests-only")
        page_read = graph.calls[2]
        self.assertEqual(page_read[3], USER_TOKEN)  # Authorization header, not the URL
        self.assertEqual(page_read[2]["fields"], "id,name,access_token")
        self.assertEqual(page_read[2]["appsecret_proof"], messenger._proof(USER_TOKEN))
        self.assertNotIn(USER_TOKEN, json.dumps(page_read[2]))

    def test_it_falls_back_to_pages_show_list_target_ids(self):
        graph = FakeGraph(debug=scopes(pages_show_list=[PAGE_B], pages_manage_metadata=[PAGE_A]))
        self.assertEqual([p["id"] for p in self.run_list(graph).value], [PAGE_B])

    def test_a_pages_messaging_entry_with_no_ids_falls_back_to_pages_show_list(self):
        graph = FakeGraph(debug=scopes(pages_messaging=[], pages_show_list=[PAGE_A]))
        self.assertEqual([p["id"] for p in self.run_list(graph).value], [PAGE_A])

    def test_both_empty_is_ok_with_no_pages(self):
        for debug in (scopes(), scopes(pages_messaging=[], pages_show_list=[]), {"data": {}}, {}):
            result = self.run_list(FakeGraph(debug=debug))
            self.assertEqual((result.code, result.value), (outcomes.OK, []))

    def test_a_non_digit_target_id_is_skipped(self):
        bad = ["abc", "12/34", "../me", "", "12 3", None, True, {"x": 1}]
        graph = FakeGraph(debug=scopes(pages_messaging=bad + [PAGE_A]))
        result = self.run_list(graph)
        self.assertEqual([p["id"] for p in result.value], [PAGE_A])
        self.assertEqual(graph.paths(), ["/me/accounts", "/debug_token", f"/{PAGE_A}"])

    def test_at_most_100_ids_are_read(self):
        ids = [str(1000 + n) for n in range(150)]
        pages = {i: {"id": i, "name": "P", "access_token": PAGE_TOKEN_A} for i in ids}
        result = self.run_list(FakeGraph(debug=scopes(pages_messaging=ids), pages=pages))
        self.assertEqual(len(result.value), 100)

    def test_rows_without_a_token_are_dropped_and_names_truncated(self):
        pages = {
            PAGE_A: {"id": PAGE_A, "name": "n" * 100, "access_token": PAGE_TOKEN_A},
            PAGE_B: {"id": PAGE_B, "name": "No token"},
        }
        result = self.run_list(FakeGraph(debug=scopes(pages_messaging=[PAGE_A, PAGE_B]), pages=pages))
        self.assertEqual(result.value, [{"id": PAGE_A, "name": "n" * 60, "access_token": PAGE_TOKEN_A}])

    def test_a_debug_token_error_returns_its_mapped_code(self):
        for status, body, code in (
            (500, {"error": {"code": 2}}, outcomes.PROVIDER_DOWN),
            (400, {"error": {"code": 190}}, outcomes.TOKEN_INVALID),
            (400, {"error": {"code": 4}}, outcomes.RATE_LIMITED),
        ):
            result = self.run_list(FakeGraph(errors={"/debug_token": (status, body)}))
            self.assertEqual((result.code, result.ok), (code, False))

    def test_a_page_read_error_fails_the_whole_call_with_its_mapped_code(self):
        # Chosen behaviour: fail fast. A partial list would show the owner a Page list that silently
        # lacks a Page they ticked, so any failed read returns that error and no Pages at all.
        graph = FakeGraph(debug=scopes(pages_messaging=[PAGE_A, PAGE_B]), errors={f"/{PAGE_B}": (500, {"error": {"code": 2}})})
        result = self.run_list(graph)
        self.assertEqual((result.code, result.ok), (outcomes.PROVIDER_DOWN, False))

    def test_no_answer_from_meta_is_provider_down(self):
        with mock.patch.object(messenger, "_transport", side_effect=OSError("down")):
            self.assertEqual(messenger.list_pages(USER_TOKEN).code, outcomes.PROVIDER_DOWN)


class CallbackFallbackTests(MessengerTestCase):
    def setUp(self):
        super().setUp()
        self.graph = FakeGraph(debug=scopes(pages_messaging=[PAGE_A]))
        self.graph.pages[PAGE_A] = {"id": PAGE_A, "name": "Ana Store Page", "access_token": PAGE_TOKEN_A}
        self.meta.pages = []  # /me/accounts is empty; everything else answers like FakeMeta
        base = self.meta

        def transport(method, path, **kw):
            if path in ("/debug_token",) or path == f"/{PAGE_A}" and method == "GET":
                return self.graph(method, path, **kw)
            return base(method, path, **kw)
        patch = mock.patch.object(messenger, "_transport", transport)
        patch.start()
        self.addCleanup(patch.stop)

    def callback(self):
        start = self.get(self.base + "start/")
        state = parse_qs(urlparse(start["Location"]).query)["state"][0]
        return self.client.get("/v1/inquiries/messenger/callback/", {"state": state, "code": "AUTHCODE"}, secure=True)

    def test_the_callback_reaches_the_pick_list_and_connect_works(self):
        with self.assertLogs("apps.inquiries", level="DEBUG") as logs:
            messenger.logger.warning("sentinel")
            response = self.callback()
        self.assertEqual(response["Location"], "https://dash.test/settings/inbox/?messenger=pick")
        for record in logs.output:
            for secret in SECRETS:
                self.assertNotIn(secret, record)
        pages = self.get(self.base + "pages/")
        self.assertEqual(pages.json(), {"items": [{"id": PAGE_A, "name": "Ana Store Page"}]})
        self.assertNotIn(PAGE_TOKEN_A, pages.content.decode())
        connect = self.post(self.base + "connect/", {"page_id": PAGE_A})
        self.assertEqual(connect.status_code, 200, connect.content)
        self.assertEqual(connect.json()["status"], "active")
        row = InquiryConnection.objects.get()
        self.assertNotIn(PAGE_TOKEN_A, row.token_encrypted)
        subscribe = [c for c in self.meta.calls if c[1] == f"/{PAGE_A}/subscribed_apps" and c[0] == "POST"][0]
        self.assertEqual(subscribe[4], PAGE_TOKEN_A)

    def test_nothing_anywhere_is_the_no_pages_redirect(self):
        self.graph.debug = scopes()
        self.assertEqual(self.callback()["Location"], "https://dash.test/settings/inbox/?messenger=error&code=no_pages")

    def test_a_graph_error_in_the_fallback_is_provider_down(self):
        self.graph.errors["/debug_token"] = (500, {"error": {"code": 2}})
        self.assertEqual(self.callback()["Location"], "https://dash.test/settings/inbox/?messenger=error&code=provider_down")
