"""The owner's own Xendit key (Coach Ed, 2026-11-18): saved encrypted, never returned, checked with
one read at Xendit first; the two switches; the client; the deploy check. A fake Xendit, no network."""
import json
import urllib.error
from io import BytesIO
from unittest import mock

from cryptography.fernet import Fernet
from django.core import checks
from django.test import SimpleTestCase, override_settings

from apps.accounts.models import AccountAuditEvent
from apps.payments import crypto, fake_xendit, services, xendit_client
from apps.payments.models import SitePaymentSettings, SiteXenditCredential
from apps.payments.tests.base import PaymentsApiTestCase, methods_url
from apps.sante.tests.base import retemplate

FERNET = Fernet.generate_key().decode()  # made for this test run
KEY, TOKEN = "xnd_development_secretkeyvalue1", "whsec-token-value-1"
xendit_on = override_settings(XENDIT_ENABLED=True, XENDIT_FAKE=True, DEBUG=True, PAYMENT_CREDENTIALS_KEY=FERNET)
NOT_SET_UP = {
    "offered": True, "configured": False, "key_mode": "", "webhook_url": "", "key_failed": False,
    "key_failed_at": None,
}


class XenditApiCase(PaymentsApiTestCase):
    def setUp(self):
        super().setUp()
        fake_xendit.reset()
        self.xurl = f"{self.url}xendit/"

    def put(self, api_key=KEY, token=TOKEN, url=None, client=None, **extra):
        return self.send("put", url or self.xurl, {"api_key": api_key, "webhook_token": token, **extra}, client=client)

    def saved(self, **kw):
        response = self.put(**kw)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


@xendit_on
class ReadTests(XenditApiCase):
    def test_offered_before_any_key_the_block_says_not_set_up_with_the_offered_flag(self):
        body = self.read()
        self.assertEqual(body["xendit"], NOT_SET_UP)
        self.assertEqual(body["enabled"], {"direct": True, "xendit": False})
        self.assertEqual(body["available"], {"direct": False, "xendit": False})

    @override_settings(XENDIT_ENABLED=False)
    def test_with_the_platform_switch_off_the_body_is_exactly_what_it_was(self):
        body = self.read()
        self.assertEqual(body["xendit"], {
            "configured": False, "key_mode": "", "webhook_url": "", "key_failed": False, "key_failed_at": None,
        })
        self.assertNotIn("offered", body["xendit"])
        self.assertEqual(body["enabled"], {"direct": True, "xendit": False})
        self.assertEqual(body["available"], {"direct": False, "xendit": False})

    def test_a_site_that_is_not_a_sante_site_reads_as_before(self):
        retemplate(self.site)
        body = self.read()
        self.assertNotIn("offered", body["xendit"])
        self.assertFalse(body["xendit"]["configured"])

    def test_the_read_after_a_save_has_the_mode_the_url_and_never_a_secret(self):
        body = self.saved()
        block = body["xendit"]
        self.assertEqual((block["offered"], block["configured"], block["key_mode"]), (True, True, "test"))
        self.assertEqual((block["key_failed"], block["key_failed_at"]), (False, None))
        row = SiteXenditCredential.objects.get(site=self.site)
        self.assertEqual(block["webhook_url"], f"https://testserver/v1/webhooks/xendit/{row.webhook_key}/")
        text = json.dumps(self.read())
        for secret in (KEY, TOKEN, row.api_key_ciphertext, row.webhook_token_ciphertext):
            self.assertNotIn(secret, text)

    def test_webhook_key_is_random_unique_and_stable_across_a_resave_and_a_remove(self):
        first = SiteXenditCredential.objects.get(site=self.saved() and self.site).webhook_key
        self.assertGreaterEqual(len(first), 40)
        self.saved(api_key="xnd_development_anotherkey2")
        self.assertEqual(SiteXenditCredential.objects.get(site=self.site).webhook_key, first)
        self.delete(self.xurl)
        self.assertEqual(SiteXenditCredential.objects.get(site=self.site).webhook_key, first)
        self.assertIn(first, self.read()["xendit"]["webhook_url"])
        client, theirs = self.ben_with_site()
        self.send("put", f"{methods_url(theirs.public_id)}xendit/", {"api_key": KEY, "webhook_token": TOKEN}, client=client)
        self.assertNotEqual(SiteXenditCredential.objects.get(site=theirs).webhook_key, first)


