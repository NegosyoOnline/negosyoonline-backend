"""The direct Messenger inbox, with recorded fake Meta payloads only: no live Meta, no real token.
`messenger._transport` is replaced by a fake that records every call."""
import base64
import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity
from apps.inquiries import crypto, messenger, messenger_inbox, retention, stored
from apps.inquiries.models import InquiryConnection, InquiryConversation, InquiryMessage, InquiryReply
from apps.inquiries.tests.base import KEY, switched_on
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

SECRET = "app-secret-for-tests-only"
PAGE_TOKEN = "PAGETOKEN-made-up-" + "x" * 30
USER_TOKEN = "USERTOKEN-made-up-" + "y" * 30
PAGE_A, PAGE_B = "111000111", "222000222"
PSID = "psid-customer-1"
messenger_on = override_settings(
    INQUIRY_MESSENGER_ENABLED=True, META_MESSENGER_APP_ID="123456", META_MESSENGER_APP_SECRET=SECRET,
    MESSENGER_VERIFY_TOKEN="verify-me", DASHBOARD_URL="https://dash.test", META_MESSENGER_CONFIG_ID="",
)


def sign(body):
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def signed_request(user_id="fbuser-1", issued=None, secret=SECRET):
    payload = base64.urlsafe_b64encode(json.dumps({
        "algorithm": "HMAC-SHA256", "user_id": user_id, "issued_at": int(issued or time.time()),
    }).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{sig}.{payload}"


def event(page=PAGE_A, psid=PSID, mid="m_1", text="Do you have size M?", echo=False, ts=None, attachments=None):
    message = {"mid": mid}
    if text is not None:
        message["text"] = text
    if attachments:
        message["attachments"] = [{"type": t, "payload": {"url": "https://cdn.example/secret.jpg?sig=zzz"}} for t in attachments]
    if echo:
        message["is_echo"] = True
        who = {"sender": {"id": page}, "recipient": {"id": psid}}
    else:
        who = {"sender": {"id": psid}, "recipient": {"id": page}}
    return {"object": "page", "entry": [{"id": page, "time": 1, "messaging": [
        {**who, "timestamp": ts or int(time.time() * 1000), "message": message}]}]}


class FakeMeta:
    """Answers the Graph paths the way Meta's docs show, and records the calls."""

    def __init__(self):
        self.calls = []
        self.send_error = None  # a (status, body) to answer the Send API with
        self.subscribe_error = None
        self.permissions = ["pages_show_list", "pages_manage_metadata", "pages_messaging", "pages_read_engagement"]
        self.pages = [{"id": PAGE_A, "name": "Ana Store Page", "access_token": PAGE_TOKEN}]
        self.sent = 0

    def __call__(self, method, path, *, params=None, form=None, token=None):
        self.calls.append((method, path, params or {}, form or {}, token))
        if path == "/oauth/access_token":
            return 200, {"access_token": USER_TOKEN}
        if path == "/me/permissions":
            return 200, {"data": [{"permission": p, "status": "granted"} for p in self.permissions]}
        if path == "/me":
            return 200, {"id": "fbuser-1"}
        if path == "/me/accounts":
            return 200, {"data": self.pages}
        if path == "/debug_token":  # the Login for Business fallback; no granted Pages here
            return 200, {"data": {"granular_scopes": []}}
        if path.endswith("/subscribed_apps"):
            if method == "POST" and self.subscribe_error:
                return self.subscribe_error
            return 200, {"success": True}
        if path.endswith("/messages"):
            self.sent += 1
            return self.send_error or (200, {"recipient_id": PSID, "message_id": f"m_out_{self.sent}"})
        raise AssertionError(f"unexpected path {path}")

    def paths(self):
        return [(m, p) for m, p, *_ in self.calls]


@switched_on
@messenger_on
class MessengerTestCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.meta = FakeMeta()
        patch = mock.patch.object(messenger, "_transport", self.meta)
        patch.start()
        self.addCleanup(patch.stop)
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.base = f"/v1/sites/{self.site_id}/inquiries/messenger/"

    def connect_row(self, site=None, page=PAGE_A, status=InquiryConnection.ACTIVE, user="fbuser-1"):
        with override_settings(INQUIRIES_CREDENTIALS_KEY=KEY):
            return InquiryConnection.objects.create(
                site=site or self.site, provider="messenger", status=status, external_page_id=page,
                label="Ana Store Page", meta_user_id=user, token_encrypted=crypto.encrypt(PAGE_TOKEN),
            )

    def hook(self, payload, header=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        extra = {} if header is False else {"HTTP_X_HUB_SIGNATURE_256": header or sign(body)}
        return self.client.post("/v1/webhooks/messenger/", data=body, content_type="application/json", secure=True, **extra)

    def inbound(self, **kw):
        response = self.hook(event(**kw))
        self.assertEqual(response.status_code, 200)
        return InquiryConversation.objects.filter(provider="messenger").first()


class SettingsAndChecksTests(MessengerTestCase):
    def test_deploy_check_reports_missing_values_while_the_switch_is_on(self):
        from apps.inquiries.checks import check_messenger_dependencies
        with override_settings(META_MESSENGER_APP_SECRET="", MESSENGER_VERIFY_TOKEN=""):
            ids = [e.id for e in check_messenger_dependencies(None)]
        self.assertEqual(ids, ["inquiries.E005"])
        with override_settings(INQUIRIES_ENABLED=False):
            self.assertEqual([e.id for e in check_messenger_dependencies(None)], ["inquiries.E004"])
        self.assertEqual(check_messenger_dependencies(None), [])
        with override_settings(INQUIRY_MESSENGER_ENABLED=False, META_MESSENGER_APP_SECRET=""):
            self.assertEqual(check_messenger_dependencies(None), [])

    def test_the_switch_needs_inquiries(self):
        from apps.inquiries.switch import messenger_on as on
        with override_settings(INQUIRIES_ENABLED=False):
            self.assertFalse(on())
        self.assertTrue(on())

    def test_graph_version_is_pinned_in_one_constant(self):
        self.assertIn(messenger.GRAPH_VERSION, messenger.GRAPH_URL)
        self.assertIn(messenger.GRAPH_VERSION, messenger.DIALOG_URL)


class WebhookTests(MessengerTestCase):
    def verify(self, **q):
        params = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1158201444", **q}
        return self.client.get("/v1/webhooks/messenger/", params, secure=True)

    def test_verify_echoes_the_challenge(self):
        response = self.verify()
        self.assertEqual((response.status_code, response.content), (200, b"1158201444"))

    def test_verify_refuses_a_wrong_token_or_mode_or_missing_config(self):
        self.assertEqual(self.verify(**{"hub.verify_token": "nope"}).status_code, 403)
        self.assertEqual(self.verify(**{"hub.mode": "unsubscribe"}).status_code, 403)
        with override_settings(MESSENGER_VERIFY_TOKEN=""):
            self.assertEqual(self.verify(**{"hub.verify_token": ""}).status_code, 403)
        self.assertEqual(self.verify(**{"hub.challenge": "<script>"}).status_code, 400)

    def test_verify_answers_while_the_switch_is_off(self):
        with override_settings(INQUIRY_MESSENGER_ENABLED=False):
            self.assertEqual(self.verify().status_code, 200)

    def test_unsigned_or_badly_signed_posts_are_403_and_store_nothing(self):
        self.connect_row()
        body = json.dumps(event()).encode()
        for kwargs in ({"header": False}, {"header": "sha256=" + "0" * 64}, {"header": "sha1=abc"}, {"header": sign(body + b" ")}):
            self.assertEqual(self.hook(None, raw=body, **kwargs).status_code, 403)
        with override_settings(META_MESSENGER_APP_SECRET=""):
            self.assertEqual(self.hook(None, raw=body, header=sign(body)).status_code, 403)
        self.assertEqual(InquiryMessage.objects.count(), 0)

    def test_a_signed_post_stores_the_message_and_the_conversation(self):
        self.connect_row()
        conversation = self.inbound()
        message = InquiryMessage.objects.get()
        self.assertEqual((conversation.site, conversation.psid, conversation.stores_history), (self.site, PSID, True))
        self.assertTrue(conversation.external_id.startswith("fm_"))
        self.assertEqual((message.body, message.direction, message.sender_kind, message.external_id), ("Do you have size M?", "in", "customer", "m_1"))
        self.assertTrue(conversation.unread)
        self.assertIsNotNone(conversation.last_inbound_at)

    def test_it_is_idempotent_by_mid(self):
        self.connect_row()
        self.inbound()
        self.inbound()
        self.assertEqual((InquiryMessage.objects.count(), InquiryConversation.objects.count()), (1, 1))

    def test_a_second_message_joins_the_same_conversation(self):
        self.connect_row()
        self.inbound()
        self.inbound(mid="m_2", text="And size L?")
        self.assertEqual((InquiryMessage.objects.count(), InquiryConversation.objects.count()), (2, 1))

    def test_an_echo_is_stored_as_the_stores_own_message_and_does_not_open_the_window(self):
        self.connect_row()
        conversation = self.inbound(echo=True, mid="m_e", text="Hello from the Page inbox")
        message = InquiryMessage.objects.get()
        self.assertEqual((message.direction, message.sender_kind, conversation.psid), ("out", "owner", PSID))
        self.assertIsNone(conversation.last_inbound_at)
        self.assertFalse(messenger_inbox.window_of(conversation)["open"])

    def test_attachments_are_recorded_as_type_only(self):
        self.connect_row()
        conversation = self.inbound(text=None, attachments=["image", "video"])
        message = InquiryMessage.objects.get()
        self.assertEqual((message.body, message.attachment_types), ("", "image,video"))
        self.assertNotIn("secret.jpg", str(list(InquiryMessage.objects.values())))
        thread = self.get(f"/v1/sites/{self.site_id}/inquiries/conversations/{conversation.external_id}/").json()
        self.assertEqual(thread["messages"][0]["attachments"], ["image", "video"])

    def snippet_after(self, **kw):
        self.connect_row()
        self.inbound(**kw)
        items, _ = stored.list_items(self.site)
        return items[0]["snippet"]

    def test_the_list_snippet_of_an_image_only_message_is_photo(self):
        self.assertEqual(self.snippet_after(text=None, attachments=["image"]), "[Photo]")

    def test_the_list_snippet_of_another_attachment_type_is_attachment(self):
        self.assertEqual(self.snippet_after(text=None, attachments=["file"]), "[Attachment]")
        self.assertEqual(stored._attachment_line("video,image"), "[Attachment]")
        self.assertEqual(stored._attachment_line("photo"), "[Photo]")
        self.assertEqual(stored._attachment_line(""), "")

    def test_the_list_snippet_prefers_the_text_over_attachments(self):
        self.assertEqual(self.snippet_after(text="Here it is", attachments=["image"]), "Here it is")

    def test_events_without_a_message_and_foreign_objects_are_ignored(self):
        self.connect_row()
        read = {"object": "page", "entry": [{"id": PAGE_A, "messaging": [
            {"sender": {"id": PSID}, "recipient": {"id": PAGE_A}, "timestamp": 1, "read": {"watermark": 1}}]}]}
        self.assertEqual(self.hook(read).status_code, 200)
        self.assertEqual(self.hook({"object": "instagram", "entry": []}).status_code, 200)
        self.assertEqual(self.hook(None, raw=b"not json").status_code, 200)
        self.assertEqual(InquiryMessage.objects.count(), 0)

    def test_an_unknown_page_or_an_inactive_connection_is_dropped_and_logged_by_code_only(self):
        self.connect_row(status=InquiryConnection.INVALID)
        with self.assertLogs("apps.inquiries.messenger_inbox", "INFO") as logs:
            self.hook(event())
            self.hook(event(page="999"))
        self.assertEqual(InquiryMessage.objects.count(), 0)
        self.assertTrue(all("no_connection" in line for line in logs.output))
        self.assertFalse(any(PSID in line for line in logs.output))

    def test_events_route_to_the_one_site_that_owns_the_page(self):
        other_site = Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="ben-store")["id"])
        self.connect_row()
        self.connect_row(site=other_site, page=PAGE_B, user="fbuser-2")
        self.inbound(page=PAGE_B, mid="m_b", text="for Ben")
        self.inbound(page=PAGE_A, mid="m_a", text="for Ana")
        self.assertEqual(set(InquiryMessage.objects.filter(site=self.site).values_list("body", flat=True)), {"for Ana"})
        self.assertEqual(set(InquiryMessage.objects.filter(site=other_site).values_list("body", flat=True)), {"for Ben"})

    def test_an_event_addressed_to_another_page_inside_an_entry_is_not_stored(self):
        self.connect_row()
        payload = event()
        payload["entry"][0]["messaging"][0]["recipient"]["id"] = "someone-else"
        self.hook(payload)
        self.assertEqual(InquiryMessage.objects.count(), 0)

    def test_the_switch_off_drops_a_signed_post_with_a_200(self):
        self.connect_row()
        with override_settings(INQUIRY_MESSENGER_ENABLED=False):
            self.assertEqual(self.hook(event()).status_code, 200)
        self.assertEqual(InquiryMessage.objects.count(), 0)

    def test_a_body_over_the_cap_is_413(self):
        big = b"{" + b" " * (1_000_001) + b"}"
        self.assertEqual(self.hook(None, raw=big).status_code, 413)


