"""The stored channels on the owner's routes (list, thread, reply, close and reopen, read, channel
settings), their site isolation, and their retention, disposal and erasure."""
from datetime import timedelta
from unittest import mock

from django.core.checks import run_checks
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts import retention as account_rules
from apps.accounts.tests.disposal_base import DisposalTestCase
from apps.common import retention
from apps.inquiries import retention as inquiry_retention
from apps.inquiries.models import (
    InquiryChannelSettings, InquiryConversation, InquiryMessage, InquiryReply,
)
from apps.inquiries.switch import stored_tables_exist
from apps.inquiries.tests.base import (
    LIST_OK, REF, REPLY_TEXT, WC_REF, FakePancake, chat_on, make_stored, no_limiter, switched_on,
)
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

BEN_WC_REF = "wc_" + "0f1e2d3c4b5a" * 2


@switched_on
class StoredOwnerTestCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        no_limiter(self)
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.base = f"/v1/sites/{self.site_id}/inquiries/"
        self.ben = self.other_client()
        self.ben_site_id = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben's")["id"]
        self.ben_site = Site.objects.get(public_id=self.ben_site_id)
        self.ben_base = f"/v1/sites/{self.ben_site_id}/inquiries/"
        self.conv = make_stored(self.site, text="SECRET CUSTOMER TEXT")
        self.ben_conv = make_stored(self.ben_site, ref=BEN_WC_REF, name="Ben's visitor")

    def reply(self, key="key-12345678", text=REPLY_TEXT, ref=WC_REF, base=None, client=None):
        return self.send(
            "post", f"{base or self.base}conversations/{ref}/reply/",
            {"text": text, "idempotency_key": key}, client=client,
        )