@xendit_on
class SaveTests(XenditApiCase):
    def test_a_good_key_is_checked_at_xendit_then_stored_encrypted_and_not_switched_on(self):
        body = self.saved()
        self.assertEqual(fake_xendit.calls, [("GET", "/balance")])
        row = SiteXenditCredential.objects.get(site=self.site)
        for ciphertext, plain in ((row.api_key_ciphertext, KEY), (row.webhook_token_ciphertext, TOKEN)):
            self.assertNotIn(plain, ciphertext)
            self.assertEqual(crypto.decrypt(ciphertext), plain)
        self.assertEqual(row.key_mode, "test")
        self.assertEqual(body["enabled"]["xendit"], False)
        self.assertEqual(body["available"]["xendit"], False)

    def test_the_secrets_reach_no_audit_record(self):
        self.saved()
        events = list(AccountAuditEvent.objects.values_list("event", "detail"))
        self.assertIn("xendit_key_saved", [e for e, _ in events])
        self.assertNotIn(KEY, json.dumps(events))
        self.assertNotIn(TOKEN, json.dumps(events))

    def test_each_bad_key_is_refused_with_its_own_message_and_nothing_is_stored(self):
        cases = [
            ("not a key", "Enter a valid Xendit secret key."),
            ("", "Enter a valid Xendit secret key."),
            ("xnd_public_development_abc", "This is your Xendit public key. Use your secret key, from Xendit Dashboard, Settings, API Keys."),
            ("xnd_development_rejected", "Xendit did not accept this key."),
        ]
        for key, message in cases:
            with self.subTest(key=key):
                self.assertEqual(self.fields(self.put(api_key=key))["api_key"], [message])
        self.assertFalse(SiteXenditCredential.objects.exists())

    def test_a_missing_token_is_refused(self):
        for token in ("", "   ", "x" * 201):
            with self.subTest(token=token):
                self.assertEqual(self.fields(self.put(token=token))["webhook_token"], ["Enter the webhook verification token."])
        self.assertFalse(SiteXenditCredential.objects.exists())

    def test_xendit_not_reachable_is_503_and_nothing_is_stored(self):
        response = self.put(api_key="xnd_development_down")
        self.assertEqual(self.error(response, 503, "xendit_unavailable")["message"], "Xendit is not reachable right now. Try again shortly.")
        self.assertFalse(SiteXenditCredential.objects.exists())

    def test_without_an_encryption_key_it_is_503_and_xendit_is_not_even_called(self):
        with override_settings(PAYMENT_CREDENTIALS_KEY=""):
            response = self.put()
        self.assertEqual(self.error(response, 503, "payments_unavailable")["message"], "Payment settings are not available yet.")
        self.assertEqual(fake_xendit.calls, [])

    def test_extra_keys_and_non_strings_are_refused(self):
        self.error(self.put(site="x"), 400, "invalid_request")
        self.error(self.send("put", self.xurl, {"api_key": 5, "webhook_token": TOKEN}), 400, "invalid_request")
        self.error(self.send("put", self.xurl, {"api_key": KEY}), 400, "invalid_request")

    @override_settings(RAILWAY_ENVIRONMENT_NAME="staging")
    def test_staging_takes_test_keys_only(self):
        self.assertEqual(self.fields(self.put(api_key="xnd_production_livekey1"))["api_key"], [services.KEY_TEST_ONLY])
        self.assertEqual(fake_xendit.calls, [])
        self.saved()

    def test_staging_live_key_for_a_listed_site_only(self):
        live = "xnd_production_livekey1"
        pid = self.site.public_id
        with override_settings(RAILWAY_ENVIRONMENT_NAME="staging", XENDIT_LIVE_KEY_SITES=(pid,)):
            self.assertEqual(self.saved(api_key=live)["xendit"]["key_mode"], "live")
            self.assertEqual(self.saved()["xendit"]["key_mode"], "test")
        for listed in ((), ("site_other",)):
            with self.subTest(listed=listed), override_settings(RAILWAY_ENVIRONMENT_NAME="staging", XENDIT_LIVE_KEY_SITES=listed):
                self.assertEqual(self.fields(self.put(api_key=live))["api_key"], [services.KEY_TEST_ONLY])

    @override_settings(RAILWAY_ENVIRONMENT_NAME="staging")
    def test_whitespace_and_blank_entries_in_the_list_are_ignored(self):
        from config.settings import parse_site_id_list
        pid = self.site.public_id
        self.assertEqual(parse_site_id_list(f" , {pid} ,, x "), (pid, "x"))
        self.assertEqual(parse_site_id_list(" , ,"), ())
        self.assertEqual(parse_site_id_list(""), ())
        with override_settings(XENDIT_LIVE_KEY_SITES=parse_site_id_list(" ,, ")):
            self.assertFalse(services.environment_allows(services.LIVE, self.site))
        with override_settings(XENDIT_LIVE_KEY_SITES=parse_site_id_list(f"  ,{pid} ")):
            self.assertTrue(services.environment_allows(services.LIVE, self.site))

    @override_settings(RAILWAY_ENVIRONMENT_NAME="production", XENDIT_LIVE_KEY_SITES=())
    def test_production_is_unchanged_by_the_list(self):
        with override_settings(XENDIT_LIVE_KEY_SITES=("*", self.site.public_id)):
            self.assertTrue(services.environment_allows(services.LIVE, self.site))
            self.assertFalse(services.environment_allows(services.TEST, self.site))

    @override_settings(RAILWAY_ENVIRONMENT_NAME="", DEBUG=False)
    def test_the_list_does_not_open_a_run_without_an_environment_name(self):
        with override_settings(XENDIT_LIVE_KEY_SITES=(self.site.public_id,)):
            self.assertFalse(services.environment_allows(services.LIVE, self.site))

    @override_settings(RAILWAY_ENVIRONMENT_NAME="", DEBUG=False)
    def test_no_environment_name_and_no_debug_refuses_either_key(self):
        for key in (KEY, "xnd_production_livekey1"):
            with self.subTest(key=key):
                self.assertEqual(self.put(api_key=key).status_code, 400)
        self.assertEqual(fake_xendit.calls, [])
        self.assertFalse(SiteXenditCredential.objects.exists())

    @override_settings(RAILWAY_ENVIRONMENT_NAME="", DEBUG=True)
    def test_no_environment_name_with_debug_takes_either_key(self):
        self.saved()
        self.assertEqual(self.saved(api_key="xnd_production_livekey1")["xendit"]["key_mode"], "live")

    @override_settings(RAILWAY_ENVIRONMENT_NAME="production")
    def test_production_takes_live_keys_only(self):
        self.assertEqual(self.fields(self.put())["api_key"], [services.KEY_LIVE_ONLY])
        self.assertEqual(self.saved(api_key="xnd_production_livekey1")["xendit"]["key_mode"], "live")

    def test_a_new_key_clears_a_failure(self):
        self.saved()
        SiteXenditCredential.objects.update(key_failed_at=SiteXenditCredential.objects.get().updated_at)
        self.assertTrue(self.read()["xendit"]["key_failed"])
        self.assertFalse(self.saved()["xendit"]["key_failed"])

    def test_the_writes_are_throttled_with_the_others(self):
        statuses = [self.put().status_code for _ in range(21)]
        self.assertEqual(statuses[:20], [200] * 20)
        self.assertEqual(statuses[20], 429)

    @override_settings(XENDIT_ENABLED=False)
    def test_while_the_platform_switch_is_off_put_and_delete_are_404_and_store_nothing(self):
        self.error(self.put(), 404, "not_found")
        self.error(self.delete(self.xurl), 404, "not_found")
        self.assertFalse(SiteXenditCredential.objects.exists())
        self.assertEqual(fake_xendit.calls, [])

    def test_a_site_that_is_not_a_sante_site_gets_404(self):
        retemplate(self.site)
        self.error(self.put(), 404, "not_found")
        self.error(self.delete(self.xurl), 404, "not_found")

    def test_another_accounts_site_and_a_missing_site_answer_the_same_404(self):
        ben, theirs = self.ben_with_site()
        reference = self.put(url=f"{methods_url('site_00000000000000000000')}xendit/").json()
        response = self.put(url=f"{methods_url(theirs.public_id)}xendit/")
        self.assertEqual((response.status_code, response.json()), (404, reference))
        self.assertFalse(SiteXenditCredential.objects.exists())

    def test_signed_out_is_401(self):
        from django.test import Client
        response = self.put(client=Client())
        self.assertEqual(response.status_code, 401, response.content)
        self.assertEqual(self.send("delete", self.xurl, client=Client()).status_code, 401)


