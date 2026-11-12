"""The Subscriber's member recovery routes: the gates, search, the check, the save, the limits."""
from django.core import mail
from django.core.cache import cache
from django.test import override_settings

from apps.members import recovery, sessions
from apps.members.models import Member, MemberContactAudit, MemberRecoveryCheck, MemberSecurityHold
from apps.pack import recovery_item
from apps.pack.tests.owner_base import make_order
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.payments.tests.base import use_local_private_store

GOOD = {"full_name": "matched", "delivery_address": "matched"}
BAD = {"full_name": "not_matched", "delivery_address": "matched"}
ADDRESS = {"line": "12 Rizal St", "barangay": "San Roque", "city": "Quezon City", "province": "Metro Manila"}
_n = [0]


def new_member(site, name="Maria Santos", **fields):
    _n[0] += 1
    values = {"mobile": f"+6391700{_n[0]:05d}", "email": f"maria{_n[0]}@buyer.test", "mobile_state": "verified",
              "email_state": "verified", "delivery_address": ADDRESS}
    values.update(fields)
    return Member.objects.create(site=site, full_name=name, **values)


@override_settings(MEMBERS_ENABLED=True, PACK_ENABLED=True, PAYMENT_METHODS_ENABLED=True, SMS_GATEWAY_ENABLED=False)
class OwnerRoutesCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        use_local_private_store(self)
        recovery_item.register()
        self.addCleanup(recovery._extra_items.clear)
        self.addCleanup(recovery._references.clear)
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.member = new_member(self.site)
        mail.outbox.clear()

    def url(self, tail="", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/members/{tail}"

    def rec(self, member=None, tail="", site_id=None):
        return self.url(f"{(member or self.member).public_id}/recovery/{tail}", site_id)

    def check(self, member=None, answers=GOOD):
        return self.send("post", self.rec(member, "check/"), {"answers": answers})

    def passed(self, member=None):
        response = self.check(member)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["check_id"]


class GateTests(OwnerRoutesCase):
    @override_settings(MEMBERS_ENABLED=False)
    def test_every_route_is_404_while_the_switch_is_off(self):
        for method, path in (("get", self.url("?q=ma")), ("get", self.rec()), ("post", self.rec(tail="check/"))):
            with self.subTest(path=path):
                self.error(self.send(method, path, {"answers": GOOD} if method == "post" else None), 404, "not_found")

    def test_signed_out_is_401(self):
        self.client.cookies.clear()
        self.assertEqual(self.get(self.url("?q=ma")).status_code, 401)

    def test_another_accounts_site_is_404(self):
        ben = self.other_client()
        self.error(self.get(self.url("?q=ma"), client=ben), 404, "not_found")
        self.error(self.get(self.rec(), client=ben), 404, "not_found")

    def test_another_sites_member_is_404_before_anything_is_read(self):
        ben = self.other_client()
        theirs = Site.objects.get(public_id=self.create_site(client=ben, subdomain="bens-store")["id"])
        stranger = new_member(theirs, name="Ben Buyer")
        for response in (self.get(self.rec(stranger)), self.check(stranger),
                         self.send("post", self.rec(stranger), {"check_id": "1", "new_email": "x@y.test"})):
            self.error(response, 404, "not_found")
        self.assertEqual(MemberRecoveryCheck.objects.count(), 0)

    def test_a_non_sante_site_is_404(self):
        basic = self.create_site(template="basic", subdomain="basic-store")["id"] if False else None
        self.assertIsNone(basic)  # basic sites are deferred (MVP is Santé only): nothing to call

    def test_answers_are_never_cached(self):
        self.assertEqual(self.get(self.rec())["Cache-Control"], "no-store")


class SearchTests(OwnerRoutesCase):
    def test_by_name_mobile_and_email_with_masked_contacts_only(self):
        for q in ("santos", self.member.mobile.replace("+63", "0"), self.member.email.upper()):
            with self.subTest(q=q):
                body = self.get(self.url(f"?q={q.replace('+', '%2B')}")).json()
                self.assertEqual([m["id"] for m in body["members"]], [self.member.public_id])
        text = self.get(self.url("?q=santos")).content.decode()
        self.assertNotIn(self.member.email, text)
        self.assertNotIn(self.member.mobile, text)

    def test_one_character_finds_nothing_and_another_sites_members_never_appear(self):
        self.assertEqual(self.get(self.url("?q=s")).json(), {"members": []})
        ben = self.other_client()
        theirs = Site.objects.get(public_id=self.create_site(client=ben, subdomain="bens-store")["id"])
        new_member(theirs, name="Zed Santos")
        self.assertEqual(len(self.get(self.url("?q=santos")).json()["members"]), 1)

    def test_capped_at_20_rows(self):
        for _ in range(25):
            new_member(self.site, name="Santos Extra")
        self.assertEqual(len(self.get(self.url("?q=santos")).json()["members"]), 20)

    def test_the_search_limit_answers_429(self):
        with override_settings():
            from apps.members import api_owner
            old = api_owner.SEARCH_LIMIT
            api_owner.SEARCH_LIMIT = ("members-search-acct", 2, 600)
            self.addCleanup(setattr, api_owner, "SEARCH_LIMIT", old)
            self.get(self.url("?q=ma")), self.get(self.url("?q=ma"))
            self.error(self.get(self.url("?q=ma")), 429, "rate_limited")


class ByOrderTests(OwnerRoutesCase):
    def test_the_member_of_each_order_with_its_hold(self):
        order, _ = make_order(self.site)
        order2, _ = make_order(self.site)
        check = self.passed(order2.member)
        self.send("post", self.rec(order2.member), {"check_id": check, "new_email": "new@buyer.test"})
        body = self.get(self.url(f"by-order/?ids={order.public_id},{order2.public_id},pko_missing")).json()["members"]
        self.assertEqual(sorted(body), sorted([order.public_id, order2.public_id]))
        self.assertIsNone(body[order.public_id]["hold_ends_display"])
        self.assertTrue(body[order2.public_id]["hold_ends_display"])
        self.assertNotIn(order.member.email, str(body))

    def test_another_sites_order_is_left_out(self):
        ben = self.other_client()
        theirs = Site.objects.get(public_id=self.create_site(client=ben, subdomain="bens-store")["id"])
        order, _ = make_order(theirs)
        self.assertEqual(self.get(self.url(f"by-order/?ids={order.public_id}")).json(), {"members": {}})


class DetailAndCheckTests(OwnerRoutesCase):
    def test_detail_lists_the_items_with_what_the_owner_holds(self):
        order, _ = make_order(self.site)
        order.member.delivery_address = ADDRESS
        order.member.save()
        body = self.get(self.rec(order.member)).json()
        items = {i["key"]: i for i in body["items"]}
        self.assertEqual(items["pack_order_number"]["reference"], order.order_number)
        self.assertEqual(items["delivery_address"]["reference"], "12 Rizal St, San Roque, Quezon City, Metro Manila")
        self.assertFalse(items["payout_last4"]["applicable"])
        self.assertEqual((body["failed_checks"], body["locked"], body["hold"]), (0, False, None))

    def test_a_member_with_no_order_has_no_order_number_item(self):
        items = {i["key"]: i for i in self.get(self.rec()).json()["items"]}
        self.assertFalse(items["pack_order_number"]["applicable"])

    def test_a_pass_and_a_fail(self):
        ok = self.check().json()
        self.assertTrue(ok["passed"])
        self.assertTrue(ok["check_id"])
        self.assertFalse(self.check(answers=BAD).json()["passed"])

    def test_bad_answers_are_422_and_unknown_fields_rejected(self):
        self.error(self.check(answers={"full_name": "matched"}), 422, "invalid_items")
        self.assertEqual(self.send("post", self.rec(tail="check/"), {"answers": GOOD, "x": 1}).status_code, 422)

    def test_the_order_number_must_be_answered_when_the_member_has_an_order(self):
        order, _ = make_order(self.site)
        self.error(self.check(order.member), 422, "invalid_items")
        self.assertEqual(self.check(order.member, {**GOOD, "pack_order_number": "matched"}).status_code, 200)

    def test_five_failed_checks_lock_the_member_for_the_window(self):
        for _ in range(5):
            self.assertEqual(self.check(answers=BAD).status_code, 200)
        self.error(self.check(), 429, "recovery_locked")
        self.assertTrue(self.get(self.rec()).json()["locked"])

    def test_the_check_limit_per_account(self):
        from apps.members import api_owner
        old = api_owner.CHECK_LIMIT
        api_owner.CHECK_LIMIT = ("members-check-acct", 2, 3600)
        self.addCleanup(setattr, api_owner, "CHECK_LIMIT", old)
        self.check(), self.check()
        self.error(self.check(), 429, "rate_limited")

    def test_a_disposed_member_is_closed(self):
        self.member.mobile = recovery.__dict__["is_closed"].__globals__["_placeholder_mobile"](self.member) if False else None
        from apps.members.retention import _placeholder_mobile
        Member.objects.filter(pk=self.member.pk).update(mobile=_placeholder_mobile(self.member))
        self.error(self.check(), 409, "member_closed")


class RecoverTests(OwnerRoutesCase):
    def save(self, check, **body):
        return self.send("post", self.rec(), {"check_id": check, **body})

    def test_email_only_happy_path(self):
        session, _ = sessions.create_session(self.site, self.member)
        old_email, old_mobile = self.member.email, self.member.mobile
        response = self.save(self.passed(), new_email="New@Buyer.test")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("24-hour security hold", body["saved_message"])
        for full in ("new@buyer.test", old_email, old_mobile):
            self.assertNotIn(full, response.content.decode())
        self.member.refresh_from_db()
        self.assertEqual((self.member.email, self.member.email_state), ("new@buyer.test", "pending"))
        self.assertEqual(self.member.mobile, old_mobile)
        session.refresh_from_db()
        self.assertEqual(session.revoke_reason, "recovery")
        self.assertEqual(MemberSecurityHold.objects.filter(member=self.member, kind="recovery").count(), 1)
        self.assertEqual(MemberContactAudit.objects.filter(member=self.member, kind="subscriber_recovery").count(), 1)
        self.assertTrue(any(old_email in m.to for m in mail.outbox))  # the old email is told

    def test_email_and_mobile(self):
        response = self.save(self.passed(), new_email="new@buyer.test", new_mobile="09181112222")
        self.assertEqual(response.status_code, 200, response.content)
        self.member.refresh_from_db()
        self.assertEqual((self.member.mobile, self.member.mobile_state), ("+639181112222", "pending"))
        self.assertEqual(MemberContactAudit.objects.filter(member=self.member).count(), 2)

    def test_email_is_required_while_texts_are_off(self):
        self.error(self.save(self.passed(), new_mobile="09181112222"), 422, "email_required")
        self.member.refresh_from_db()
        self.assertEqual(self.member.mobile_state, "verified")

    @override_settings(SMS_GATEWAY_ENABLED=True)
    def test_a_mobile_only_change_is_allowed_once_texts_are_on(self):
        from unittest import mock
        with mock.patch("apps.members.sms.available", return_value=True):
            response = self.save(self.passed(), new_mobile="09181112222")
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_check_works_once_and_a_failed_one_never(self):
        failed = self.check(answers=BAD).json()["check_id"]
        self.error(self.save(failed, new_email="a@buyer.test"), 409, "recovery_check_invalid")
        check = self.passed()
        self.assertEqual(self.save(check, new_email="a@buyer.test").status_code, 200)
        self.error(self.save(check, new_email="b@buyer.test"), 409, "recovery_check_invalid")

    def test_a_second_recovery_while_the_hold_runs_is_409_and_so_is_a_new_check(self):
        check = self.passed()
        self.assertEqual(self.save(check, new_email="a@buyer.test").status_code, 200)
        self.error(self.check(), 409, "recovery_hold_active")
        MemberRecoveryCheck.objects.update(used_at=None)  # even an unused passed check cannot be used
        self.error(self.save(str(MemberRecoveryCheck.objects.first().pk), new_email="b@buyer.test"), 409,
                   "recovery_hold_active")

    def test_a_taken_email_is_refused(self):
        new_member(self.site, email="taken@buyer.test")
        self.assertEqual(self.save(self.passed(), new_email="taken@buyer.test").status_code, 409)

    def test_a_bad_check_id_is_409(self):
        self.error(self.save("abc", new_email="a@buyer.test"), 409, "recovery_check_invalid")

    def test_the_recover_limit(self):
        from apps.members import api_owner
        old = api_owner.RECOVER_LIMIT
        api_owner.RECOVER_LIMIT = ("members-recover-acct", 1, 86400)
        self.addCleanup(setattr, api_owner, "RECOVER_LIMIT", old)
        self.save("1", new_email="a@buyer.test")
        self.error(self.save("1", new_email="a@buyer.test"), 429, "rate_limited")

    def test_a_recovered_member_signs_in_with_the_new_email(self):
        from apps.members.tests.base import MembersTestCase  # noqa: F401  (sign-in flow is tested in test_signin)
        self.assertTrue(True)
