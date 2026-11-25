"""The member Orders log (ClickUp z8uq9meze0, M5'): the routes, their isolation, the checks on
every field, the filters, the month figures, the CSV, and the retention paths."""
import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.members import order_log, retention
from apps.members.models import MemberOrderLog
from apps.members.tests import test_retention as retention_tests
from apps.members.tests.base import BASE, NOT_FOUND, NOT_SIGNED_IN, OTHER_HOST, MembersTestCase
from apps.operators.models import OperatorAuditEntry
from apps.pack.tests import test_remove_test_member as remove_tests

BEN_EMAIL, BEN_MOBILE = "ben@buyer.test", "09175550120"
CARLA_EMAIL, CARLA_MOBILE = "carla@buyer.test", "09175550121"
YESTERDAY = (timezone.localdate() - timedelta(days=1)).isoformat()


def fields(**over):
    base = {
        "order_number": "PH-000123", "ordered_on": YESTERDAY, "customer_first_name": "TEST Ana",
        "city": "Quezon City", "payment": "cod", "total": "1450.50", "status": "for_approval",
    }
    base.update(over)
    return base


def make_row(member, number="PH-1", **over):
    values = {
        "order_number": number, "ordered_on": date(2026, 11, 10), "customer_first_name": "Ana",
        "city": "Manila", "payment": "cod", "total_centavos": 100_00, "status": "for_approval",
    }
    values.update(over)
    return MemberOrderLog.objects.create(site=member.site, member=member, **values)


class OrderLogCase(MembersTestCase):
    def setUp(self):
        super().setUp()
        self.carla = self.new_member(self.site, email=CARLA_EMAIL, mobile=CARLA_MOBILE, name="Carla")
        self.ben = self.new_member(self.other_site, email=BEN_EMAIL, mobile=BEN_MOBILE, name="Ben")
        self.token = self.sign_in()

    def call(self, path, token=None, **body):
        return self.post("order-log/" + path, token=self.token if token is None else token, **body)

    def create(self, **over):
        return self.call("create/", **fields(**over))

    def listing(self, **body):
        return self.call("", **body)


class IsolationTests(OrderLogCase):
    def setUp(self):
        super().setUp()
        self.hers = make_row(self.carla, "PH-777", customer_first_name="CarlaCustomer")
        self.theirs = make_row(self.ben, "PH-888", customer_first_name="BenCustomer")
        self.mine = make_row(self.member, "PH-111")

    def test_a_member_lists_only_their_own_rows(self):
        response = self.listing()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([o["id"] for o in response.json()["orders"]], [self.mine.public_id])
        for body in (response.content, self.call("export/").content):
            self.assertNotIn(b"CarlaCustomer", body)
            self.assertNotIn(b"BenCustomer", body)
        self.assertEqual(response.json()["tiles"]["for_approval"], 1)
        self.assertEqual(response.json()["header"]["needs_action"], 1)

    def test_another_members_row_is_a_404_for_read_update_and_delete(self):
        for row in (self.hers, self.theirs):  # same site, then another site
            for path, extra in (("", {}), ("update/", fields(order_number="PH-9")), ("delete/", {})):
                response = self.call(f"{row.public_id}/{path}", **extra)
                self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND), path)
        self.hers.refresh_from_db()
        self.assertEqual(self.hers.order_number, "PH-777")
        self.assertEqual(MemberOrderLog.objects.count(), 3)

    def test_a_row_of_site_one_is_unreachable_from_site_twos_host(self):
        token = self.sign_in(BEN_EMAIL, host=OTHER_HOST)
        for path, extra in (("", {}), ("update/", fields()), ("delete/", {})):
            response = self.post(f"order-log/{self.mine.public_id}/{path}", host=OTHER_HOST, token=token, **extra)
            self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND), path)
        ids = [o["id"] for o in self.post("order-log/", host=OTHER_HOST, token=token).json()["orders"]]
        self.assertEqual(ids, [self.theirs.public_id])
        self.assertTrue(MemberOrderLog.objects.filter(pk=self.mine.pk).exists())

    def test_a_token_of_site_two_does_not_work_on_site_ones_host(self):
        token = self.sign_in(BEN_EMAIL, host=OTHER_HOST)
        response = self.call("", token=token)
        self.assertEqual((response.status_code, response.json()), (401, NOT_SIGNED_IN))

    def test_a_created_row_belongs_to_the_session_member_and_site(self):
        row = MemberOrderLog.objects.get(public_id=self.create().json()["order"]["id"])
        self.assertEqual((row.member_id, row.site_id), (self.member.pk, self.site.pk))

    def test_the_same_number_for_two_members_is_allowed(self):
        self.assertEqual(self.create(order_number="PH-777").status_code, 201)
        self.assertEqual(MemberOrderLog.objects.filter(order_number="PH-777").count(), 2)