@xendit_on
class RemoveTests(XenditApiCase):
    def test_remove_blanks_the_key_only_switches_it_off_and_keeps_the_url_and_token(self):
        self.saved()
        self.patch(self.url, {"xendit_enabled": True})
        row = SiteXenditCredential.objects.get(site=self.site)
        key = row.webhook_key
        response = self.delete(self.xurl)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["awaiting_xendit_orders"], 0)
        self.assertEqual(body["xendit"]["configured"], False)
        self.assertIn(key, body["xendit"]["webhook_url"])
        self.assertEqual(body["enabled"]["xendit"], False)
        row.refresh_from_db()
        self.assertEqual((row.api_key_ciphertext, row.key_mode), ("", ""))
        self.assertNotEqual(row.webhook_token_ciphertext, "")  # F1: the webhook still authenticates
        self.assertEqual(crypto.decrypt(row.webhook_token_ciphertext), TOKEN)

    def test_remove_says_how_many_orders_still_wait(self):
        self.saved()
        from apps.pack import constants as C
        from apps.pack.tests.owner_base import make_order
        order, _ = make_order(self.site, status=C.AWAITING_XENDIT)
        order.payment_method, order.xendit_external_id = "xendit", f"np-{self.site.pk}-{'a' * 24}"
        order.save()
        self.assertEqual(self.delete(self.xurl).json()["awaiting_xendit_orders"], 1)

    def test_remove_with_nothing_saved_is_fine(self):
        self.assertEqual(self.delete(self.xurl).json()["awaiting_xendit_orders"], 0)


