"""The Subscriber's Santé registration work and the "account created" email (ClickUp z8uq9meyqj):
Start (T9), Mark registered (T11+), the owner API's data minimisation, site isolation, the
once-only email and its retry and give-up."""
import json
from datetime import date, timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.notifications.services import EmailNotSent
from apps.pack import constants as C, registered_notice, registration
from apps.pack.models import PackRegisteredNotice, SanteRegistration, SanteRegistrationEvent
from apps.pack.tests.owner_base import OwnerTestCase, make_order

VIDEO = "https://videos.example.test/santé-ordering"
ADDRESS = {"line": "12 Rizal St", "barangay": "Poblacion", "city": "Naga", "province": "Camarines Sur", "zip": "4400"}


def make_registration(site, status="ready_to_register", *, email_state="verified", purged=False):
    order, _ = make_order(site, status=C.VERIFIED)
    member = order.member
    member.email_state = email_state
    member.save(update_fields=["email_state"])
    order.delivery_address = {**ADDRESS, "receiver_name": member.full_name, "receiver_mobile": member.mobile}
    order.save(update_fields=["delivery_address"])
    reg = SanteRegistration.objects.create(
        site=site, member=member, pack_order=order, status=status,
        date_of_birth=None if purged else date(1990, 5, 17),
        desired_username="" if purged else "mariasantos90", alternate_username="" if purged else "msantos1990",
        details_confirmed_at=None if purged else timezone.now(),
        details_purged_at=timezone.now() if purged else None,
    )
    return reg


@override_settings(PACK_ORDERING_VIDEO_URL=VIDEO)
class RegistrationBase(OwnerTestCase):
    def url(self, tail="", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/pack/registrations/{tail}"

    def act(self, reg, action, **body):
        reg.refresh_from_db()
        body.setdefault("state_version", reg.state_version)
        with self.captureOnCommitCallbacks(execute=True):
            return self.post(self.url(f"{reg.member.public_id}/{action}/"), body)

    def in_registration(self, **kw):
        reg = make_registration(self.site, "in_registration", **kw)
        return reg

    def listed(self, filter="all"):
        """The owner's list. (The mark's own answer is built before the test's after-commit hook.)"""
        return self.get(self.url(f"?filter={filter}")).json()["registrations"]

    def mark(self, reg, sid="SA-1234567"):
        return self.act(reg, "registered", sante_member_id=sid)


class StartTests(RegistrationBase):
    def test_start_moves_ready_to_in_registration_and_records_the_edge(self):
        reg = make_registration(self.site)
        response = self.act(reg, "start")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "in_registration")
        reg.refresh_from_db()
        self.assertEqual((reg.status, reg.state_version), ("in_registration", 2))
        event = SanteRegistrationEvent.objects.get(registration=reg)
        self.assertEqual((event.edge, event.from_status, event.to_status, event.actor_type),
                         ("T9", "ready_to_register", "in_registration", "subscriber"))

    def test_start_from_any_other_status_is_409(self):
        for status in ("awaiting_buyer_details", "waiting_for_accept", "in_registration", "registered"):
            reg = make_registration(self.site, status)
            r = self.act(reg, "start")
            self.assertIn(r.status_code, (404, 409), status)
            if r.status_code == 409:
                self.assertEqual(r.json()["error"]["code"], "wrong_status")

    def test_a_stale_version_is_409(self):
        reg = make_registration(self.site)
        self.error(self.act(reg, "start", state_version=reg.state_version + 3), 409, "stale")
        reg.refresh_from_db()
        self.assertEqual(reg.status, "ready_to_register")