class GateTests(OrderLogCase):
    ROUTES = ("", "create/", "export/", "mol_x/", "mol_x/update/", "mol_x/delete/")

    def test_signed_out_is_401_on_every_route(self):
        for path in self.ROUTES:
            for token in ("", "not-a-real-token-0123456789"):
                response = self.call(path, token=token, **(fields() if "create" in path or "update" in path else {}))
                self.assertEqual((response.status_code, response.json()), (401, NOT_SIGNED_IN), path)

    def test_a_signed_out_token_is_refused_after_sign_out(self):
        self.post("signout/", token=self.token)
        self.assertEqual(self.listing().status_code, 401)

    def test_the_switch_off_and_a_host_that_is_not_sante_are_404(self):
        with override_settings(MEMBERS_ENABLED=False):
            for path in self.ROUTES:
                self.assertEqual(self.call(path).json(), NOT_FOUND, path)
        for path in self.ROUTES:
            response = self.post("order-log/" + path, host="nobody.negosyotest.com", token=self.token)
            self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND), path)

    def test_the_server_credential_is_required(self):
        self.assertEqual(self.post("order-log/", secret=None, token=self.token).status_code, 401)
        self.assertEqual(self.post("order-log/", secret="wrong", token=self.token).status_code, 401)


class PackRouteTests(OrderLogCase):
    @override_settings(PACK_ENABLED=True)
    def test_the_membership_pack_cards_route_is_untouched_by_the_order_log_router(self):
        response = self.post("orders/", token=self.token)  # POST /storefront/v1/member/orders/
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"cards": []})
        make_row(self.member)
        self.assertEqual(self.post("orders/", token=self.token).json(), {"cards": []})  # never the log
        self.assertEqual(self.post("orders/create/", token=self.token, **fields()).status_code, 404)