class ConnectTests(MessengerTestCase):
    def start(self):
        response = self.get(self.base + "start/")
        self.assertEqual(response.status_code, 302)
        return response["Location"]

    def callback(self, state, **extra):
        return self.client.get("/v1/inquiries/messenger/callback/", {"state": state, "code": "AUTHCODE", **extra}, secure=True)

    def state(self):
        return parse_qs(urlparse(self.start()).query)["state"][0]

    def pick(self):
        response = self.callback(self.state())
        self.assertEqual(response["Location"], "https://dash.test/settings/inbox/?messenger=pick")

    def test_start_redirects_to_the_dialog_with_a_signed_state(self):
        url = urlparse(self.start())
        query = parse_qs(url.query)
        self.assertEqual(url.netloc, "www.facebook.com")
        self.assertIn(messenger.GRAPH_VERSION, url.path)
        self.assertEqual(query["client_id"], ["123456"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["scope"], ["pages_show_list,pages_manage_metadata,pages_messaging,pages_read_engagement"])
        self.assertTrue(query["redirect_uri"][0].endswith("/v1/inquiries/messenger/callback/"))
        self.assertNotIn(SECRET, url.query)

    def test_a_config_id_replaces_the_scope(self):
        with override_settings(META_MESSENGER_CONFIG_ID="cfg_77"):
            query = parse_qs(urlparse(self.start()).query)
        self.assertEqual(query["config_id"], ["cfg_77"])
        self.assertNotIn("scope", query)

    def test_every_owner_route_is_404_while_off_or_unconfigured_and_401_signed_out(self):
        calls = lambda: [
            self.get(self.base + "start/"), self.get(self.base + "connection/"), self.get(self.base + "pages/"),
            self.send("post", self.base + "connect/", {"page_id": PAGE_A}), self.send("delete", self.base + "connection/"),
        ]
        with override_settings(INQUIRY_MESSENGER_ENABLED=False):
            for response in calls():
                self.error(response, 404, "not_found")
        with override_settings(META_MESSENGER_APP_SECRET=""):
            for response in calls():
                self.error(response, 404, "not_found")
        self.client.cookies.clear()
        for response in calls():
            self.error(response, 401, "unauthenticated")

    def test_another_accounts_site_is_404(self):
        for response in (self.get(self.base + "start/", client=self.other_client()), self.get(self.base + "connection/", client=self.other_client())):
            self.error(response, 404, "not_found")

    def test_the_callback_makes_a_pick_list_and_the_owner_connects_a_page(self):
        self.assertEqual(self.get(self.base + "connection/").json()["status"], "unset")
        self.pick()
        self.assertTrue(self.get(self.base + "connection/").json()["pick_pending"])
        pages = self.get(self.base + "pages/")
        self.assertEqual(pages.json(), {"items": [{"id": PAGE_A, "name": "Ana Store Page"}]})
        self.assertNotIn(PAGE_TOKEN, pages.content.decode())
        response = self.post(self.base + "connect/", {"page_id": PAGE_A})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["page_id"], body["page_name"], body["pick_pending"]), ("active", PAGE_A, "Ana Store Page", False))
        self.assertNotIn(PAGE_TOKEN, response.content.decode())
        row = InquiryConnection.objects.get()
        self.assertEqual((row.provider, row.external_page_id, row.meta_user_id), ("messenger", PAGE_A, "fbuser-1"))
        self.assertNotIn(PAGE_TOKEN, row.token_encrypted)
        with override_settings(INQUIRIES_CREDENTIALS_KEY=KEY):
            self.assertEqual(crypto.decrypt(row.token_encrypted), PAGE_TOKEN)
        self.assertEqual(self.get(self.base + "pages/").status_code, 410)  # the pick list is used up

    def test_the_page_is_subscribed_to_the_five_fields_with_the_page_token_outside_the_url(self):
        self.pick()
        self.post(self.base + "connect/", {"page_id": PAGE_A})
        subscribe = [c for c in self.meta.calls if c[1] == f"/{PAGE_A}/subscribed_apps" and c[0] == "POST"][0]
        self.assertEqual(subscribe[3]["subscribed_fields"], "messages,messaging_postbacks,message_echoes,message_deliveries,message_reads")
        self.assertEqual(subscribe[4], PAGE_TOKEN)
        for _, path, params, *_ in self.meta.calls:
            self.assertNotIn(PAGE_TOKEN, path + json.dumps(params))
            self.assertNotIn(USER_TOKEN, path + json.dumps(params))
        self.assertNotIn(USER_TOKEN, str(list(InquiryConnection.objects.values())))  # the user token is never stored

    def test_connect_errors(self):
        self.error(self.get(self.base + "pages/"), 410, "pick_expired")
        self.error(self.post(self.base + "connect/", {"page_id": PAGE_A}), 410, "pick_expired")
        self.pick()
        self.error(self.post(self.base + "connect/", {"page_id": "not-mine"}), 400, "not_in_pick_list")
        self.meta.subscribe_error = (500, {"error": {"code": 2}})
        self.error(self.post(self.base + "connect/", {"page_id": PAGE_A}), 502, "provider_down")
        self.assertEqual(InquiryConnection.objects.count(), 0)

    def test_d5_a_page_on_another_site_is_refused(self):
        other_site = Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="ben-store")["id"])
        self.connect_row(site=other_site, user="fbuser-2")
        self.pick()
        self.error(self.post(self.base + "connect/", {"page_id": PAGE_A}), 409, "page_taken")
        self.assertEqual(InquiryConnection.objects.filter(site=self.site).count(), 0)
        self.assertEqual(InquiryConnection.objects.get().site, other_site)

    def test_the_database_also_refuses_one_page_on_two_sites(self):
        from django.db import IntegrityError, transaction
        other_site = Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="ben-store")["id"])
        self.connect_row()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.connect_row(site=other_site)

    def test_one_sites_pick_list_is_not_anothers(self):
        self.pick()
        ben = self.other_client()
        ben_site = self.create_site(client=ben, subdomain="ben-store")["id"]
        self.error(self.get(f"/v1/sites/{ben_site}/inquiries/messenger/pages/", client=ben), 410, "pick_expired")
        self.error(self.post(f"/v1/sites/{ben_site}/inquiries/messenger/connect/", {"page_id": PAGE_A}, client=ben), 410, "pick_expired")

    def test_the_callback_refuses_a_bad_replayed_or_foreign_state(self):
        bad = "https://dash.test/settings/inbox/?messenger=error&code=state_invalid"
        self.assertEqual(self.callback("garbage")["Location"], bad)
        state = self.state()
        self.assertEqual(self.callback(state)["Location"], "https://dash.test/settings/inbox/?messenger=pick")
        self.assertEqual(self.callback(state)["Location"], bad)  # one use

    def test_the_callback_is_bound_to_the_browser_that_started_it(self):
        bad = "https://dash.test/settings/inbox/?messenger=error&code=state_invalid"
        other = self.other_client()  # another signed-in account follows the same link
        state = self.state()
        self.meta.calls.clear()
        response = other.get("/v1/inquiries/messenger/callback/", {"state": state, "code": "AUTHCODE"}, secure=True)
        self.assertEqual(response["Location"], bad)
        signed_out = self.client_class().get("/v1/inquiries/messenger/callback/", {"state": state, "code": "AUTHCODE"}, secure=True)
        self.assertEqual(signed_out["Location"], bad)
        self.assertEqual(self.meta.calls, [])  # no code exchange
        self.assertFalse(self.get(self.base + "connection/").json()["pick_pending"])
        # the wrong browsers did not use up the state: the owner's own browser still works
        self.assertEqual(self.callback(state)["Location"], "https://dash.test/settings/inbox/?messenger=pick")

    def test_the_callback_failure_codes(self):
        def code(**extra):
            return parse_qs(urlparse(self.callback(self.state(), **extra)["Location"]).query).get("code", [""])[0]
        self.assertEqual(code(error="access_denied", code=""), "cancelled")
        self.meta.permissions = ["pages_show_list"]
        self.assertEqual(code(), "permission_missing")
        self.meta.permissions = ["pages_show_list", "pages_manage_metadata", "pages_messaging"]
        self.assertEqual(code(), "permission_missing")  # the old three alone are no longer enough
        self.meta.permissions = ["pages_show_list", "pages_manage_metadata", "pages_messaging", "pages_read_engagement"]
        self.meta.pages = []
        self.assertEqual(code(), "no_pages")
        self.meta.pages = [{"id": PAGE_A, "name": "P", "access_token": PAGE_TOKEN}]
        self.meta.calls.clear()
        with mock.patch.object(messenger, "_transport", side_effect=OSError("down")):
            self.assertEqual(code(), "provider_down")

    def error_url(self, code):
        return f"https://dash.test/settings/inbox/?messenger=error&code={code}"

    def test_a_signed_out_crawler_is_logged_as_a_crawler_without_its_user_agent(self):
        state = self.state()
        agent = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
        with self.assertLogs("apps.inquiries.api_messenger", level="INFO") as logs:
            response = self.client_class().get(
                "/v1/inquiries/messenger/callback/", {"state": state, "code": "AUTHCODE"},
                secure=True, HTTP_USER_AGENT=agent,
            )
        self.assertEqual(response["Location"], self.error_url("state_invalid"))
        self.assertIn("Messenger callback: state_invalid (signed_out, crawler)", logs.output[0])
        self.assertNotIn("externalhit", logs.output[0])
        # the crawler burned nothing: the owner's own browser still completes the connection
        self.assertEqual(self.callback(state)["Location"], "https://dash.test/settings/inbox/?messenger=pick")

    def test_the_callback_logs_its_outcome_without_secrets(self):
        def run(**extra):
            with self.assertLogs("apps.inquiries.api_messenger", level="INFO") as logs:
                self.callback(self.state(), **extra)
            return logs
        state_text = self.state()
        logs = run()
        self.assertEqual(logs.output[0].split(":", 2)[2], "Messenger callback: pick")
        self.meta.permissions = ["pages_show_list"]
        logs2 = run()
        self.assertIn("Messenger callback: permission_missing (missing: pages_manage_metadata, pages_messaging, pages_read_engagement)", logs2.output[0])
        self.meta.permissions = ["pages_show_list", "pages_manage_metadata", "pages_messaging", "pages_read_engagement"]
        self.meta.pages = []
        logs3 = run()
        self.assertIn("Messenger callback: no_pages", logs3.output[0])
        with self.assertLogs("apps.inquiries.api_messenger", level="INFO") as logs4:
            self.client_class().get("/v1/inquiries/messenger/callback/", {"state": state_text, "code": "AUTHCODE"}, secure=True)
        self.assertIn("Messenger callback: state_invalid (signed_out)", logs4.output[0])
        for text in logs.output + logs2.output + logs3.output + logs4.output:
            for secret in ("AUTHCODE", state_text, USER_TOKEN, PAGE_TOKEN, "Ana Store Page"):
                self.assertNotIn(secret, text)

    def test_disconnect_unsubscribes_and_deletes_the_token(self):
        self.connect_row()
        response = self.send("delete", self.base + "connection/")
        self.assertEqual(response.status_code, 204)
        self.assertIn(("DELETE", f"/{PAGE_A}/subscribed_apps"), self.meta.paths())
        delete = [c for c in self.meta.calls if c[0] == "DELETE"][0]
        self.assertEqual(delete[4], PAGE_TOKEN)  # the Page's own token, not the app token
        self.assertEqual(delete[2], {"appsecret_proof": messenger._proof(PAGE_TOKEN)})
        self.assertNotIn(f"{123456}|{SECRET}", [c[4] for c in self.meta.calls])
        row = InquiryConnection.objects.get()
        self.assertEqual((row.token_encrypted, row.external_page_id, row.status), ("", "", "unset"))
        self.assertEqual(self.get(self.base + "connection/").json()["status"], "unset")

    def test_disconnect_with_an_unreadable_token_skips_the_unsubscribe_and_still_clears(self):
        row = self.connect_row()
        InquiryConnection.objects.filter(pk=row.pk).update(token_encrypted="not-a-fernet-token")
        self.assertEqual(self.send("delete", self.base + "connection/").status_code, 204)
        self.assertNotIn("DELETE", [m for m, _ in self.meta.paths()])
        self.assertEqual(InquiryConnection.objects.get().external_page_id, "")

    def test_switching_pages_unsubscribes_the_old_page_with_its_own_token(self):
        self.connect_row()
        self.meta.pages = [{"id": PAGE_B, "name": "Second Page", "access_token": "PAGETOKEN-B-" + "z" * 30}]
        self.pick()
        self.assertEqual(self.post(self.base + "connect/", {"page_id": PAGE_B}).status_code, 200)
        delete = [c for c in self.meta.calls if c[0] == "DELETE"]
        self.assertEqual([(c[1], c[4]) for c in delete], [(f"/{PAGE_A}/subscribed_apps", PAGE_TOKEN)])

    def test_the_old_page_is_unsubscribed_only_after_the_switch_is_saved(self):
        self.connect_row()
        self.meta.pages = [{"id": PAGE_B, "name": "Second Page", "access_token": "PAGETOKEN-B-" + "z" * 30}]
        self.pick()
        seen = []
        real = messenger.unsubscribe_page

        def spy(page_id, token):
            seen.append((page_id, token, InquiryConnection.objects.get(site=self.site).external_page_id))
            return real(page_id, token)

        with mock.patch.object(messenger, "unsubscribe_page", spy):
            self.assertEqual(self.post(self.base + "connect/", {"page_id": PAGE_B}).status_code, 200)
        self.assertEqual(seen, [(PAGE_A, PAGE_TOKEN, PAGE_B)])  # the row already showed Page B

    def test_a_lost_race_does_not_unsubscribe_the_old_page(self):
        from django.db import IntegrityError
        self.connect_row()
        self.meta.pages = [{"id": PAGE_B, "name": "Second Page", "access_token": "PAGETOKEN-B-" + "z" * 30}]
        self.pick()
        with mock.patch.object(InquiryConnection, "save", side_effect=IntegrityError("unique")):
            self.error(self.post(self.base + "connect/", {"page_id": PAGE_B}), 409, "page_taken")
        self.assertEqual([c for c in self.meta.calls if c[0] == "DELETE"], [])
        self.assertEqual(InquiryConnection.objects.get(site=self.site).external_page_id, PAGE_A)

    def test_connection_shape_for_active_and_invalid(self):
        row = self.connect_row()
        body = self.get(self.base + "connection/").json()
        self.assertEqual(set(body), {"status", "page_id", "page_name", "connected_at", "pick_pending", "comments_ready"})
        self.assertEqual((body["status"], body["page_id"], body["page_name"]), ("active", PAGE_A, "Ana Store Page"))
        row.status = "invalid"
        row.save()
        self.assertEqual(self.get(self.base + "connection/").json()["status"], "invalid")

    def test_no_token_reaches_the_logs(self):
        with self.assertLogs("apps.inquiries", "DEBUG") as logs:
            self.meta.subscribe_error = (400, {"error": {"code": 190, "message": PAGE_TOKEN}})
            self.pick()
            self.post(self.base + "connect/", {"page_id": PAGE_A})
            self.connect_row()
            self.hook(event(page="999"))
        text = "\n".join(logs.output)
        for secret in (PAGE_TOKEN, USER_TOKEN, SECRET):
            self.assertNotIn(secret, text)