class MarkRegisteredTests(RegistrationBase):
    def test_mark_registered_records_the_id_and_sends_one_email(self):
        reg = self.in_registration()
        response = self.mark(reg)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["sante_member_id"]), ("registered", "SA-1234567"))
        reg.refresh_from_db()
        self.assertEqual((reg.status, reg.sante_member_id), ("registered", "SA-1234567"))
        self.assertIsNotNone(reg.registered_at)
        self.assertEqual(SanteRegistrationEvent.objects.get(registration=reg).edge, "T11+")
        self.assertEqual([m.to for m in mail.outbox], [[reg.member.email]])
        notice = PackRegisteredNotice.objects.get()
        self.assertEqual((notice.status, notice.attempts, notice.registration_id), ("sent", 1, reg.pk))
        self.assertIsNotNone(notice.sent_at)
        self.assertEqual(self.listed("registered")[0]["email"]["state"], "sent")

    def test_the_email_content(self):
        reg = self.in_registration()
        self.mark(reg)
        message = mail.outbox[0]
        text = message.subject + message.body
        self.assertIn("Santé account has been created", message.body)
        self.assertIn("Santé Mobile App", message.body)
        self.assertIn(VIDEO, message.body)
        for forbidden in ("password", "code", "token", "SA-1234567", "1990", "mariasantos90", "Rizal"):
            self.assertNotIn(forbidden, text.lower() if forbidden.islower() else text)

    @override_settings(PACK_ORDERING_VIDEO_URL="")
    def test_with_no_video_the_email_says_coming_soon_and_links_nothing(self):
        reg = self.in_registration()
        self.mark(reg)
        body = mail.outbox[0].body
        self.assertIn("coming soon", body)
        self.assertNotIn("http", body)

    @override_settings(PACK_ORDERING_VIDEO_URL="javascript:alert(1)")
    def test_a_video_setting_that_is_not_http_is_not_linked(self):
        reg = self.in_registration()
        self.mark(reg)
        self.assertNotIn("javascript", mail.outbox[0].body)
        self.assertIn("coming soon", mail.outbox[0].body)

    def test_a_duplicate_mark_registered_sends_no_second_email(self):
        reg = self.in_registration()
        self.mark(reg)
        again = self.mark(reg, sid="SA-9999999")
        self.error(again, 409, "wrong_status")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(PackRegisteredNotice.objects.count(), 1)
        reg.refresh_from_db()
        self.assertEqual(reg.sante_member_id, "SA-1234567")

    def test_a_duplicate_event_on_the_notice_sends_no_second_email(self):
        reg = self.in_registration()
        self.mark(reg)
        notice = PackRegisteredNotice.objects.get()
        self.assertEqual(registered_notice.send(notice.pk), "")
        self.assertEqual(registered_notice.run(timezone.now() + timedelta(days=2)), {})
        self.assertEqual(len(mail.outbox), 1)

    def test_only_from_in_registration(self):
        reg = make_registration(self.site)  # ready_to_register: Start comes first
        self.error(self.mark(reg), 409, "wrong_status")
        self.assertEqual(PackRegisteredNotice.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_bad_id_is_422_and_changes_nothing(self):
        reg = self.in_registration()
        for sid in ("", "  ", "has space", "x" * 33, "ü"):
            r = self.mark(reg, sid=sid)
            self.assertEqual(r.status_code, 422, sid)
            self.assertIn("sante_member_id", r.json()["error"]["fields"])
        reg.refresh_from_db()
        self.assertEqual(reg.status, "in_registration")
        self.assertEqual(len(mail.outbox), 0)

    def test_a_stale_version_is_409(self):
        reg = self.in_registration()
        self.error(self.act(reg, "registered", sante_member_id="SA-1", state_version=99), 409, "stale")

    def test_nothing_is_sent_when_the_change_does_not_commit(self):
        reg = self.in_registration()
        actor = ("subscriber", self.account.pk, self.account.email)
        with mock.patch("apps.pack.registration._transition", side_effect=RuntimeError("boom")):
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(RuntimeError):
                    registration.mark_registered(
                        actor, self.site, reg.member.public_id, state_version=reg.state_version,
                        sante_member_id="SA-1",
                    )
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(PackRegisteredNotice.objects.count(), 0)


class EmailFailureTests(RegistrationBase):
    def test_an_unverified_email_gets_nothing_and_shows_retrying(self):
        reg = self.in_registration(email_state="pending")
        self.assertEqual(self.mark(reg).json()["status"], "registered")  # the registration itself is not undone
        self.assertEqual(len(mail.outbox), 0)
        email = self.listed()[0]["email"]
        self.assertEqual((email["state"], email["last_error_code"]), ("retrying", "no_verified_email"))

    def test_a_failed_send_is_kept_retried_by_the_sweep_and_sent_once(self):
        reg = self.in_registration()
        with mock.patch("apps.pack.registered_notice.send_store_email", side_effect=EmailNotSent()):
            self.mark(reg)
        notice = PackRegisteredNotice.objects.get()
        self.assertEqual((notice.status, notice.attempts, notice.last_error_code), ("failed", 1, "email_not_sent"))
        self.assertEqual(self.listed()[0]["email"]["state"], "retrying")
        now = timezone.now()
        self.assertEqual(registered_notice.run(now + timedelta(hours=1)), {})  # not due yet
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(registered_notice.run(now + timedelta(hours=7)), {"sent": 1})
        notice.refresh_from_db()
        self.assertEqual((notice.status, notice.attempts, notice.last_error_code), ("sent", 2, ""))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(registered_notice.run(now + timedelta(days=3)), {})
        self.assertEqual(len(mail.outbox), 1)

    def test_it_gives_up_after_the_retry_limit_and_logs_once(self):
        reg = self.in_registration()
        with mock.patch("apps.pack.registered_notice.send_store_email", side_effect=EmailNotSent()):
            self.mark(reg)
            now, outcomes = timezone.now(), []
            with self.assertLogs("apps.pack.registered_notice", level="ERROR") as logs:
                for i in range(1, registered_notice.MAX_RETRIES + 3):
                    outcomes.append(registered_notice.run(now + timedelta(hours=7 * i)))
        notice = PackRegisteredNotice.objects.get()
        self.assertEqual(notice.status, "gave_up")
        self.assertEqual(notice.attempts, registered_notice.MAX_RETRIES + 1)
        self.assertIsNotNone(notice.gave_up_at)
        self.assertEqual(sum(o.get("gave_up", 0) for o in outcomes), 1)
        self.assertEqual(len(logs.records), 1)
        for line in logs.output:
            self.assertNotIn(reg.member.email, line)
        row = self.get(self.url("?filter=registered")).json()["registrations"][0]
        self.assertEqual(row["email"]["state"], "gave_up")
        self.assertEqual(len(mail.outbox), 0)

    def test_the_sweep_does_nothing_while_pack_is_off(self):
        with override_settings(PACK_ENABLED=False):
            self.assertEqual(registered_notice.run(), {"skipped": "off"})


class OwnerReadTests(RegistrationBase):
    def test_a_row_being_worked_on_has_the_encoding_details(self):
        ready = make_registration(self.site)
        busy = make_registration(self.site, "in_registration")
        rows = {r["id"]: r for r in self.get(self.url()).json()["registrations"]}
        self.assertEqual(set(rows), {ready.member.public_id, busy.member.public_id})
        for reg in (ready, busy):
            row = rows[reg.member.public_id]
            self.assertEqual(row["details"]["date_of_birth"], "1990-05-17")
            self.assertEqual(row["details"]["desired_username"], "mariasantos90")
            self.assertEqual(row["details"]["alternate_username"], "msantos1990")
            self.assertEqual(row["details"]["mobile"], reg.member.mobile)
            self.assertEqual(row["details"]["delivery_address"]["barangay"], "Poblacion")
            self.assertIsNone(row["email"])

    def test_a_registered_row_returns_only_name_id_date_and_email_status(self):
        reg = self.in_registration()
        self.mark(reg)
        body = self.get(self.url("?filter=registered")).json()
        row = body["registrations"][0]
        self.assertNotIn("details", row)
        self.assertEqual(
            sorted(row),
            ["email", "id", "name", "order_number", "registered_at", "sante_member_id", "state_version", "status",
             "status_entered_at"],
        )
        self.assertEqual((row["name"], row["sante_member_id"]), (reg.member.full_name, "SA-1234567"))
        text = json.dumps(body)
        for personal in ("1990-05-17", "mariasantos90", "msantos1990", reg.member.mobile, "Rizal", reg.member.email):
            self.assertNotIn(personal, text)
        # the detail of the same member in the "all" list is the same minimal row
        self.assertNotIn("details", self.get(self.url("?filter=all")).json()["registrations"][0])

    def test_the_details_go_as_soon_as_the_row_is_registered(self):
        reg = self.in_registration()
        self.assertIn("details", self.get(self.url()).json()["registrations"][0])
        self.mark(reg)
        self.assertEqual(self.get(self.url()).json()["registrations"], [])  # not in "to register"

    def test_fields_the_retention_job_erased_stay_erased(self):
        reg = self.in_registration(purged=True)
        row = self.get(self.url()).json()["registrations"][0]
        self.assertEqual(
            (row["details"]["date_of_birth"], row["details"]["desired_username"],
             row["details"]["alternate_username"], row["details"]["details_erased"]),
            (None, "", "", True),
        )
        self.mark(reg)
        reg.refresh_from_db()
        self.assertEqual((reg.date_of_birth, reg.desired_username, reg.alternate_username), (None, "", ""))
        self.assertIsNotNone(reg.details_purged_at)

    def test_starting_never_restores_erased_fields(self):
        reg = make_registration(self.site, purged=True)
        self.act(reg, "start")
        reg.refresh_from_db()
        self.assertEqual((reg.date_of_birth, reg.desired_username, reg.alternate_username), (None, "", ""))

    def test_rows_in_other_statuses_are_not_listed_and_not_actionable(self):
        waiting = make_registration(self.site, "awaiting_buyer_details")
        self.assertEqual(self.get(self.url("?filter=all")).json()["registrations"], [])
        self.error(self.act(waiting, "start"), 409, "wrong_status")

    def test_there_is_no_export_of_these_fields(self):
        make_registration(self.site)
        for tail in ("export/", "export.csv", "?format=csv"):
            response = self.get(self.url(tail))
            self.assertNotIn("text/csv", response.get("Content-Type", ""), tail)
            if tail != "?format=csv":
                self.assertEqual(response.status_code, 404, tail)


class IsolationTests(RegistrationBase):
    def test_another_sites_registrations_are_not_listed_or_actionable(self):
        ben, ben_site, _, _ = self.other_site()
        theirs = make_registration(ben_site, "in_registration")
        mine = make_registration(self.site)
        listed = [r["id"] for r in self.get(self.url("?filter=all")).json()["registrations"]]
        self.assertEqual(listed, [mine.member.public_id])
        # Ana's own site id with Ben's member
        self.error(self.act(theirs, "start"), 404, "not_found")
        self.error(self.act(theirs, "registered", sante_member_id="SA-1"), 404, "not_found")
        # Ben's site id as Ana
        self.error(self.get(self.url("?filter=all", site_id=ben_site.public_id)), 404, "not_found")
        r = self.post(self.url(f"{theirs.member.public_id}/registered/", site_id=ben_site.public_id),
                      {"state_version": theirs.state_version, "sante_member_id": "SA-1"})
        self.error(r, 404, "not_found")
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, "in_registration")
        self.assertEqual(PackRegisteredNotice.objects.count(), 0)

    @override_settings(PACK_ENABLED=False)
    def test_the_switch_off_is_404(self):
        self.error(self.get(self.url()), 404, "not_found")