@xendit_on
class SwitchTests(XenditApiCase):
    def test_it_cannot_be_switched_on_without_a_working_key(self):
        response = self.patch(self.url, {"xendit_enabled": True})
        self.assertEqual(self.fields(response)["xendit_enabled"], ["Save a working Xendit key first."])
        self.assertFalse(SitePaymentSettings.objects.exists())

    def test_it_cannot_be_switched_on_while_the_key_has_failed(self):
        self.saved()
        SiteXenditCredential.objects.update(key_failed_at=SiteXenditCredential.objects.get().updated_at)
        self.assertEqual(self.fields(self.patch(self.url, {"xendit_enabled": True}))["xendit_enabled"], ["Save a working Xendit key first."])

    def test_switched_on_with_a_key_it_is_enabled_and_available_and_off_again(self):
        self.saved()
        self.added()
        body = self.patch(self.url, {"xendit_enabled": True}).json()
        self.assertEqual(body["enabled"], {"direct": True, "xendit": True})
        self.assertEqual(body["available"], {"direct": True, "xendit": True})
        body = self.patch(self.url, {"xendit_enabled": False}).json()
        self.assertEqual(body["enabled"]["xendit"], False)
        self.assertEqual(body["available"]["xendit"], False)
        self.assertEqual(body["available"]["direct"], True)  # untouched

    def test_xendit_available_does_not_depend_on_a_bank_account(self):
        self.saved()
        body = self.patch(self.url, {"xendit_enabled": True}).json()
        self.assertEqual(body["available"], {"direct": False, "xendit": True})

    def test_direct_off_and_xendit_on_together(self):
        self.saved()
        body = self.patch(self.url, {"direct_enabled": False, "xendit_enabled": True}).json()
        self.assertEqual(body["enabled"], {"direct": False, "xendit": True})

    @override_settings(XENDIT_ENABLED=False)
    def test_with_the_platform_switch_off_a_saved_xendit_row_still_reads_off(self):
        SitePaymentSettings.objects.create(site=self.site, xendit_enabled=True)
        self.assertEqual(self.read()["enabled"], {"direct": True, "xendit": False})
        self.assertEqual(services.methods_available(self.site), {"direct": False, "xendit": False})

    def test_a_row_left_on_does_not_make_xendit_available_without_a_working_key(self):
        SitePaymentSettings.objects.create(site=self.site, xendit_enabled=True)
        self.assertEqual(services.methods_available(self.site)["xendit"], False)