class ListAndThreadTests(StoredOwnerTestCase):
    def test_the_list_holds_stored_conversations_without_a_pancake_connection(self):
        body = self.get(self.base + "conversations/").json()
        (item,) = body["items"]
        self.assertEqual((item["ref"], item["channel"], item["source"]), (WC_REF, "webchat", "stored"))
        self.assertEqual(item["display_name"], "Maria Santos")
        self.assertTrue(item["unread"])
        self.assertEqual(body["sources"], {"pancake": "not_configured", "stored": "ok"})

    def test_the_list_merges_with_pancake_by_time_and_flags_each_source(self):
        from apps.inquiries.tests.base import configure

        configure(self.site)
        InquiryConversation.objects.filter(pk=self.conv.pk).update(last_message_at=timezone.now())
        with FakePancake():
            body = self.get(self.base + "conversations/").json()
        refs = [i["ref"] for i in body["items"]]
        self.assertEqual(refs[0], WC_REF)
        self.assertEqual(len(refs), 3)
        self.assertEqual(body["sources"], {"pancake": "ok", "stored": "ok"})

    def test_stored_conversations_past_the_first_page_are_reached_by_the_stored_cursor(self):
        from apps.inquiries.stored import LIST_LIMIT

        base = timezone.now() - timedelta(days=1)
        for n in range(LIST_LIMIT + 5):  # 5 more than a page, two of them with the same time
            at = base + timedelta(minutes=min(n, LIST_LIMIT + 2))
            make_stored(self.site, ref="wc_" + f"{n:024x}", at=at)
        total = InquiryConversation.objects.filter(site=self.site).count()
        first = self.get(self.base + "conversations/").json()
        self.assertEqual(len(first["items"]), LIST_LIMIT)
        self.assertIsNone(first["next_cursor"])  # the Pancake cursor contract is untouched
        self.assertTrue(first["stored_next_cursor"])
        seen = [i["ref"] for i in first["items"]]
        cursor = first["stored_next_cursor"]
        while cursor:
            page = self.get(self.base + "conversations/?stored_cursor=" + cursor).json()
            self.assertIsNone(page["next_cursor"])
            seen += [i["ref"] for i in page["items"]]
            cursor = page["stored_next_cursor"]
        self.assertEqual(len(seen), total)
        self.assertEqual(len(set(seen)), total)  # no row twice, none skipped
        self.assertNotIn(BEN_WC_REF, seen)
        last = self.get(self.base + "conversations/").json()
        self.assertEqual(last["items"][0]["ref"], seen[0])

    def test_a_stored_cursor_is_checked_and_reads_only_this_sites_rows(self):
        for bad in ("x", "1_", "_5", "1-5", "n_x"):
            self.error(self.get(self.base + "conversations/?stored_cursor=" + bad), 422, "invalid")
        ben_first = self.get(self.ben_base + "conversations/", client=self.ben).json()
        self.assertIsNone(ben_first["stored_next_cursor"])
        page = self.get(self.base + "conversations/?stored_cursor=n_999999999").json()
        self.assertEqual(page["items"], [])
        self.assertNotIn(BEN_WC_REF, str(page))

    def test_pancake_down_keeps_the_stored_list(self):
        from apps.inquiries.tests.base import configure, http_error

        configure(self.site)
        with FakePancake(listing=http_error(503)):
            body = self.get(self.base + "conversations/").json()
        self.assertEqual([i["ref"] for i in body["items"]], [WC_REF])
        self.assertEqual(body["sources"]["pancake"], "provider_down")

    def test_without_stored_conversations_an_unconnected_pancake_still_answers_as_before(self):
        InquiryMessage.objects.filter(site=self.site).delete()
        InquiryConversation.objects.filter(site=self.site).delete()
        self.error(self.get(self.base + "conversations/"), 409, "not_connected")

    def test_the_thread_shows_the_messages_and_marks_nothing_read(self):
        body = self.get(self.base + f"conversations/{WC_REF}/").json()
        self.assertEqual([m["text"] for m in body["messages"]], ["SECRET CUSTOMER TEXT"])
        self.assertEqual(body["reply"], {"open": True, "reason": ""})
        self.assertEqual(body["channel"], "webchat")
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.unread)

    def test_another_sites_ref_and_a_missing_ref_are_the_same_404(self):
        for path in ("conversations/{r}/",):
            foreign = self.get(self.base + path.format(r=BEN_WC_REF))
            missing = self.get(self.base + path.format(r="wc_" + "9" * 24))
            self.error(foreign, 404, "not_found")
            self.assertEqual(foreign.content, missing.content)
        self.assertNotIn(b"Ben's visitor", foreign.content)

    def test_the_stored_list_shows_only_this_sites_rows(self):
        mine = self.get(self.base + "conversations/").json()["items"]
        theirs = self.get(self.ben_base + "conversations/", client=self.ben).json()["items"]
        self.assertEqual([i["ref"] for i in mine], [WC_REF])
        self.assertEqual([i["ref"] for i in theirs], [BEN_WC_REF])


class ReplyTests(StoredOwnerTestCase):
    def test_a_reply_is_stored_and_the_audit_row_keeps_length_and_code_only(self):
        body = self.reply().json()
        self.assertEqual(body["state"], "sent")
        message = InquiryMessage.objects.get(site=self.site, direction="out")
        self.assertEqual((message.body, message.sender_kind, message.delivery_state), (REPLY_TEXT, "owner", "sent"))
        row = InquiryReply.objects.get(site=self.site)
        self.assertEqual((row.state, row.body_length, row.message_id), ("sent", len(REPLY_TEXT), message.pk))
        self.assertNotIn(REPLY_TEXT, str(InquiryReply.objects.values().get(pk=row.pk)))
        self.conv.refresh_from_db()
        self.assertFalse(self.conv.unread)

    def test_the_same_key_twice_sends_once(self):
        self.reply()
        self.assertEqual(self.reply().json()["state"], "sent")
        self.assertEqual(InquiryMessage.objects.filter(site=self.site, direction="out").count(), 1)

    def test_a_key_used_for_another_conversation_is_a_conflict(self):
        other = make_stored(self.site, ref="wc_" + "7" * 24, name="Jun")
        self.reply()
        self.error(self.reply(ref=other.external_id), 409, "conflict")

    def test_a_closed_conversation_refuses_a_reply_until_reopened(self):
        self.send("post", self.base + f"conversations/{WC_REF}/close/")
        self.error(self.reply(), 409, "closed")
        self.assertFalse(InquiryReply.objects.exists())
        self.send("post", self.base + f"conversations/{WC_REF}/reopen/")
        self.assertEqual(self.reply().status_code, 200)

    def test_a_reply_under_another_sites_id_is_not_found_and_stores_nothing(self):
        response = self.reply(ref=BEN_WC_REF)
        self.error(response, 404, "not_found")
        self.assertFalse(InquiryReply.objects.exists())
        self.error(self.reply(ref=WC_REF, base=self.ben_base, client=self.ben), 404, "not_found")
        self.assertFalse(InquiryMessage.objects.filter(direction="out").exists())

    def test_text_rules_are_those_of_pancake_replies(self):
        self.error(self.reply(text="   "), 422, "invalid")
        self.error(self.reply(text="x" * 2001), 422, "invalid")
        self.error(self.reply(key="short"), 422, "invalid")