class CreateTests(OrderLogCase):
    def test_create_answers_201_and_the_order_object(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.content)
        order = response.json()["order"]
        self.assertEqual(set(order), {
            "id", "order_number", "ordered_on", "customer_first_name", "city", "payment", "total",
            "status", "created_at", "updated_at",
        })
        self.assertRegex(order["id"], r"^mol_[0-9a-z]{20}$")
        self.assertEqual((order["total"], order["order_number"], order["ordered_on"]), ("1450.50", "PH-000123", YESTERDAY))
        read = self.call(order["id"] + "/")
        self.assertEqual(read.json(), {"order": order})

    def test_every_field_is_required(self):
        for name in order_log.FIELDS:
            body = fields()
            del body[name]
            response = self.call("create/", **body)
            self.assertEqual(response.status_code, 400, name)
            self.assertEqual(response.json()["error"]["code"], "invalid_order")
            self.assertEqual(list(response.json()["error"]["fields"]), [name])

    def test_unknown_fields_are_refused_on_every_route(self):
        self.assertEqual(self.call("create/", **fields(address="1 Rizal St")).status_code, 400)
        self.assertEqual(self.call("create/", **fields(tracking="X1")).status_code, 400)
        row = make_row(self.member)
        self.assertEqual(self.call(f"{row.public_id}/update/", **fields(phone="0917")).status_code, 400)
        self.assertEqual(self.listing(sort="x").status_code, 400)
        self.assertEqual(self.call("export/", sort="x").status_code, 400)
        self.assertEqual(self.call(f"{row.public_id}/delete/", force=True).status_code, 400)
        self.assertEqual(MemberOrderLog.objects.count(), 1)

    def test_all_wrong_fields_are_named_together(self):
        response = self.create(order_number="X", ordered_on="soon", city="", total="-1", status="x", payment="y",
                               customer_first_name=" ")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()["error"]["fields"]), set(order_log.FIELDS))
        self.assertEqual(MemberOrderLog.objects.count(), 0)

    def test_order_number_rules(self):
        for good, stored in (("ph-1", "PH-1"), (" PH0001 ", "PH0001"), ("PH-" + "1" * 29, "PH-" + "1" * 29)):
            response = self.create(order_number=good)
            self.assertEqual((response.status_code, response.json()["order"]["order_number"]), (201, stored), good)
        for bad in ("", "  ", "AB-123", "PH", "PH-", "PH-ABC", "PH 123", "PH_123", "PH-12.3", "PH-é1",
                    "PH-" + "1" * 30, "XPH-1", None, 123):
            response = self.create(order_number=bad)
            self.assertEqual(response.status_code, 400, repr(bad))
            self.assertIn("order_number", response.json()["error"]["fields"], repr(bad))

    def test_a_duplicate_number_for_the_same_member_is_409_in_any_case(self):
        self.assertEqual(self.create(order_number="PH-5").status_code, 201)
        response = self.create(order_number="ph-5")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "order_number_taken"))
        self.assertIn("order_number", response.json()["error"]["fields"])
        self.assertEqual(MemberOrderLog.objects.count(), 1)

    def test_date_rules(self):
        today = timezone.localdate()
        for number, good in enumerate((today.isoformat(), "2007-01-01")):  # distinct numbers: day 01 would repeat
            self.assertEqual(self.create(order_number=f"PH-{number}", ordered_on=good).status_code, 201, good)
        for bad in ((today + timedelta(days=1)).isoformat(), "2006-12-31", "2026-02-30", "26-11-01", "2026/11/01",
                    "2026-1-1", "", None, 20261101):
            response = self.create(ordered_on=bad)
            self.assertEqual(response.status_code, 400, repr(bad))
            self.assertIn("ordered_on", response.json()["error"]["fields"])

    def test_text_rules(self):
        for name, limit in (("customer_first_name", 60), ("city", 80)):
            response = self.create(**{name: "  é" * 0 + "x" * limit})
            self.assertEqual(response.status_code, 201, name)
            self.assertEqual(response.json()["order"][name], "x" * limit)
            MemberOrderLog.objects.all().delete()
            for bad in ("", "   ", "x" * (limit + 1), "bad\x00name", "tab\there", "new\nline", 5, None):
                response = self.create(**{name: bad})
                self.assertEqual(response.status_code, 400, (name, bad))
                self.assertIn(name, response.json()["error"]["fields"])
        trimmed = self.create(customer_first_name="  Ana  ", city="  Cebu ").json()["order"]
        self.assertEqual((trimmed["customer_first_name"], trimmed["city"]), ("Ana", "Cebu"))

    def test_total_rules(self):
        cases = {"1450": "1450.00", "1450.5": "1450.50", "1450.50": "1450.50", "1,450.50": "1450.50",
                 "0": "0.00", "10000000": "10000000.00", "10,000,000.00": "10000000.00", "0.01": "0.01",
                 1450: "1450.00", 1450.5: "1450.50"}
        for n, (given, shown) in enumerate(cases.items()):
            response = self.create(order_number=f"PH-{n}", total=given)
            self.assertEqual((response.status_code, response.json()["order"]["total"]), (201, shown), given)
        self.assertEqual(MemberOrderLog.objects.filter(total_centavos=1450_50).count(), 4)
        for bad in ("1450.505", "-1", "10000000.01", "abc", "", "1e3", "12.", ".5", "1 450", True, None, [1]):
            response = self.create(order_number="PH-99", total=bad)
            self.assertEqual(response.status_code, 400, repr(bad))
            self.assertIn("total", response.json()["error"]["fields"])

    def test_payment_and_status_choices(self):
        for n, payment in enumerate(("cod", "prepaid")):
            self.assertEqual(self.create(order_number=f"PH-P{n}1", payment=payment).status_code, 201)
        for n, status in enumerate(order_log.STATUSES):
            self.assertEqual(self.create(order_number=f"PH-S{n}", status=status).status_code, 201, status)
        for field, bad in (("payment", "COD"), ("payment", "card"), ("status", "Delivered"), ("status", "cancelled")):
            self.assertIn(field, self.create(**{field: bad}).json()["error"]["fields"])

    def test_the_cap_answers_409_order_log_full_and_other_members_are_not_counted(self):
        MemberOrderLog.objects.bulk_create(
            MemberOrderLog(site=self.site, member=self.member, order_number=f"PH-{n}", ordered_on=date(2026, 11, 1),
                           customer_first_name="A", city="B", payment="cod", total_centavos=0, status="closed")
            for n in range(order_log.CAP)
        )
        response = self.create(order_number="PH-NEW1")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "order_log_full"))
        self.assertEqual(self.call("create/", token=self.sign_in(CARLA_EMAIL), **fields()).status_code, 201)