class ReplyTests(MessengerTestCase):
    def setUp(self):
        super().setUp()
        self.row = self.connect_row()
        self.conversation = self.inbound()
        self.ref = self.conversation.external_id
        self.url = f"/v1/sites/{self.site_id}/inquiries/conversations/{self.ref}/"

    def reply(self, key="key-00000001", text="Yes, size M is in stock."):
        return self.send("post", self.url + "reply/", {"text": text, "idempotency_key": key})

    def age(self, hours):
        InquiryConversation.objects.filter(pk=self.conversation.pk).update(
            last_inbound_at=timezone.now() - timedelta(hours=hours))

    def test_the_list_and_thread_show_the_messenger_conversation_with_its_window(self):
        listing = self.get(f"/v1/sites/{self.site_id}/inquiries/conversations/").json()["items"][0]
        self.assertEqual((listing["ref"], listing["channel"], listing["display_name"]), (self.ref, "messenger", "Messenger customer"))
        thread = self.get(self.url).json()
        self.assertTrue(thread["reply_window"]["open"])
        self.assertIsNotNone(thread["reply_window"]["closes_at"])
        self.assertTrue(thread["reply"]["open"])
        self.age(25)
        thread = self.get(self.url).json()
        self.assertFalse(thread["reply_window"]["open"])
        self.assertEqual(thread["reply"], {"open": False, "reason": "window_closed"})

    def test_a_stored_channel_thread_has_no_reply_window(self):
        from apps.inquiries.tests.base import make_stored
        make_stored(self.site)
        thread = self.get(f"/v1/sites/{self.site_id}/inquiries/conversations/wc_" + "a1b2c3d4e5f6" * 2 + "/").json()
        self.assertNotIn("reply_window", thread)

    def test_a_reply_inside_the_window_goes_through_the_send_api(self):
        response = self.reply()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["state"], "sent")
        send = [c for c in self.meta.calls if c[1] == f"/{PAGE_A}/messages"][0]
        self.assertEqual(json.loads(send[3]["recipient"]), {"id": PSID})
        self.assertEqual(send[3]["messaging_type"], "RESPONSE")
        self.assertEqual(json.loads(send[3]["message"]), {"text": "Yes, size M is in stock."})
        self.assertEqual(send[4], PAGE_TOKEN)
        audit = InquiryReply.objects.get()
        self.assertEqual((audit.state, audit.provider_message_ref, audit.body_length), ("sent", "m_out_1", len("Yes, size M is in stock.")))
        self.assertNotIn("size M is in stock", str(list(InquiryReply.objects.values())))
        out = InquiryMessage.objects.get(external_id="m_out_1")
        self.assertEqual((out.direction, out.sender_kind, out.delivery_state), ("out", "owner", "sent"))

    def test_after_a_page_switch_an_old_conversation_cannot_reply_with_the_new_pages_token(self):
        self.assertEqual(self.conversation.page_id, PAGE_A)
        self.meta.pages = [{"id": PAGE_B, "name": "Second Page", "access_token": PAGE_TOKEN}]
        state = parse_qs(urlparse(self.get(self.base + "start/")["Location"]).query)["state"][0]
        self.client.get("/v1/inquiries/messenger/callback/", {"state": state, "code": "AUTHCODE"}, secure=True)
        self.assertEqual(self.post(self.base + "connect/", {"page_id": PAGE_B}).status_code, 200)
        self.assertEqual(InquiryConnection.objects.get().external_page_id, PAGE_B)
        self.meta.calls.clear()
        response = self.reply()
        self.assertEqual((response.json()["state"], response.json()["error_code"]), ("failed", "not_configured"))
        self.assertEqual([c for c in self.meta.calls if c[1].endswith("/messages")], [])
        self.assertEqual(InquiryMessage.objects.filter(direction="out").count(), 0)
        # a conversation that arrives on the new Page replies normally, from that Page
        fresh = self.hook(event(page=PAGE_B, psid="psid-new", mid="m_new"))
        self.assertEqual(fresh.status_code, 200)
        new = InquiryConversation.objects.get(psid="psid-new")
        self.assertEqual(new.page_id, PAGE_B)
        url = f"/v1/sites/{self.site_id}/inquiries/conversations/{new.external_id}/reply/"
        ok = self.send("post", url, {"text": "Hello", "idempotency_key": "key-00000002"})
        self.assertEqual(ok.json()["state"], "sent")
        self.assertEqual(len([c for c in self.meta.calls if c[1] == f"/{PAGE_B}/messages"]), 1)

    def test_the_echo_of_our_own_reply_is_not_stored_twice(self):
        self.reply()
        self.hook(event(echo=True, mid="m_out_1", text="Yes, size M is in stock."))
        self.assertEqual(InquiryMessage.objects.filter(external_id="m_out_1").count(), 1)

    def test_a_reply_outside_the_window_is_refused_before_any_call(self):
        self.age(24 * 3600 // 3600 + 1)
        self.meta.calls.clear()
        response = self.reply()
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["state"], response.json()["error_code"]), ("blocked_window", "window_closed"))
        self.assertEqual(self.meta.calls, [])
        self.assertEqual(InquiryMessage.objects.filter(direction="out").count(), 0)

    def test_a_repeated_key_sends_once(self):
        self.reply()
        again = self.reply()
        self.assertEqual(again.json()["state"], "sent")
        self.assertEqual(self.meta.sent, 1)
        self.assertEqual(InquiryReply.objects.count(), 1)

    def test_meta_errors_map_to_outcomes_without_leaking_anything(self):
        cases = [
            ((400, {"error": {"code": 190, "message": PAGE_TOKEN}}), "token_invalid"),
            ((400, {"error": {"code": 10, "error_subcode": 2018278}}), "window_closed"),
            ((400, {"error": {"code": 10, "error_subcode": 1893015}}), "forbidden_page"),
            ((400, {"error": {"code": 200}}), "forbidden_page"),
            ((400, {"error": {"code": 613, "error_subcode": 2534040}}), "rate_limited"),
            ((400, {"error": {"code": 551}}), "not_found"),
            ((500, {"error": {"code": 2}}), "provider_down"),
            ((400, {"error": {"code": 9999}}), "bad_response"),
        ]
        for n, (answer, code) in enumerate(cases):
            self.meta.send_error = answer
            response = self.reply(key=f"key-{n:08d}")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["error_code"], code)
            self.assertNotIn(PAGE_TOKEN, response.content.decode())
            self.assertNotIn("message", response.json().get("error", {}))
            InquiryConnection.objects.filter(pk=self.row.pk).update(status="active")
        self.assertFalse(any(PAGE_TOKEN in str(v) for v in InquiryReply.objects.values_list("error_code", "provider_message_ref")))

    def test_an_invalid_token_marks_the_connection_invalid_and_stops_further_calls(self):
        self.meta.send_error = (400, {"error": {"code": 190}})
        self.reply()
        self.assertEqual(InquiryConnection.objects.get().status, "invalid")
        self.meta.calls.clear()
        response = self.reply(key="key-00000002")
        self.assertEqual(response.json()["error_code"], "token_invalid")
        self.assertEqual(self.meta.calls, [])

    def test_the_limiter_is_asked_per_connection(self):
        with mock.patch("apps.inquiries.messenger_inbox.limiter.acquire", return_value=False) as acquire:
            response = self.reply()
        acquire.assert_called_once_with(self.row.pk)
        self.assertEqual((response.json()["state"], response.json()["error_code"]), ("blocked_rate", "rate_limited"))
        self.assertEqual(self.meta.sent, 0)

    def test_read_close_and_reopen_work_on_a_messenger_ref(self):
        read = self.send("post", self.url + "read/")
        self.assertEqual((read.status_code, read.json()), (200, {"ref": self.ref, "status": "open", "unread": False}))
        closed = self.send("post", self.url + "close/")
        self.assertEqual((closed.status_code, closed.json()["status"]), (200, "closed"))
        self.assertEqual(self.reply().status_code, 409)  # a closed conversation takes no reply
        reopened = self.send("post", self.url + "reopen/")
        self.assertEqual((reopened.status_code, reopened.json()["status"]), (200, "open"))

    def test_a_new_customer_message_reopens_and_marks_unread(self):
        self.send("post", self.url + "close/")
        self.send("post", self.url + "read/")
        self.inbound(mid="m_new", text="Hello again")
        conversation = InquiryConversation.objects.get(pk=self.conversation.pk)
        self.assertEqual((conversation.status, conversation.unread), ("open", True))

    def test_isolation_another_site_cannot_see_or_reply_or_change_the_conversation(self):
        ben = self.other_client()
        ben_site = self.create_site(client=ben, subdomain="ben-store")["id"]
        url = f"/v1/sites/{ben_site}/inquiries/conversations/{self.ref}/"
        self.error(self.get(url, client=ben), 404, "not_found")
        self.error(self.send("post", url + "reply/", {"text": "hi", "idempotency_key": "key-99999999"}, client=ben), 404, "not_found")
        for action in ("read", "close", "reopen"):
            self.error(self.send("post", url + action + "/", client=ben), 404, "not_found")
        self.assertEqual(self.get(f"/v1/sites/{ben_site}/inquiries/conversations/", client=ben).json()["items"], [])
        self.assertEqual(self.meta.sent, 0)

    def test_messenger_conversations_are_hidden_while_the_switch_is_off(self):
        with override_settings(INQUIRY_MESSENGER_ENABLED=False):
            self.assertEqual(self.get(f"/v1/sites/{self.site_id}/inquiries/conversations/").json()["items"], [])
            self.error(self.get(self.url), 404, "not_found")