@xendit_on
class SettingsOutTests(XenditApiCase):
    def test_any_method_stays_direct_only_so_the_product_shop_is_unchanged(self):
        self.saved()
        self.patch(self.url, {"xendit_enabled": True})
        self.assertFalse(services.any_method(self.site))
        self.added()
        self.assertTrue(services.any_method(self.site))


@xendit_on
class ClientTests(SimpleTestCase):
    """The client on urllib: Basic auth with the key, the error map, the parsers. No network."""

    def reply(self, body=b"{}", code=200):
        class Response(BytesIO):
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False
        if code >= 400:
            return urllib.error.HTTPError("https://api.xendit.co/x", code, "err", {}, BytesIO(b"{}"))
        return Response(body)

    def call(self, response_or_error, fn=xendit_client.check_key, *args, **kw):
        seen = {}

        def fake_open(request):
            seen["request"] = request
            if isinstance(response_or_error, Exception):
                raise response_or_error
            return response_or_error

        with override_settings(XENDIT_FAKE=False), mock.patch.object(xendit_client, "_open", fake_open):
            result = fn("xnd_development_k", *args, **kw)
        return result, seen["request"]

    def test_the_key_is_sent_as_basic_auth_and_never_in_the_url(self):
        _, request = self.call(self.reply(b'{"balance": 1}'))
        import base64
        self.assertEqual(request.get_header("Authorization"), "Basic " + base64.b64encode(b"xnd_development_k:").decode())
        self.assertEqual(request.full_url, "https://api.xendit.co/balance")
        self.assertNotIn("xnd_development_k", request.full_url)

    def test_error_codes_map_to_unauthorized_unavailable_or_invalid(self):
        for code, kind in ((401, "unauthorized"), (403, "unauthorized"), (429, "unavailable"), (500, "unavailable"),
                           (503, "unavailable"), (400, "invalid"), (404, "invalid")):
            with self.subTest(code=code):
                with self.assertRaises(xendit_client.XenditError) as caught:
                    self.call(self.reply(code=code))
                self.assertEqual(caught.exception.kind, kind)

    def test_a_network_failure_or_timeout_or_bad_body_is_unavailable(self):
        for failure in (urllib.error.URLError("down"), TimeoutError(), OSError("reset")):
            with self.subTest(failure=failure):
                with self.assertRaises(xendit_client.XenditError) as caught:
                    self.call(failure)
                self.assertEqual(caught.exception.kind, "unavailable")
        with self.assertRaises(xendit_client.XenditError) as caught:
            self.call(self.reply(b"<html>"))
        self.assertEqual(caught.exception.kind, "unavailable")

    def test_create_invoice_posts_the_amount_in_php_with_our_reference_and_redirects(self):
        _, request = self.call(
            self.reply(b'{"id": "i1"}'), xendit_client.create_invoice, external_id="np-1-a", amount=1488,
            description="d", duration_seconds=86400, success_url="https://s/ok", failure_url="https://s/ok",
        )
        self.assertEqual((request.get_method(), request.full_url), ("POST", "https://api.xendit.co/v2/invoices"))
        sent = json.loads(request.data)
        self.assertEqual(sent["amount"], 1488.0)
        self.assertEqual((sent["currency"], sent["external_id"], sent["invoice_duration"]), ("PHP", "np-1-a", 86400))
        self.assertEqual((sent["success_redirect_url"], sent["failure_redirect_url"]), ("https://s/ok", "https://s/ok"))

    def test_find_invoices_keeps_only_ours_newest_first_and_adopts_the_newest_unexpired(self):
        docs = json.dumps([
            {"id": "a", "external_id": "np-1-x", "status": "EXPIRED", "created": "2026-11-18T01:00:00Z"},
            {"id": "b", "external_id": "np-1-x", "status": "PENDING", "created": "2026-11-18T02:00:00Z"},
            {"id": "c", "external_id": "someone-else", "status": "PENDING", "created": "2026-11-18T03:00:00Z"},
        ]).encode()
        found, request = self.call(self.reply(docs), xendit_client.find_invoices, "np-1-x")
        self.assertEqual([d["id"] for d in found], ["b", "a"])
        self.assertIn("external_id=np-1-x", request.full_url)
        self.assertEqual(xendit_client.newest_unexpired(found)["id"], "b")
        self.assertIsNone(xendit_client.newest_unexpired([found[1]]))

    def test_parse_invoice_reads_the_paid_amount_and_xendits_paid_time(self):
        state = xendit_client.parse_invoice({
            "id": "i", "external_id": "np-1-x", "status": "paid", "amount": 1488, "paid_amount": 1488,
            "currency": "php", "paid_at": "2026-11-18T10:00:00.000Z",
        })
        self.assertEqual((state.status, state.currency, str(state.amount)), ("PAID", "PHP", "1488"))
        self.assertEqual(state.paid_at.isoformat(), "2026-11-18T10:00:00+00:00")
        for bad in (None, "", "yesterday", "2026-02-30T10:00:00Z", "2026-11-18T10:00:00", 5):
            self.assertIsNone(xendit_client.parse_paid_at(bad))