class StatusAndReadTests(StoredOwnerTestCase):
    def test_close_reopen_and_read(self):
        path = self.base + f"conversations/{WC_REF}/"
        self.assertEqual(self.send("post", path + "read/").json(), {"ref": WC_REF, "status": "open", "unread": False})
        self.assertEqual(self.send("post", path + "close/").json()["status"], "closed")
        self.assertEqual(self.send("post", path + "reopen/").json()["status"], "open")

    def test_reopening_next_to_another_open_conversation_of_the_same_visitor_is_a_conflict(self):
        InquiryConversation.objects.filter(pk=self.conv.pk).update(visitor_hash="h" * 64, status="closed")
        make_stored(self.site, ref="wc_" + "5" * 24, visitor_hash="h" * 64)
        self.error(self.send("post", self.base + f"conversations/{WC_REF}/reopen/"), 409, "conflict")

    def test_another_sites_conversation_cannot_be_closed_read_or_reopened(self):
        for action in ("close", "reopen", "read"):
            response = self.send("post", self.base + f"conversations/{BEN_WC_REF}/{action}/")
            self.error(response, 404, "not_found")
        self.ben_conv.refresh_from_db()
        self.assertEqual((self.ben_conv.status, self.ben_conv.unread), ("open", True))

    def test_a_pancake_ref_is_not_a_stored_conversation(self):
        self.error(self.send("post", self.base + f"conversations/{REF}/close/"), 404, "not_found")