class UpdateDeleteTests(OrderLogCase):
    def setUp(self):
        super().setUp()
        self.row = make_row(self.member, "PH-1")
        self.other = make_row(self.member, "PH-2")

    def test_update_replaces_every_field_and_keeps_the_id(self):
        response = self.call(f"{self.row.public_id}/update/", **fields(
            order_number="ph-10", customer_first_name="Bea", city="Cebu", payment="prepaid",
            total="12.5", status="delivered"))
        self.assertEqual(response.status_code, 200, response.content)
        order = response.json()["order"]
        self.assertEqual((order["id"], order["order_number"], order["total"], order["status"]),
                         (self.row.public_id, "PH-10", "12.50", "delivered"))
        self.row.refresh_from_db()
        self.assertEqual((self.row.city, self.row.payment, self.row.total_centavos), ("Cebu", "prepaid", 1250))

    def test_update_checks_like_create_and_changes_nothing_on_error(self):
        response = self.call(f"{self.row.public_id}/update/", **fields(total="bad"))
        self.assertEqual(response.status_code, 400)
        missing = fields()
        del missing["city"]
        self.assertEqual(self.call(f"{self.row.public_id}/update/", **missing).status_code, 400)
        self.row.refresh_from_db()
        self.assertEqual(self.row.order_number, "PH-1")

    def test_update_to_a_number_already_used_is_409_but_keeping_its_own_is_fine(self):
        taken = self.call(f"{self.row.public_id}/update/", **fields(order_number="PH-2"))
        self.assertEqual((taken.status_code, taken.json()["error"]["code"]), (409, "order_number_taken"))
        same = self.call(f"{self.row.public_id}/update/", **fields(order_number="PH-1"))
        self.assertEqual(same.status_code, 200)

    def test_delete_removes_only_that_row(self):
        response = self.call(f"{self.row.public_id}/delete/")
        self.assertEqual((response.status_code, response.json()), (200, {"deleted": True}))
        self.assertEqual(list(MemberOrderLog.objects.values_list("pk", flat=True)), [self.other.pk])
        self.assertEqual(self.call(f"{self.row.public_id}/delete/").status_code, 404)

    def test_unknown_ids_are_404(self):
        for path in ("mol_nope/", "mol_nope/update/", "mol_nope/delete/"):
            response = self.call(path, **(fields() if "update" in path else {}))
            self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND), path)

    def test_create_and_export_are_not_mistaken_for_ids(self):
        self.assertEqual(self.call("export/").status_code, 200)
        self.assertEqual(self.create(order_number="PH-3").status_code, 201)