class CryptoAndCheckTests(SimpleTestCase):
    def test_round_trip_and_the_wrong_key_fails(self):
        with override_settings(PAYMENT_CREDENTIALS_KEY=FERNET):
            token = crypto.encrypt("secret")
            self.assertNotIn("secret", token)
            self.assertEqual(crypto.decrypt(token), "secret")
        with override_settings(PAYMENT_CREDENTIALS_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(crypto.SecretKeyError):
                crypto.decrypt(token)

    def test_no_or_invalid_key_cannot_encrypt_or_decrypt(self):
        for key in ("", "not-a-fernet-key"):
            with override_settings(PAYMENT_CREDENTIALS_KEY=key):
                self.assertFalse(crypto.key_ready())
                with self.assertRaises(crypto.SecretKeyError):
                    crypto.encrypt("x")
                with self.assertRaises(crypto.SecretKeyError):
                    crypto.decrypt("x")

    def run_check(self):
        from apps.payments.checks import check_xendit_dependencies
        return check_xendit_dependencies(None)

    def test_deploy_check_xendit_on_needs_the_key(self):
        with override_settings(XENDIT_ENABLED=True, PAYMENT_CREDENTIALS_KEY=""):
            found = self.run_check()
        self.assertEqual([e.id for e in found], ["payments.E001"])
        with override_settings(XENDIT_ENABLED=True, PAYMENT_CREDENTIALS_KEY=FERNET):
            self.assertEqual(self.run_check(), [])
        with override_settings(XENDIT_ENABLED=False, PAYMENT_CREDENTIALS_KEY=""):
            self.assertEqual(self.run_check(), [])
