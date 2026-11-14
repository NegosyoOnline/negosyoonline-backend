"""SHOP-1 follow-up (z8uq9mewrw): the member edits their own saved delivery address,
POST /storefront/v1/member/address/ (TWCinc negosyoonline_storefront/addresses.py: save_home_address)."""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.members import holds
from apps.members.models import (
    ImmutableRecordError, Member, MemberAddressEvent, MemberContactAudit, MemberSecurityHold,
)
from apps.pack.models import PackOrder
from apps.shop.models import ShopOrder
from apps.shop.tests.base import HOST, ShopTestCase

OLD = {"line": "9 Ayala Ave", "barangay": "Poblacion", "city": "Makati", "province": "Metro Manila",
       "zip": "1200", "landmark": "Near the park"}
NEW = {"line": "5 Osmena Blvd", "barangay": "Lahug", "city": "Cebu City", "province": "Cebu",
       "zip": "6000", "landmark": "Behind the church"}
OTHER_HOST = "bens-store.negosyotest.com"


class AddressEditTestCase(ShopTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.me = self.member(benefits=True, address=OLD)
        self.token_ = self.token(self.me)

    def save(self, token=None, host=HOST, **fields):
        body = {**NEW, **fields}
        body = {k: v for k, v in body.items() if v is not None}
        return self.post("member/address/", host=host, member_token=self.token_ if token is None else token, **body)

    def hold(self, member=None, **extra):
        member = member or self.me
        audit = MemberContactAudit.objects.create(
            site=member.site, member=member, kind="recovery", field="email", actor_type="owner",
        )
        return MemberSecurityHold.objects.create(
            site=member.site, member=member, kind="recovery", starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(hours=20), audit=audit, **extra,
        )


class SaveTests(AddressEditTestCase):
    def test_save_returns_the_profile_address(self):
        response = self.save()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"home_address": NEW, "home_address_source": "profile"})
        self.assertEqual(Member.objects.get(pk=self.me.pk).delivery_address, NEW)

    def test_a_guest_and_a_dead_session_are_refused(self):
        for token in ("", "not-a-real-token"):
            response = self.save(token=token)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (401, "member_session_invalid"))
        self.assertEqual(Member.objects.get(pk=self.me.pk).delivery_address, OLD)

    def test_another_sites_session_cannot_save(self):
        theirs = self.member(email="juan@buyer.test", mobile="09170000002", name="Juan Cruz", site=self.other_site)
        theirs_token = self.token(theirs)
        response = self.save(token=theirs_token)  # their token on this host
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.save(token=self.token_, host=OTHER_HOST).status_code, 401)  # mine on theirs
        self.assertEqual(Member.objects.get(pk=theirs.pk).delivery_address, {})
        self.assertEqual(Member.objects.get(pk=self.me.pk).delivery_address, OLD)
        self.assertEqual(MemberAddressEvent.objects.count(), 0)

    def test_no_secret_is_refused_and_the_body_cannot_name_a_member(self):
        self.assertEqual(self.post("member/address/", secret=None, member_token=self.token_, **NEW).status_code, 401)
        theirs = self.member(email="juan@buyer.test", mobile="09170000002", name="Juan Cruz")
        response = self.save(member_id=theirs.public_id, site_id="x")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (422, "invalid_address"))
        self.assertEqual(Member.objects.get(pk=theirs.pk).delivery_address, {})
        self.assertEqual(Member.objects.get(pk=self.me.pk).delivery_address, OLD)

    def test_extra_keys_are_refused(self):
        response = self.save(country="Philippines")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (422, "invalid_address"))
        self.assertIn("delivery_address", response.json()["error"]["fields"])
        self.assertEqual(Member.objects.get(pk=self.me.pk).delivery_address, OLD)

    def test_required_fields_and_length_are_checked_per_field(self):
        response = self.save(line="  ", city="", province="")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (422, "invalid_address"))
        self.assertEqual(set(response.json()["error"]["fields"]), {"line", "city", "province"})
        response = self.save(landmark="x" * 201)
        self.assertEqual(set(response.json()["error"]["fields"]), {"landmark"})
        self.assertEqual(self.save(landmark="x" * 200).status_code, 200)  # the limit itself is fine
        self.assertEqual(self.save(barangay=None, zip=None, landmark=None).status_code, 200)  # optional keys
        self.assertEqual(Member.objects.get(pk=self.me.pk).delivery_address["barangay"], "")

    def test_values_are_trimmed(self):
        self.save(line="  1 Mabini St  ")
        self.assertEqual(Member.objects.get(pk=self.me.pk).delivery_address["line"], "1 Mabini St")

    def test_only_ten_saves_an_hour(self):
        for _ in range(10):
            self.assertEqual(self.save().status_code, 200)
        response = self.save()
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (429, "rate_limited"))
        other = self.member(email="juan@buyer.test", mobile="09170000002", name="Juan Cruz", benefits=True)
        self.assertEqual(self.save(token=self.token(other)).status_code, 200)  # per member

