"""T-GW: the three calls the phone makes."""
import json
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.notifications import gateway, sms
from apps.notifications.models import SmsDevice, SmsMessage
from apps.notifications.tests.base import (
    CODE_TEXT, MOBILE, STATUS_TEXT, SmsTestCase, fallback, make_phone, set_policy,
)
from apps.operators.tests.base import run_now

P = SmsMessage.Priority
S = SmsMessage.Status
TOKEN = "token-of-the-system-phone"
UNAUTHENTICATED = {"error": {"code": "unauthenticated", "message": "Unknown phone."}}


class GatewayCase(SmsTestCase):
    def setUp(self):
        super().setUp()
        self.phone = make_phone("system", TOKEN)
        patcher = mock.patch("apps.operators.background.run_later", side_effect=run_now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, name, body=None, token=TOKEN, ip="203.0.113.7"):
        headers = {"HTTP_X_REAL_IP": ip}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(
            f"/gateway/v1/{name}", data=json.dumps(body or {}), content_type="application/json",
            secure=True, **headers,
        )

    def poll(self, **fields):
        response = self.call("poll", fields.pop("body", None), **fields)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def report(self, *reports, **fields):
        return self.call("report", {"reports": list(reports)}, **fields)

    def queue(self, priority=P.CODE, text=CODE_TEXT, age=0, ttl=600, with_fallback=False, **fields):
        """A waiting text, written the way send_sms leaves it."""
        category = "code" if priority == P.CODE else "status"
        message = SmsMessage.objects.create(
            recipient=MOBILE, category=category, purpose=fields.pop("purpose", "test"),
            priority=priority, expires_at=timezone.now() + timedelta(seconds=ttl), **fields,
        )
        if age:
            created = timezone.now() - timedelta(seconds=age)
            SmsMessage.objects.filter(pk=message.pk).update(created_at=created)
        sms.hold_text(message, text, max(ttl, 1))
        if with_fallback:
            sms.hold_fallback(message, fallback(), max(ttl, 1))
        return message

    def state(self, message):
        message.refresh_from_db()
        return message.status, message.reason


class GatewayIdentityTests(GatewayCase):
    def test_1_no_token_a_wrong_token_a_rotated_token_and_a_disabled_phone_get_the_same_401(self):
        make_phone("old", "token-before-rotation")
        SmsDevice.objects.filter(name="old").update(token_hash=sms.token_hash("token-after"))
        make_phone("stopped", "token-of-a-disabled-phone", is_active=False)
        self.queue()

        tries = (None, "wrong", "token-before-rotation", "token-of-a-disabled-phone", "")
        for name in ("poll", "report", "received"):
            for number, token in enumerate(tries):
                with self.subTest(call=name, token=token):
                    response = self.call(name, token=token, ip=f"198.51.100.{number}")
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json(), UNAUTHENTICATED)
        self.assertEqual(SmsMessage.objects.get().status, S.QUEUED)

    def test_1_a_token_without_the_bearer_word_is_refused(self):
        response = self.client.post(
            "/gateway/v1/poll", data="{}", content_type="application/json", secure=True,
            HTTP_AUTHORIZATION=TOKEN,
        )
        self.assertEqual(response.status_code, 401)

    def test_2_failed_tries_are_limited_per_ip(self):
        for _ in range(gateway.FAILED_TRIES):
            self.assertEqual(self.call("poll", token="wrong").status_code, 401)

        self.assertEqual(self.call("poll", token="wrong").status_code, 429)
        self.assertEqual(self.call("poll").status_code, 429)  # the token is not looked at
        self.assertEqual(self.call("poll", ip="203.0.113.8").status_code, 200)

    def test_2_a_good_call_does_not_count_as_a_failed_try(self):
        for _ in range(gateway.FAILED_TRIES + 2):
            self.assertEqual(self.call("poll").status_code, 200)

    def test_only_post_is_answered(self):
        for name in ("poll", "report", "received"):
            response = self.client.get(f"/gateway/v1/{name}", secure=True)
            self.assertEqual(response.status_code, 405)

    def test_a_body_that_is_not_an_object_is_refused(self):
        for name in ("poll", "report"):
            response = self.client.post(
                f"/gateway/v1/{name}", data="[1", content_type="application/json", secure=True,
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            )
            self.assertEqual(response.status_code, 400)

    def test_12_gateway_off_answers_404_to_all_three(self):
        with override_settings(SMS_GATEWAY_ENABLED=False):
            for name in ("poll", "report", "received"):
                with self.assertNumQueries(0):
                    self.assertEqual(self.call(name).status_code, 404)

    def test_15_the_calls_are_not_in_the_published_api(self):
        paths = self.client.get("/v1/openapi.json", secure=True).json()["paths"]
        self.assertFalse([path for path in paths if "gateway" in path or "poll" in path])
        self.assertEqual(self.client.post("/v1/gateway/v1/poll", secure=True).status_code, 404)