class MetaCallbackTests(MessengerTestCase):
    def post_signed(self, path, value):
        return self.client.post(f"/v1/webhooks/messenger/{path}/", {"signed_request": value}, secure=True)

    def test_deauthorize_marks_the_users_connection_invalid_and_deletes_the_token(self):
        self.connect_row()
        other_site = Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="ben-store")["id"])
        self.connect_row(site=other_site, page=PAGE_B, user="fbuser-2")
        response = self.post_signed("deauthorize", signed_request("fbuser-1"))
        self.assertEqual(response.status_code, 200)
        mine = InquiryConnection.objects.get(site=self.site)
        self.assertEqual((mine.status, mine.token_encrypted), ("invalid", ""))
        self.assertNotIn("DELETE", [m for m, _ in self.meta.paths()])  # the app is already removed
        self.assertEqual(InquiryConnection.objects.get(site=other_site).status, "active")

    def test_a_request_shaped_the_way_meta_sends_it_is_accepted_and_applied(self):
        """Meta's payload also carries a `user` object; the Messenger app's secret signs it."""
        self.connect_row()
        data = {"algorithm": "HMAC-SHA256", "issued_at": int(time.time()), "user": {"country": "ph", "locale": "en_US"}, "user_id": "fbuser-1"}
        payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
        sig = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        response = self.post_signed("deauthorize", f"{sig}.{payload}")
        self.assertEqual((response.status_code, response.json()), (200, {}))
        self.assertEqual(InquiryConnection.objects.get(site=self.site).status, "invalid")

    def link(self, user="fbuser-1", account=None):
        return ExternalIdentity.objects.create(
            account=account or self.account, provider="facebook", provider_user_id=user,
        )

    def test_one_signed_request_removes_the_messenger_data_and_the_sign_in_link(self):
        for path in ("deauthorize", "data-deletion"):
            with self.subTest(path=path):
                InquiryConnection.objects.all().delete()
                ExternalIdentity.objects.all().delete()
                row = self.connect_row()
                self.link()
                response = self.post_signed(path, signed_request("fbuser-1"))
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(InquiryConnection.objects.get(pk=row.pk).token_encrypted, "")
                self.assertFalse(ExternalIdentity.objects.filter(provider="facebook", provider_user_id="fbuser-1").exists())
                self.assertTrue(AccountAuditEvent.objects.filter(account=self.account, event="identity_removed").exists())

    def test_a_sign_in_link_without_a_connection_is_removed_and_the_answer_is_the_same(self):
        self.link()
        deauthorize = self.post_signed("deauthorize", signed_request("fbuser-1"))
        self.assertEqual((deauthorize.status_code, deauthorize.json()), (200, {}))
        self.assertFalse(ExternalIdentity.objects.exists())
        self.link()
        deletion = self.post_signed("data-deletion", signed_request("fbuser-1"))
        self.assertEqual(deletion.status_code, 200, deletion.content)
        self.assertEqual(set(deletion.json()), {"url", "confirmation_code"})
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_a_connection_without_a_sign_in_link_behaves_as_before(self):
        self.connect_row()
        self.assertEqual(self.post_signed("data-deletion", signed_request("fbuser-1")).status_code, 200)
        self.assertEqual(InquiryConnection.objects.get(site=self.site).token_encrypted, "")
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_another_facebook_users_link_and_other_sites_are_untouched(self):
        self.connect_row()
        other_site = Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="ben-store")["id"])
        self.connect_row(site=other_site, page=PAGE_B, user="fbuser-2")
        ben = Account.objects.exclude(pk=self.account.pk).get()
        self.link("fbuser-1")
        self.link("fbuser-2", account=ben)
        for path in ("deauthorize", "data-deletion"):
            self.assertEqual(self.post_signed(path, signed_request("fbuser-1")).status_code, 200)
        self.assertEqual(list(ExternalIdentity.objects.values_list("provider_user_id", flat=True)), ["fbuser-2"])
        self.assertEqual(InquiryConnection.objects.get(site=other_site).status, "active")

    def test_a_wrong_signature_keeps_the_sign_in_link(self):
        self.connect_row()
        self.link()
        for path in ("deauthorize", "data-deletion"):
            response = self.post_signed(path, signed_request("fbuser-1", secret="the-login-apps-secret"))
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid_signature"))
        self.assertEqual(ExternalIdentity.objects.count(), 1)
        self.assertEqual(InquiryConnection.objects.get(site=self.site).status, "active")

    def test_a_failed_messenger_cleanup_keeps_the_link_removed_and_a_retry_completes_it(self):
        self.connect_row()
        self.inbound()
        self.link()
        with mock.patch("apps.inquiries.messenger_inbox.erase_user", side_effect=RuntimeError("boom")):
            response = self.post_signed("data-deletion", signed_request("fbuser-1"))
        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertTrue(InquiryMessage.objects.filter(site=self.site).exists())
        retry = self.post_signed("data-deletion", signed_request("fbuser-1"))
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(set(retry.json()), {"url", "confirmation_code"})
        self.assertFalse(InquiryMessage.objects.filter(site=self.site).exists())
        self.assertEqual(InquiryConnection.objects.get(site=self.site).token_encrypted, "")

    def test_malformed_payloads_are_refused_with_the_fixed_code_and_log_only_the_class(self):
        self.connect_row()
        data = {"algorithm": "HMAC-SHA256", "user_id": "fbuser-1"}  # no issued_at
        payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
        sig = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        for value in ("", "nodot", "a.b", f"{sig}.{payload}"):
            with self.subTest(value=value[:8]), self.assertLogs("apps.accounts.facebook", level="WARNING") as logs:
                response = self.post_signed("deauthorize", value)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid_signature"))
            self.assertNotIn(SECRET, "".join(logs.output))
            self.assertNotIn(sig, "".join(logs.output))
        self.assertEqual(InquiryConnection.objects.get(site=self.site).status, "active")

    def test_a_forged_stale_or_unconfigured_callback_is_refused(self):
        self.connect_row()
        for path in ("deauthorize", "data-deletion"):
            self.assertEqual(self.post_signed(path, signed_request(secret="wrong")).status_code, 400)
            self.assertEqual(self.post_signed(path, signed_request(issued=time.time() - 3 * 86400)).status_code, 400)
            self.assertEqual(self.post_signed(path, "").status_code, 400)
            with override_settings(META_MESSENGER_APP_SECRET=""):
                self.assertEqual(self.post_signed(path, signed_request()).status_code, 404)
        self.assertEqual(InquiryConnection.objects.get().status, "active")

    def test_the_callbacks_work_while_the_switch_is_off(self):
        self.connect_row()
        with override_settings(INQUIRY_MESSENGER_ENABLED=False):
            self.assertEqual(self.post_signed("data-deletion", signed_request()).status_code, 200)

    def test_data_deletion_erases_the_users_messenger_data_and_answers_with_a_status_route(self):
        self.connect_row()
        conversation = self.inbound()
        other_site = Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="ben-store")["id"])
        self.connect_row(site=other_site, page=PAGE_B, user="fbuser-2")
        self.inbound(page=PAGE_B, psid="psid-ben", mid="m_ben", text="ben's customer")
        response = self.post_signed("data-deletion", signed_request("fbuser-1"))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {"url", "confirmation_code"})
        self.assertFalse(InquiryMessage.objects.filter(site=self.site).exists())
        self.assertFalse(InquiryConversation.objects.filter(pk=conversation.pk).exists())
        mine = InquiryConnection.objects.get(site=self.site)
        self.assertEqual((mine.token_encrypted, mine.external_page_id, mine.meta_user_id), ("", "", ""))
        self.assertEqual(InquiryMessage.objects.filter(site=other_site).count(), 1)  # another user's data stays
        status = self.client.get(urlparse(body["url"]).path, secure=True)
        self.assertEqual((status.status_code, status.json()), (200, {"status": "completed"}))
        self.assertEqual(self.client.get("/v1/webhooks/messenger/data-deletion/forged/", secure=True).status_code, 404)

    def test_data_deletion_keeps_a_conversation_a_reply_audit_row_points_at_but_blanks_it(self):
        self.connect_row()
        conversation = self.inbound()
        InquiryReply.objects.create(
            site=self.site, conversation=conversation, author=self.account, idempotency_key="key-77777777", body_length=3,
        )
        self.post_signed("data-deletion", signed_request("fbuser-1"))
        conversation.refresh_from_db()
        self.assertEqual((conversation.psid, conversation.display_name, conversation.status), ("", "", "closed"))
        self.assertFalse(InquiryMessage.objects.exists())

    def test_data_deletion_is_scoped_to_the_users_connections_and_unsubscribes_with_the_page_token(self):
        self.connect_row()
        # a customer whose page-scoped id happens to equal the Facebook user id, on someone else's Page
        other_site = Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="ben-store")["id"])
        self.connect_row(site=other_site, page=PAGE_B, user="fbuser-2")
        stranger = self.inbound(page=PAGE_B, psid="fbuser-1", mid="m_same", text="coincidence")
        self.meta.calls.clear()
        self.post_signed("data-deletion", signed_request("fbuser-1"))
        stranger.refresh_from_db()
        self.assertEqual(stranger.psid, "fbuser-1")
        self.assertEqual(InquiryMessage.objects.filter(site=other_site).count(), 1)
        delete = [c for c in self.meta.calls if c[0] == "DELETE"]
        self.assertEqual([(c[1], c[4]) for c in delete], [(f"/{PAGE_A}/subscribed_apps", PAGE_TOKEN)])

    def test_the_same_answer_for_an_unknown_user(self):
        self.assertEqual(self.post_signed("data-deletion", signed_request("nobody")).status_code, 200)