class HoldTests(AddressEditTestCase):
    def test_a_recovery_hold_refuses_the_save(self):
        self.hold()
        response = self.save()
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (423, "address_locked"))
        self.assertEqual(Member.objects.get(pk=self.me.pk).delivery_address, OLD)
        self.assertEqual(MemberAddressEvent.objects.count(), 0)

    def test_another_members_hold_and_an_ended_hold_block_nothing(self):
        other = self.member(email="juan@buyer.test", mobile="09170000002", name="Juan Cruz", benefits=True)
        self.hold(other)
        self.assertEqual(self.save().status_code, 200)
        MemberSecurityHold.objects.all().update(ends_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.save().status_code, 200)

    def test_a_payout_fallback_hold_does_not_lock_the_address(self):
        MemberSecurityHold.objects.create(
            site=self.site, member=self.me, kind="payout_fallback", starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(hours=20), payout_destination_ref=1,
        )
        self.assertIsNone(holds.active_hold(self.me, "address_change"))
        self.assertEqual(self.save().status_code, 200)

class AuditTests(AddressEditTestCase):
    def test_one_row_per_save_with_old_new_and_ip(self):
        self.save(visitor_ip="198.51.100.77")
        self.save(line="6 Colon St")
        first, second = MemberAddressEvent.objects.order_by("pk")
        self.assertEqual((first.site_id, first.member_id), (self.site.pk, self.me.pk))
        self.assertEqual((first.old, first.new), (OLD, NEW))
        self.assertEqual(first.ip_advisory, "198.51.100.77")
        self.assertEqual((second.old, second.new["line"]), (NEW, "6 Colon St"))

    def test_a_refused_save_writes_no_row(self):
        self.save(line="")
        self.assertEqual(MemberAddressEvent.objects.count(), 0)

    def test_rows_are_append_only(self):
        self.save()
        event = MemberAddressEvent.objects.get()
        event.new = {}
        with self.assertRaises(ImmutableRecordError):
            event.save()
        with self.assertRaises(ImmutableRecordError):
            event.delete()
        self.assertEqual(MemberAddressEvent.objects.get().new, NEW)


class ReadTests(AddressEditTestCase):
    def read(self, token=None, host=HOST):
        return self.post("member/address/read/", host=host, member_token=self.token_ if token is None else token)

    def test_the_saved_address_is_returned_unlocked(self):
        response = self.read()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"home_address": OLD, "home_address_locked": False})

    def test_the_membership_pack_order_address_is_the_fallback(self):
        member = self.member(email="ana@buyer.test", mobile="09170000003", name="Ana Reyes", benefits=True)
        PackOrder.objects.filter(member=member).update(delivery_address=NEW)
        response = self.read(token=self.token(member))
        self.assertEqual(response.json(), {"home_address": NEW, "home_address_locked": False})

    def test_no_address_is_an_empty_object(self):
        member = self.member(email="ana@buyer.test", mobile="09170000003", name="Ana Reyes")
        response = self.read(token=self.token(member))
        self.assertEqual(response.json(), {"home_address": {}, "home_address_locked": False})

    def test_a_recovery_hold_reports_locked(self):
        self.hold()
        self.assertEqual(self.read().json(), {"home_address": OLD, "home_address_locked": True})

    def test_a_guest_and_a_dead_session_are_refused(self):
        for token in ("", "not-a-real-token"):
            response = self.read(token=token)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (401, "member_session_invalid"))

    def test_another_sites_session_is_refused_and_never_sees_this_sites_data(self):
        theirs = self.member(email="juan@buyer.test", mobile="09170000002", name="Juan Cruz",
                             site=self.other_site, address=NEW)
        theirs_token = self.token(theirs)
        self.assertEqual(self.read(token=theirs_token).status_code, 401)  # their token on this host
        self.assertEqual(self.read(token=self.token_, host=OTHER_HOST).status_code, 401)  # mine on theirs
        own = self.read(token=theirs_token, host=OTHER_HOST)
        self.assertEqual(own.json()["home_address"], NEW)
        self.assertEqual(self.read().json()["home_address"], OLD)