class PollTests(GatewayCase):
    def test_3_poll_hands_out_at_most_5_oldest_first_and_marks_them_dispatched(self):
        waiting = [self.queue(age=60 - number, text=f"Text {number}") for number in range(7)]

        answer = self.poll()

        self.assertEqual([m["id"] for m in answer["messages"]], [m.public_id for m in waiting[:5]])
        first = answer["messages"][0]
        self.assertEqual((first["to"], first["text"], first["gap"]), (MOBILE, "Text 0", 0))
        self.assertEqual(
            set(first), {"id", "to", "text", "expires_at", "gap"}
        )
        for message in waiting[:5]:
            message.refresh_from_db()
            self.assertEqual((message.status, message.device), (S.DISPATCHED, self.phone))
            self.assertIsNotNone(message.dispatched_at)
        self.assertEqual(self.state(waiting[5]), (S.QUEUED, ""))

    def test_4_a_second_poll_does_not_hand_the_same_text_out_again(self):
        self.queue()
        self.assertEqual(len(self.poll()["messages"]), 1)
        self.assertEqual(self.poll()["messages"], [])

    def test_4_a_second_claim_of_the_same_record_gets_nothing(self):
        message = self.queue()
        now = timezone.now()
        with mock.patch.object(gateway, "_waiting", return_value=[message]):
            first = gateway._hand_out(self.phone, now)
            second = gateway._hand_out(self.phone, now)  # as a poll that read the queue too early

        self.assertEqual([m["id"] for m in first], [message.public_id])
        self.assertEqual(second, [])
        self.assertEqual(self.state(message), (S.DISPATCHED, ""))

    def test_5_the_text_is_gone_from_the_cache_after_handout(self):
        message = self.queue()
        self.poll()
        self.assertIsNone(cache.get(sms.text_key(message)))

    def test_6_an_expired_text_is_never_handed_out_and_becomes_expired(self):
        message = self.queue(ttl=-1)
        with self.assertLogs("apps.notifications", level="ERROR"):
            answer = self.poll()

        self.assertEqual(answer["messages"], [])
        self.assertEqual(self.state(message), (S.EXPIRED, ""))
        self.assertIsNone(cache.get(sms.text_key(message)))

    def test_7_a_missing_cache_entry_gives_failed_text_lost(self):
        message = self.queue()
        cache.delete(sms.text_key(message))
        with self.assertLogs("apps.notifications", level="ERROR"):
            answer = self.poll()

        self.assertEqual(answer["messages"], [])
        self.assertEqual(self.state(message), (S.FAILED, "text_lost"))

    def test_10_no_report_after_10_minutes_gives_failed_no_report(self):
        message = self.queue()
        self.poll()
        SmsMessage.objects.update(dispatched_at=timezone.now() - timedelta(minutes=9))
        self.poll()
        self.assertEqual(self.state(message), (S.DISPATCHED, ""))

        SmsMessage.objects.update(dispatched_at=timezone.now() - timedelta(minutes=10, seconds=1))
        with self.assertLogs("apps.notifications", level="ERROR"):
            self.poll()
        self.assertEqual(self.state(message), (S.FAILED, "no_report"))

    def test_11_an_expired_text_with_a_fallback_is_emailed_once(self):
        message = self.queue(P.STATUS, STATUS_TEXT, ttl=-1, with_fallback=True)

        self.poll()
        self.poll()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["buyer@example.test"])
        message.refresh_from_db()
        self.assertEqual((message.status, message.emailed), (S.EXPIRED, True))

    def test_11_a_text_with_no_report_and_a_lost_text_are_emailed_once(self):
        unreported = self.queue(P.STATUS, STATUS_TEXT, with_fallback=True)
        self.poll()
        SmsMessage.objects.update(dispatched_at=timezone.now() - timedelta(minutes=11))
        lost = self.queue(P.STATUS, STATUS_TEXT, with_fallback=True)
        cache.delete(sms.text_key(lost))

        self.poll()
        self.poll()

        self.assertEqual(len(mail.outbox), 2)
        for message in (unreported, lost):
            message.refresh_from_db()
            self.assertEqual((message.status, message.emailed), (S.FAILED, True))

    def test_13_sending_stopped_hands_out_nothing_and_slows_the_phone(self):
        message = self.queue()
        self.assertEqual(self.poll()["wait"], gateway.WAIT)
        waiting = self.queue()
        set_policy(sending_stopped=True)

        answer = self.poll()

        self.assertEqual((answer["messages"], answer["wait"]), ([], gateway.WAIT_STOPPED))
        self.assertGreater(gateway.WAIT_STOPPED, gateway.WAIT)
        self.assertEqual(self.state(waiting), (S.QUEUED, ""))
        self.assertEqual(self.state(message)[0], S.DISPATCHED)

    def test_14_poll_updates_last_contact_and_presence(self):
        SmsDevice.objects.update(last_contact_at=None)
        self.assertFalse(sms.gateway_online())

        self.poll(body={"app_version": "1.2.0", "battery": 87, "charging": True})

        self.phone.refresh_from_db()
        self.assertTrue(sms.gateway_online())
        self.assertAlmostEqual(self.phone.last_contact_at, timezone.now(), delta=timedelta(seconds=5))
        self.assertEqual(
            (self.phone.app_version, self.phone.battery_percent, self.phone.is_charging),
            ("1.2.0", 87, True),
        )

    def test_14_odd_values_from_the_phone_are_not_stored(self):
        self.poll(body={"app_version": "x" * 200, "battery": 900, "charging": "yes"})
        self.phone.refresh_from_db()
        self.assertEqual(
            (self.phone.app_version, self.phone.battery_percent, self.phone.is_charging),
            ("x" * 20, None, None),
        )

    def test_16_poll_answers_the_incoming_switch_off_for_a_new_phone(self):
        self.assertIs(self.poll()["incoming"], False)
        SmsDevice.objects.update(forwards_incoming=True)
        self.assertIs(self.poll()["incoming"], True)

    def test_19_codes_then_replies_then_status_texts_then_marketing(self):
        marketing = self.queue(P.MARKETING, "Sale", age=40)
        status = self.queue(P.STATUS, STATUS_TEXT, age=30)
        reply = self.queue(P.REPLY, "Yes we are open", age=20)
        code = self.queue(P.CODE, CODE_TEXT, age=10)

        ids = [m["id"] for m in self.poll()["messages"]]

        self.assertEqual(ids, [m.public_id for m in (code, reply, status, marketing)])

    def test_19_a_code_queued_after_many_marketing_texts_is_in_the_next_answer(self):
        for number in range(8):
            self.queue(P.MARKETING, "Sale", age=100 - number)
        code = self.queue(P.CODE)
        self.assertEqual(self.poll()["messages"][0]["id"], code.public_id)

    def test_20_every_text_carries_a_gap(self):
        self.queue(P.CODE)
        self.queue(P.REPLY, "Yes")
        self.queue(P.STATUS, STATUS_TEXT)
        gaps = set()
        for _ in range(30):
            marketing = self.queue(P.MARKETING, "Sale")
            by_id = {m["id"]: m["gap"] for m in self.poll()["messages"]}
            gaps.add(by_id.pop(marketing.public_id))
            self.assertEqual(set(by_id.values()) - {0}, set())
            self.report({"id": marketing.public_id, "status": "sent"})

        self.assertTrue(all(isinstance(gap, int) and 18 <= gap <= 25 for gap in gaps), gaps)
        self.assertGreater(len(gaps), 1)

    def test_21_one_paced_text_at_a_time_and_codes_still_go(self):
        first = self.queue(P.MARKETING, "Sale", age=30)
        second = self.queue(P.MARKETING, "Sale", age=20)

        self.assertEqual([m["id"] for m in self.poll()["messages"]], [first.public_id])
        code = self.queue(P.CODE)
        self.assertEqual([m["id"] for m in self.poll()["messages"]], [code.public_id])
        self.assertEqual(self.state(second), (S.QUEUED, ""))

        self.report({"id": first.public_id, "status": "sent"})
        self.assertEqual([m["id"] for m in self.poll()["messages"]], [second.public_id])

    def test_22_a_full_answer_tells_the_phone_to_ask_again_at_once(self):
        for _ in range(6):
            self.queue()
        self.assertEqual(self.poll()["wait"], 0)
        self.assertEqual(self.poll()["wait"], gateway.WAIT)

    def test_a_second_phone_is_handed_nothing(self):
        make_phone("spare", "token-of-the-spare-phone")
        message = self.queue()

        self.assertEqual(self.poll(token="token-of-the-spare-phone")["messages"], [])
        self.assertEqual(self.state(message), (S.QUEUED, ""))
        self.assertEqual(len(self.poll()["messages"]), 1)

    def test_no_log_line_holds_a_number_or_a_text(self):
        with self.assertLogs(level="DEBUG") as logs:
            self.queue(ttl=-1)
            lost = self.queue()
            cache.delete(sms.text_key(lost))
            sent = self.queue(text="Your NegosyoOnline code is 804215.")
            self.poll()
            self.report({"id": sent.public_id, "status": "failed", "reason": f"to {MOBILE} 804215"})
            self.call("poll", token="wrong")

        everything = "\n".join(logs.output)
        self.assertIn("sms_not_sent", everything)
        for private in (MOBILE, "9171234567", "804215", "NegosyoOnline code", TOKEN):
            self.assertNotIn(private, everything)