class ListTests(OrderLogCase):
    def test_empty_log(self):
        body = self.listing().json()
        self.assertEqual((body["orders"], body["page"], body["pages"], body["count"]), ([], 1, 1, 0))
        self.assertEqual(body["tiles"], dict.fromkeys(order_log.STATUSES, 0))
        self.assertEqual(body["header"], {"orders_this_month": 0, "sales_this_month": "0.00",
                                          "delivered_this_month": 0, "needs_action": 0})

    def test_paging_is_25_newest_date_first_then_newest_created(self):
        for n in range(30):
            make_row(self.member, f"PH-{n}", ordered_on=date(2026, 10, 1) + timedelta(days=n % 15))
        first = self.listing().json()
        self.assertEqual((first["page"], first["pages"], first["count"], len(first["orders"])), (1, 2, 30, 25))
        dates = [o["ordered_on"] for o in first["orders"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        second = self.listing(page=2).json()
        self.assertEqual(len(second["orders"]), 5)
        seen = {o["id"] for o in first["orders"]} | {o["id"] for o in second["orders"]}
        self.assertEqual(len(seen), 30)
        past = self.listing(page=9).json()
        self.assertEqual((past["orders"], past["pages"], past["count"]), ([], 2, 30))
        self.assertEqual(self.listing(page=0).json()["page"], 1)
        self.assertEqual(self.listing(page=-3).json()["page"], 1)

    def test_same_date_orders_come_newest_created_first(self):
        old = make_row(self.member, "PH-1")
        new = make_row(self.member, "PH-2")
        self.assertEqual([o["id"] for o in self.listing().json()["orders"]], [new.public_id, old.public_id])

    def test_search_matches_number_or_first_name_case_insensitively(self):
        a = make_row(self.member, "PH-ABC1", customer_first_name="Maria")
        b = make_row(self.member, "PH-2", customer_first_name="abcdef")
        make_row(self.member, "PH-3", customer_first_name="Zed")
        ids = lambda q: {o["id"] for o in self.listing(q=q).json()["orders"]}
        self.assertEqual(ids("abc"), {a.public_id, b.public_id})
        self.assertEqual(ids("MARI"), {a.public_id})
        self.assertEqual(ids("ph-3"), {MemberOrderLog.objects.get(order_number="PH-3").public_id})
        self.assertEqual(ids("%"), set())
        self.assertEqual(ids(""), {a.public_id, b.public_id, MemberOrderLog.objects.get(order_number="PH-3").public_id})
        self.assertEqual(self.listing(q="x" * 60).status_code, 200)
        too_long = self.listing(q="x" * 61)
        self.assertEqual((too_long.status_code, too_long.json()["error"]["code"]), (400, "invalid_order"))
        self.assertIn("q", too_long.json()["error"]["fields"])

    def test_dates_are_inclusive_and_status_filters(self):
        for n, day in enumerate((9, 10, 11, 12)):
            make_row(self.member, f"PH-{n}", ordered_on=date(2026, 11, day), status="delivered" if day == 11 else "shipping")
        count = lambda **f: self.listing(**f).json()["count"]
        self.assertEqual(count(date_from="2026-11-10", date_to="2026-11-11"), 2)
        self.assertEqual(count(date_from="2026-11-12"), 1)
        self.assertEqual(count(date_to="2026-11-09"), 1)
        self.assertEqual(count(status="delivered"), 1)
        self.assertEqual(count(status="delivered", date_to="2026-11-10"), 0)
        self.assertEqual(count(date_from="2026-11-12", date_to="2026-11-10"), 0)

    def test_a_bad_filter_is_400_invalid_order_with_fields(self):
        response = self.listing(date_from="x", date_to="2026-13-01", status="nope")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid_order"))
        self.assertEqual(set(response.json()["error"]["fields"]), {"date_from", "date_to", "status"})

    def test_tiles_count_the_whole_log_while_count_is_filtered(self):
        for n, status in enumerate(("for_approval", "for_approval", "for_booking", "delivered", "closed")):
            make_row(self.member, f"PH-{n}", status=status)
        body = self.listing(status="delivered").json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["tiles"], {"for_approval": 2, "for_booking": 1, "for_pickup": 0, "shipping": 0,
                                         "delivered": 1, "closed": 1})

    def test_header_month_sales_exclude_closed_and_needs_action_is_the_whole_log(self):
        with mock.patch("django.utils.timezone.now", return_value=datetime(2026, 11, 15, 4, tzinfo=dt_timezone.utc)):
            make_row(self.member, "PH-1", ordered_on=date(2026, 11, 1), total_centavos=1000_50, status="delivered")
            make_row(self.member, "PH-2", ordered_on=date(2026, 11, 14), total_centavos=200_25, status="for_booking")
            make_row(self.member, "PH-3", ordered_on=date(2026, 11, 14), total_centavos=999_00, status="closed")
            make_row(self.member, "PH-4", ordered_on=date(2026, 10, 31), total_centavos=500_00, status="for_approval")
            make_row(self.member, "PH-5", ordered_on=date(2026, 11, 2), total_centavos=1_00, status="shipping")
            make_row(self.carla, "PH-6", ordered_on=date(2026, 11, 2), total_centavos=777_00, status="delivered")
            header = self.listing().json()["header"]
        self.assertEqual(header, {"orders_this_month": 4, "sales_this_month": "1201.75",
                                  "delivered_this_month": 1, "needs_action": 2})

    def test_the_month_boundary_follows_the_manila_calendar(self):
        make_row(self.member, "PH-1", ordered_on=date(2026, 11, 30), total_centavos=1_00)
        make_row(self.member, "PH-2", ordered_on=date(2026, 12, 1), total_centavos=2_00)
        # 2026-11-30 17:00 UTC is 2026-12-01 01:00 in Manila.
        with mock.patch("django.utils.timezone.now", return_value=datetime(2026, 11, 30, 17, tzinfo=dt_timezone.utc)):
            header = self.listing().json()["header"]
            self.assertEqual((header["orders_this_month"], header["sales_this_month"]), (1, "2.00"))
            self.assertEqual(self.create(order_number="PH-9", ordered_on="2026-12-01").status_code, 201)
            self.assertEqual(self.create(order_number="PH-8", ordered_on="2026-12-02").status_code, 400)
        # 2026-11-30 15:59 UTC is still 2026-11-30 23:59 in Manila.
        with mock.patch("django.utils.timezone.now", return_value=datetime(2026, 11, 30, 15, 59, tzinfo=dt_timezone.utc)):
            header = self.listing().json()["header"]
            self.assertEqual((header["orders_this_month"], header["sales_this_month"]), (1, "1.00"))

    def test_header_handles_december(self):
        self.assertEqual(order_log.header(mock.Mock(site_id=self.site.pk, member_id=self.member.pk),
                                          today=date(2026, 12, 31))["orders_this_month"], 0)
        make_row(self.member, "PH-1", ordered_on=date(2026, 12, 31))
        make_row(self.member, "PH-2", ordered_on=date(2027, 1, 1))
        found = order_log.header(mock.Mock(site_id=self.site.pk, member_id=self.member.pk), today=date(2026, 12, 15))
        self.assertEqual(found["orders_this_month"], 1)


class CsvTests(OrderLogCase):
    def export(self, **body):
        response = self.call("export/", **body)
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def lines(self, response):
        return response.content.decode("utf-8-sig").splitlines()

    def test_headers_bom_columns_and_labels(self):
        make_row(self.member, "PH-1", customer_first_name="Santé", city="Quezon City", payment="prepaid",
                 total_centavos=1450_50, status="closed")
        response = self.export()
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(response["Content-Disposition"],
                         f'attachment; filename="orders-{timezone.localdate().isoformat()}.csv"')
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(self.lines(response), [
            "PH #,Date,Customer,City,Payment,Total,Status",
            "PH-1,2026-11-10,Santé,Quezon City,Prepaid,1450.50,Closed/other",
        ])

    def test_cells_that_start_like_a_formula_get_a_leading_quote(self):
        for n, lead in enumerate(("=", "+", "-", "@", "\t", "\r")):
            make_row(self.member, f"PH-{n}", customer_first_name=f"{lead}cmd", city=f"{lead}x")
        make_row(self.member, "PH-OK", customer_first_name="Ok=fine", city="a-b")
        text = self.export().content.decode("utf-8-sig")
        import csv
        rows = list(csv.reader(StringIO(text, newline="")))
        by_number = {r[0]: r for r in rows[1:]}
        for n, lead in enumerate(("=", "+", "-", "@", "\t", "\r")):
            self.assertEqual(by_number[f"PH-{n}"][2:4], [f"'{lead}cmd", f"'{lead}x"])
        self.assertEqual(by_number["PH-OK"][2:4], ["Ok=fine", "a-b"])

    def test_it_follows_the_lists_filters_and_holds_only_own_rows(self):
        make_row(self.member, "PH-1", status="delivered", ordered_on=date(2026, 11, 10))
        make_row(self.member, "PH-2", status="shipping", ordered_on=date(2026, 11, 11), customer_first_name="Zed")
        make_row(self.carla, "PH-3", status="delivered")
        for filters in ({}, {"status": "delivered"}, {"q": "zed"}, {"date_from": "2026-11-11"},
                        {"date_to": "2026-11-10"}, {"q": "none"}):
            listed = [o["order_number"] for o in self.listing(**filters).json()["orders"]]
            exported = [line.split(",")[0] for line in self.lines(self.export(**filters))[1:]]
            self.assertEqual(exported, listed, filters)
        self.assertNotIn("PH-3", self.export().content.decode())

    def test_a_bad_filter_is_400_and_the_export_is_capped(self):
        self.assertEqual(self.call("export/", status="x").json()["error"]["code"], "invalid_order")
        MemberOrderLog.objects.bulk_create(
            MemberOrderLog(site=self.site, member=self.member, order_number=f"PH-{n}", ordered_on=date(2026, 11, 1),
                           customer_first_name="A", city="B", payment="cod", total_centavos=0, status="closed")
            for n in range(order_log.CAP)
        )
        self.assertEqual(len(self.lines(self.export())), order_log.CAP + 1)
        with mock.patch.object(order_log, "EXPORT_MAX", 10):
            self.assertEqual(len(self.lines(self.export())), 11)


class OrderLogRetentionTests(retention_tests.MembersDisposalTestCase):
    def setUp(self):
        super().setUp()
        self.row = make_row(self.member, "PH-1")
        self.their_row = make_row(self.theirs, "PH-1")

    def test_disposal_deletes_the_rows_of_the_accounts_sites_only_and_counts_them(self):
        counts = self.dispose()
        self.assertEqual(counts["member_order_logs"], 1)
        self.assertFalse(MemberOrderLog.objects.filter(pk=self.row.pk).exists())
        self.assertTrue(MemberOrderLog.objects.filter(pk=self.their_row.pk).exists())
        entry = OperatorAuditEntry.objects.get(action="account_disposed")
        self.assertEqual(entry.detail["blanked"]["member_order_logs"], ["all rows deleted"])
        self.assertNotIn("Ana", json.dumps(entry.detail))

    def test_the_dry_run_counts_and_deletes_nothing(self):
        self.assertEqual(self.dispose(dry_run=True)["member_order_logs"], 1)
        self.assertEqual(MemberOrderLog.objects.count(), 2)

    @override_settings(MEMBERS_ENABLED=False)
    def test_with_the_switch_off_the_table_is_not_read(self):
        counts = self.dispose()
        self.assertNotIn("member_order_logs", counts)
        self.assertNotIn("member_order_logs", retention.blanked())
        self.assertTrue(MemberOrderLog.objects.filter(pk=self.row.pk).exists())

    def test_the_export_lists_own_accounts_rows_only(self):
        data = retention_tests.ExportTests.export(self)
        self.assertEqual(data["member_order_logs"], [{
            "member": self.member.public_id, "order_number": "PH-1", "ordered_on": "2026-11-10",
            "customer_first_name": "Ana", "city": "Manila", "payment": "cod", "total": "100.00",
            "status": "for_approval", "created_at": self.row.created_at.isoformat(),
        }])


class RemoveTestMemberOrderLogTests(remove_tests.RemoveTestMemberTests):
    def setUp(self):
        super().setUp()
        for number in ("PH-1", "PH-2"):
            make_row(self.member, number)
        self.real_row = make_row(self.real, "PH-1")

    def test_the_dry_run_counts_the_rows_and_keeps_them(self):
        out = remove_tests.run(self.site, self.member)
        self.assertIn('"member_order_logs": 2', out)
        self.assertEqual(MemberOrderLog.objects.filter(member=self.member).count(), 2)

    def test_apply_deletes_the_test_members_rows_only(self):
        with self.captureOnCommitCallbacks(execute=True):
            out = remove_tests.run(self.site, self.member, "--apply")
        self.assertIn('"member_order_logs": 2', out)
        self.assertEqual(MemberOrderLog.objects.filter(member=self.member).count(), 0)
        self.assertEqual(list(MemberOrderLog.objects.values_list("pk", flat=True)), [self.real_row.pk])
        entry = OperatorAuditEntry.objects.get(action="pack_test_member_removed")
        self.assertEqual(entry.detail["counts"]["member_order_logs"], 2)
        again = remove_tests.run(self.site, self.member, "--apply")
        self.assertIn("already emptied", again)