@override_settings(RETENTION_ENABLED=True)
class RetentionTests(MessengerTestCase):
    def test_old_messenger_messages_and_the_customer_id_are_removed_like_stored_channels(self):
        self.connect_row()
        conversation = self.inbound()
        old = timezone.now() - retention.MESSAGE_RETENTION - timedelta(days=1)
        InquiryConversation.objects.filter(pk=conversation.pk).update(last_message_at=old)
        retention.old_messages(timezone.now(), False)
        self.assertFalse(InquiryMessage.objects.exists())
        self.assertFalse(InquiryConversation.objects.filter(pk=conversation.pk).exists())

    def test_a_recent_messenger_conversation_is_kept(self):
        self.connect_row()
        self.inbound()
        retention.old_messages(timezone.now(), False)
        self.assertEqual(InquiryMessage.objects.count(), 1)

    def test_site_disposal_blanks_the_token_the_page_and_the_customer_fields(self):
        self.connect_row()
        conversation = self.inbound()
        retention.blank_inquiries(Site.objects.filter(pk=self.site.pk))
        row = InquiryConnection.objects.get()
        self.assertEqual((row.token_encrypted, row.external_page_id, row.meta_user_id, row.status), ("", "", "", "unset"))
        conversation.refresh_from_db()
        self.assertEqual(conversation.psid, "")
        self.assertFalse(InquiryMessage.objects.exists())