class ReportTests(GatewayCase):
    def test_8_report_sets_sent_and_repeating_it_changes_nothing(self):
        message = self.queue()
        self.poll()

        response = self.report({"id": message.public_id, "status": "sent"})

        self.assertEqual((response.status_code, response.content), (204, b""))
        message.refresh_from_db()
        self.assertEqual(message.status, S.SENT)
        sent_at = message.sent_at
        self.assertIsNotNone(sent_at)

        again = self.report({"id": message.public_id, "status": "failed", "reason": "late"})
        self.assertEqual(again.status_code, 204)
        message.refresh_from_db()
        self.assertEqual((message.status, message.reason, message.sent_at), (S.SENT, "", sent_at))

    def test_8_report_sets_failed_with_a_short_reason(self):
        message = self.queue()
        self.poll()
        with self.assertLogs("apps.notifications", level="ERROR"):
            response = self.report(
                {"id": message.public_id, "status": "failed", "reason": "No Service!"}
            )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.state(message), (S.FAILED, "no_service"))

    def test_8_the_phones_reason_can_never_carry_a_number_or_a_text(self):
        message = self.queue()
        self.poll()
        with self.assertLogs("apps.notifications", level="ERROR"):
            self.report(
                {"id": message.public_id, "status": "failed", "reason": f"{MOBILE} code 123456 " * 9}
            )

        status, reason = self.state(message)
        self.assertEqual(status, S.FAILED)
        self.assertLessEqual(len(reason), 40)
        self.assertFalse(any(character.isdigit() for character in reason))

    def test_8_a_failed_text_with_no_reason_says_so(self):
        message = self.queue()
        self.poll()
        with self.assertLogs("apps.notifications", level="ERROR"):
            self.report({"id": message.public_id, "status": "failed"})
        self.assertEqual(self.state(message), (S.FAILED, "phone_failed"))

    def test_8_a_text_that_was_not_handed_out_cannot_be_reported(self):
        message = self.queue()
        self.report({"id": message.public_id, "status": "sent"})
        self.assertEqual(self.state(message), (S.QUEUED, ""))

    def test_9_a_report_for_another_phones_text_is_refused(self):
        make_phone("spare", "token-of-the-spare-phone")
        mine = self.queue()
        theirs = self.queue()
        self.poll()

        response = self.report(
            {"id": mine.public_id, "status": "sent"}, {"id": theirs.public_id, "status": "sent"},
            token="token-of-the-spare-phone",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.state(mine), (S.DISPATCHED, ""))
        unknown = self.report({"id": "sms_00000000000000000000", "status": "sent"})
        self.assertEqual(unknown.status_code, 403)

    def test_a_report_that_is_not_in_the_agreed_shape_is_refused_whole(self):
        message = self.queue()
        self.poll()
        bad = (
            {"reports": "sent"},
            {"reports": [{"id": message.public_id, "status": "delivered"}]},
            {"reports": [{"id": message.public_id, "status": "sent"}, "x"]},
            {"reports": [{"id": message.public_id, "status": "sent"}] * 101},
        )
        for body in bad:
            with self.subTest(body=str(body)[:60]):
                self.assertEqual(self.call("report", body).status_code, 400)
        self.assertEqual(self.state(message), (S.DISPATCHED, ""))

    def test_11_a_failed_text_with_a_fallback_is_emailed_once(self):
        message = self.queue(P.STATUS, STATUS_TEXT, with_fallback=True)
        self.poll()

        for _ in range(2):
            self.report({"id": message.public_id, "status": "failed", "reason": "no service"})

        self.assertEqual(len(mail.outbox), 1)
        message.refresh_from_db()
        self.assertEqual((message.status, message.emailed), (S.FAILED, True))

    def test_a_sent_text_drops_its_fallback(self):
        message = self.queue(P.STATUS, STATUS_TEXT, with_fallback=True)
        self.poll()
        self.report({"id": message.public_id, "status": "sent"})

        self.assertIsNone(cache.get(sms.fallback_key(message)))
        self.assertEqual(mail.outbox, [])