class ChannelsTests(StoredOwnerTestCase):
    def test_the_owner_switches_chat_on_and_pauses_it(self):
        path = self.base + "channels/"
        self.assertFalse(self.get(path).json()["webchat_enabled"])
        body = self.send("put", path, {"webchat_enabled": True}).json()
        self.assertTrue(body["webchat_enabled"])
        until = (timezone.now() + timedelta(hours=2)).isoformat()
        self.assertIsNotNone(self.send("put", path, {"paused_until": until}).json()["paused_until"])
        self.assertTrue(InquiryChannelSettings.objects.get(site=self.site).webchat_enabled)
        self.assertIsNone(self.send("put", path, {"paused_until": None}).json()["paused_until"])

    def test_the_email_switch_shape_default_and_save(self):
        path = self.base + "channels/"
        body = self.get(path).json()
        self.assertIs(body["email_enabled"], False)
        self.assertIs(body["contact_inbox_available"], False)
        with override_settings(INQUIRY_CONTACT_INBOX_ENABLED=True):
            self.assertIs(self.get(path).json()["contact_inbox_available"], True)
        with override_settings(INQUIRY_CONTACT_INBOX_ENABLED=True, INQUIRIES_ENABLED=True):
            body = self.send("put", path, {"email_enabled": True}).json()
        self.assertIs(body["email_enabled"], True)
        self.assertTrue(InquiryChannelSettings.objects.get(site=self.site).email_enabled)
        # None / absent leaves it unchanged, and other fields do not reset it
        self.assertIs(self.send("put", path, {"email_enabled": None, "webchat_enabled": True}).json()["email_enabled"], True)
        self.assertIs(self.send("put", path, {"email_enabled": False}).json()["email_enabled"], False)
        self.assertEqual(self.send("put", path, {"email_enabled": "maybe"}).status_code, 400)

    def test_one_sites_email_switch_never_changes_the_others(self):
        self.send("put", self.base + "channels/", {"email_enabled": True})
        self.assertFalse(InquiryChannelSettings.objects.filter(site=self.ben_site, email_enabled=True).exists())
        self.error(self.get(self.ben_base + "channels/"), 404, "not_found")
        self.error(self.send("put", self.ben_base + "channels/", {"email_enabled": True}), 404, "not_found")
        self.assertFalse(InquiryChannelSettings.objects.filter(site=self.ben_site).exists())

    def test_a_pause_in_the_past_or_too_far_ahead_is_refused(self):
        path = self.base + "channels/"
        for when in (timezone.now() - timedelta(minutes=1), timezone.now() + timedelta(days=91)):
            self.error(self.send("put", path, {"paused_until": when.isoformat()}), 422, "invalid")

    def test_other_fields_are_refused_and_one_site_never_changes_the_others(self):
        path = self.base + "channels/"
        self.assertEqual(self.send("put", path, {"sms_enabled": True}).status_code, 400)
        self.send("put", path, {"webchat_enabled": True})
        self.assertFalse(InquiryChannelSettings.objects.filter(site=self.ben_site).exists())

    def test_a_foreign_site_id_is_the_404_of_a_missing_site(self):
        foreign = self.get(self.ben_base + "channels/")
        missing = self.get("/v1/sites/s_doesnotexist/inquiries/channels/")
        self.error(foreign, 404, "not_found")
        self.assertEqual(foreign.content, missing.content)


class SwitchTests(StoredOwnerTestCase):
    def walk(self):
        return [
            self.get(self.base + "channels/"),
            self.get(self.base + f"conversations/{WC_REF}/"),
            self.send("post", self.base + f"conversations/{WC_REF}/close/"),
            self.send("post", self.base + f"conversations/{WC_REF}/read/"),
            self.send("post", self.base + f"conversations/{WC_REF}/reopen/"),
            self.reply(),
        ]

    def test_switch_off_every_stored_route_is_404_and_changes_nothing(self):
        with override_settings(INQUIRIES_ENABLED=False):
            for response in self.walk():
                self.error(response, 404, "not_found")
        self.assertFalse(InquiryReply.objects.exists())

    def test_before_inquiries_0002_the_stored_routes_are_404_and_pancake_lists_as_before(self):
        from apps.inquiries.tests.base import configure

        configure(self.site)
        with mock.patch("apps.inquiries.api_owner.stored_tables_exist", return_value=False):
            for response in self.walk():
                self.error(response, 404, "not_found")
            with FakePancake():
                body = self.get(self.base + "conversations/").json()
        self.assertEqual(len(body["items"]), len(LIST_OK["conversations"]))
        self.assertNotIn("sources", body)


class DeployCheckTests(TestCase):
    def ids(self):
        return [m.id for m in run_checks(include_deployment_checks=True)]

    def test_chat_needs_the_inquiries_and_storefront_switches(self):
        with override_settings(INQUIRY_WEBCHAT_ENABLED=True, INQUIRIES_ENABLED=False, STOREFRONT_ENABLED=True):
            self.assertIn("inquiries.E002", self.ids())
        with override_settings(INQUIRY_WEBCHAT_ENABLED=True, INQUIRIES_ENABLED=True, STOREFRONT_ENABLED=False):
            self.assertIn("inquiries.E002", self.ids())
        with override_settings(INQUIRY_WEBCHAT_ENABLED=True, INQUIRIES_ENABLED=True, STOREFRONT_ENABLED=True):
            self.assertNotIn("inquiries.E002", self.ids())
        with override_settings(INQUIRY_WEBCHAT_ENABLED=False, INQUIRIES_ENABLED=False, STOREFRONT_ENABLED=False):
            self.assertNotIn("inquiries.E002", self.ids())

    def test_the_chat_switch_is_off_by_default_and_the_stored_tables_are_seen(self):
        from django.conf import settings

        self.assertFalse(settings.INQUIRY_WEBCHAT_ENABLED)
        self.assertTrue(stored_tables_exist())
        with mock.patch("apps.inquiries.switch.connection.introspection.table_names", return_value=["inquiries_inquiryconnection"]):
            self.assertFalse(stored_tables_exist())