class WebhookSummaryTests(MessengerTestCase):
    """One warning summary line per POST: fixed codes and counts only (task z8uq9mg76y)."""
    LOGGER = "apps.inquiries.messenger_inbox"
    ZERO_KINDS = dict(message=0, echo=0, delivery=0, read=0, postback=0, pass_thread_control=0, take_thread_control=0, other=0)

    def line(self, payload, **expect):
        with self.assertLogs(self.LOGGER, "WARNING") as logs:
            self.assertEqual(self.hook(payload).status_code, 200)
        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1)
        out = "\n".join(logs.output)
        for secret in (PSID, "m_1", "m_2", PAGE_A, PAGE_B, "Do you have size M?", "secret.jpg", PAGE_TOKEN):
            self.assertNotIn(secret, out)
        return warnings[0].getMessage()

    def fmt(self, entries=(1, 0, 0, 0), kinds=None, stored=0, dropped=(0, 0, 0, 0)):
        k = {**self.ZERO_KINDS, **(kinds or {})}
        return (
            "Messenger webhook summary entries=messaging:%d,standby:%d,changes:%d,other:%d events=%s stored=%d "
            "dropped=no_connection:%d,not_addressed:%d,empty:%d,duplicate:%d"
            % (*entries, ",".join(f"{a}:{b}" for a, b in k.items()), stored, *dropped)
        )

    def test_stored_message(self):
        self.connect_row()
        self.assertEqual(self.line(event()), self.fmt(kinds={"message": 1}, stored=1))
        self.assertEqual(InquiryMessage.objects.count(), 1)

    def test_echo(self):
        self.connect_row()
        self.assertEqual(self.line(event(echo=True)), self.fmt(kinds={"echo": 1}, stored=1))

    def test_delivery_and_read(self):
        self.connect_row()
        payload = {"object": "page", "entry": [{"id": PAGE_A, "time": 1, "messaging": [
            {"sender": {"id": PSID}, "recipient": {"id": PAGE_A}, "delivery": {"mids": ["m_1"]}},
            {"sender": {"id": PSID}, "recipient": {"id": PAGE_A}, "read": {"watermark": 1}},
            {"sender": {"id": PSID}, "recipient": {"id": PAGE_A}, "postback": {"payload": "x"}},
        ]}]}
        self.assertEqual(self.line(payload), self.fmt(kinds={"delivery": 1, "read": 1, "postback": 1}))
        self.assertEqual(InquiryMessage.objects.count(), 0)

    def test_standby_is_counted_never_stored_and_calls_nothing(self):
        self.connect_row()
        payload = event()
        payload["entry"][0]["standby"] = payload["entry"][0].pop("messaging")
        payload["entry"][0]["standby"].append({"sender": {"id": PSID}, "recipient": {"id": PAGE_A}, "take_thread_control": {"x": 1}})
        before = len(self.meta.calls) if hasattr(self.meta, "calls") else 0
        self.assertEqual(self.line(payload), self.fmt(entries=(0, 1, 0, 0), kinds={"message": 1, "take_thread_control": 1}))
        self.assertEqual((InquiryMessage.objects.count(), InquiryConversation.objects.count()), (0, 0))
        self.assertEqual(len(self.meta.calls) if hasattr(self.meta, "calls") else 0, before)

    def test_no_connection(self):
        self.assertEqual(self.line(event()), self.fmt(kinds={"message": 1}, dropped=(1, 0, 0, 0)))
        empty = {"object": "page", "entry": [{"id": PAGE_B, "changes": [{"field": "x"}], "other_key": 1}]}
        self.assertEqual(self.line(empty), self.fmt(entries=(0, 0, 1, 1), dropped=(1, 0, 0, 0)))

    def test_not_addressed(self):
        self.connect_row()
        payload = event()
        payload["entry"][0]["messaging"][0]["recipient"]["id"] = PAGE_B
        self.assertEqual(self.line(payload), self.fmt(kinds={"message": 1}, dropped=(0, 1, 0, 0)))

    def test_empty(self):
        self.connect_row()
        self.assertEqual(self.line(event(text=None)), self.fmt(kinds={"message": 1}, dropped=(0, 0, 1, 0)))

    def test_duplicate(self):
        self.connect_row()
        self.line(event())
        self.assertEqual(self.line(event()), self.fmt(kinds={"message": 1}, dropped=(0, 0, 0, 1)))

    def test_mixed_payload(self):
        self.connect_row()
        good = event()["entry"][0]["messaging"]
        late = event(mid="m_2")["entry"][0]["messaging"]
        payload = {"object": "page", "entry": [
            {"id": PAGE_A, "time": 1, "messaging": good + late + [{"sender": {"id": PSID}, "recipient": {"id": PAGE_A}, "read": {}}],
             "standby": event(mid="m_3")["entry"][0]["messaging"]},
            {"id": PAGE_B, "time": 1, "messaging": event(page=PAGE_B)["entry"][0]["messaging"]},
        ]}
        self.assertEqual(
            self.line(payload),
            self.fmt(entries=(2, 1, 0, 0), kinds={"message": 4, "read": 1}, stored=2, dropped=(1, 0, 0, 0)),
        )

    def test_error_line_stays_and_is_the_class_only(self):
        self.connect_row()
        with mock.patch.object(messenger_inbox, "_store_event", side_effect=ValueError("m_1 secret text")):
            with self.assertLogs(self.LOGGER, "ERROR") as logs:
                self.hook(event())
        self.assertEqual([r.getMessage() for r in logs.records], ["Messenger event not stored (ValueError)"])

    def test_non_page_object_logs_nothing(self):
        with self.assertNoLogs(self.LOGGER, "WARNING"):
            self.hook({"object": "user", "entry": []})