class ReceivedTests(GatewayCase):
    TEXTS = {
        "messages": [
            {
                "id": "in_1", "from": "+639189990000", "text": "Is my order ready?",
                "received_at": "2026-11-11T09:00:00+08:00", "sim_slot": 0,
            }
        ]
    }

    def assert_nothing_kept(self, body):
        before = {model: model.objects.count() for model in (SmsMessage, SmsDevice)}
        with mock.patch.object(gateway, "cache", wraps=cache) as gateway_cache:
            with mock.patch.object(sms, "cache", wraps=cache) as sms_cache:
                with self.assertNoLogs(level="DEBUG"):
                    response = self.call("received", body)
        self.assertEqual((response.status_code, response.content), (204, b""))
        for spied in (gateway_cache, sms_cache):
            for write in ("set", "add", "incr", "set_many"):
                getattr(spied, write).assert_not_called()
        self.assertEqual(before, {model: model.objects.count() for model in before})

    def test_17_received_answers_204_and_keeps_nothing_with_the_switch_off(self):
        self.assert_nothing_kept(self.TEXTS)

    def test_17_received_answers_204_and_keeps_nothing_with_the_switch_on(self):
        SmsDevice.objects.update(forwards_incoming=True)
        self.assert_nothing_kept(self.TEXTS)

    def test_17_received_does_not_count_as_a_poll(self):
        SmsDevice.objects.update(last_contact_at=None)
        self.call("received", self.TEXTS)
        self.assertIsNone(SmsDevice.objects.get().last_contact_at)

    def test_18_received_without_a_good_token_gets_the_same_401(self):
        for token in (None, "wrong"):
            response = self.call("received", self.TEXTS, token=token)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), UNAUTHENTICATED)

    def test_23_received_sent_twice_with_the_same_ids_answers_204_both_times(self):
        for _ in range(2):
            self.assertEqual(self.call("received", self.TEXTS).status_code, 204)

    def test_received_takes_anything_the_phone_sends(self):
        for raw in ("", "not json", json.dumps({"messages": [{}] * 50})):
            response = self.client.post(
                "/gateway/v1/received", data=raw, content_type="application/json", secure=True,
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            )
            self.assertEqual(response.status_code, 204)