# -- retention, disposal and erasure -------------------------------------------------------------

RULE = "never_verified_accounts"


@switched_on
@override_settings(RETENTION_ENABLED=True)
class StoredRetentionTests(DisposalTestCase):
    def setUp(self):
        super().setUp()
        self.mine = Site.objects.get(account=self.account)
        self.theirs = Site.objects.get(account=self.other)
        self.old = timezone.now() - inquiry_retention.MESSAGE_RETENTION - timedelta(days=1)
        self.fresh = timezone.now() - timedelta(days=10)

    def plant(self, site, ref, at, hash_="a" * 64, email="maria@visitor.test"):
        conversation = make_stored(site, ref=ref, at=at, email=email, visitor_hash=hash_)
        InquiryConversation.objects.filter(pk=conversation.pk).update(thread_token=ref[-20:])
        InquiryMessage.objects.create(
            site=site, conversation=conversation, direction="out", sender_kind="owner", body="Reply", created_at=at,
        )
        return conversation

    def test_the_constant_is_180_days(self):
        self.assertEqual(inquiry_retention.MESSAGE_RETENTION, timedelta(days=180))

    def test_old_messages_and_conversations_go_and_recent_ones_stay(self):
        old = self.plant(self.mine, "wc_" + "1" * 24, self.old)
        fresh = self.plant(self.mine, "wc_" + "2" * 24, self.fresh, hash_="b" * 64)
        theirs = self.plant(self.theirs, "wc_" + "3" * 24, self.old, hash_="c" * 64)
        retention.run_all(only=["inquiry_messages"])
        self.assertFalse(InquiryConversation.objects.filter(pk=old.pk).exists())
        self.assertFalse(InquiryConversation.objects.filter(pk=theirs.pk).exists())  # every site's, by age
        self.assertEqual(InquiryMessage.objects.filter(conversation=fresh).count(), 2)
        self.assertEqual(InquiryConversation.objects.get(pk=fresh.pk).contact_email, "maria@visitor.test")

    def test_dry_run_counts_equal_run_counts(self):
        self.plant(self.mine, "wc_" + "1" * 24, self.old)
        self.plant(self.theirs, "wc_" + "3" * 24, self.old, hash_="c" * 64)
        self.plant(self.mine, "wc_" + "2" * 24, self.fresh, hash_="b" * 64)
        dry = retention.run_all(only=["inquiry_messages"], dry_run=True).counts["inquiry_messages"]
        self.assertEqual(InquiryMessage.objects.count(), 6)
        ran = retention.run_all(only=["inquiry_messages"]).counts["inquiry_messages"]
        self.assertEqual((dry, ran), (6, 6))  # 4 messages and 2 conversations
        self.assertEqual(retention.run_all(only=["inquiry_messages"]).counts["inquiry_messages"], 0)

    def test_a_conversation_an_audit_row_points_at_is_blanked_not_deleted(self):
        old = self.plant(self.mine, "wc_" + "1" * 24, self.old)
        InquiryReply.objects.create(
            site=self.mine, conversation=old, author=self.account, idempotency_key="key-old-0001", body_length=5,
        )
        retention.run_all(only=["inquiry_messages"])
        old.refresh_from_db()
        self.assertEqual(
            (old.contact_name, old.contact_email, old.visitor_hash, old.thread_token, old.display_name, old.status),
            ("", "", "", "", "", "closed"),
        )
        self.assertFalse(InquiryMessage.objects.filter(conversation=old).exists())
        self.assertEqual(InquiryReply.objects.count(), 1)
        self.assertEqual(retention.run_all(only=["inquiry_messages"], dry_run=True).counts["inquiry_messages"], 0)

    def test_nothing_runs_with_the_retention_switch_off_or_before_0002(self):
        self.plant(self.mine, "wc_" + "1" * 24, self.old)
        with override_settings(RETENTION_ENABLED=False):
            retention.run_all(only=["inquiry_messages"])
        with mock.patch("apps.inquiries.retention.stored_tables_exist", return_value=False):
            retention.run_all(only=["inquiry_messages"])
        self.assertEqual(InquiryMessage.objects.count(), 2)

    def test_old_messages_are_deleted_even_while_the_inquiries_switch_is_off(self):
        # the rule only deletes: messages stored while the switch was on must not outlive 180 days
        old = self.plant(self.mine, "wc_" + "1" * 24, self.old)
        fresh = self.plant(self.mine, "wc_" + "2" * 24, self.fresh, hash_="b" * 64)
        with override_settings(INQUIRIES_ENABLED=False):
            retention.run_all(only=["inquiry_messages"])
        self.assertFalse(InquiryMessage.objects.filter(conversation=old).exists())
        self.assertFalse(InquiryConversation.objects.filter(pk=old.pk).exists())
        self.assertEqual(InquiryMessage.objects.filter(conversation=fresh).count(), 2)

    def test_disposal_clears_only_that_account_and_a_dry_run_changes_nothing(self):
        mine = self.plant(self.mine, "wc_" + "1" * 24, self.fresh)
        theirs = self.plant(self.theirs, "wc_" + "3" * 24, self.fresh, hash_="c" * 64)
        chat_on(self.mine)
        chat_on(self.theirs)
        dry = account_rules.dispose_account(self.account, now=self.now, rule=RULE, dry_run=True)
        self.assertEqual(InquiryMessage.objects.count(), 4)
        counts = account_rules.dispose_account(self.account, now=self.now, rule=RULE)
        for key in ("inquiry_conversations", "inquiry_messages", "inquiry_channel_settings"):
            self.assertEqual(counts[key], dry[key], key)
        self.assertEqual(counts["inquiry_messages"], 2)
        mine.refresh_from_db()
        self.assertEqual((mine.contact_name, mine.contact_email, mine.visitor_hash, mine.thread_token), ("",) * 4)
        self.assertFalse(InquiryMessage.objects.filter(site=self.mine).exists())
        settings_row = InquiryChannelSettings.objects.get(site=self.mine)
        self.assertEqual((settings_row.email_route_token, settings_row.webchat_enabled), ("", False))
        theirs.refresh_from_db()
        self.assertEqual(theirs.contact_email, "maria@visitor.test")
        self.assertEqual(InquiryMessage.objects.filter(site=self.theirs).count(), 2)
        self.assertTrue(InquiryChannelSettings.objects.get(site=self.theirs).webchat_enabled)
        self.assertNotEqual(InquiryChannelSettings.objects.get(site=self.theirs).email_route_token, "")

    def test_disposal_runs_with_the_switches_off(self):
        self.plant(self.mine, "wc_" + "1" * 24, self.fresh)
        with override_settings(INQUIRIES_ENABLED=False, INQUIRY_WEBCHAT_ENABLED=False):
            account_rules.dispose_account(self.account, now=self.now, rule=RULE)
        self.assertFalse(InquiryMessage.objects.filter(site=self.mine).exists())

    def test_before_inquiries_0002_disposal_still_blanks_the_names_and_reads_no_new_table(self):
        make = InquiryConversation.objects.create(
            site=self.mine, provider="pancake", external_id="p_1", display_name="Maria Santos",
        )
        with mock.patch("apps.inquiries.retention.stored_tables_exist", return_value=False):
            counts = inquiry_retention.blank_inquiries_of(self.account)
        self.assertEqual(counts, {"inquiry_connections": 0, "inquiry_conversations": 1})
        make.refresh_from_db()
        self.assertEqual(make.display_name, "")
