"""The SMS settings routes under /staff/v1/sms/policy/: reached through the staff routes'
own origin and session checks, closed while SMS_GATEWAY_ENABLED is off, and always written
through services.change_policy, which the admin list shares for its audit."""
from unittest import mock

from django.test import override_settings

from apps.notifications import services, staff
from apps.notifications.models import SmsPolicy
from apps.notifications.tests.base import gateway_on, set_policy
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff

BASE = "/staff/v1/sms/policy/"
PATHS = ("get/", "set/")
GOOD = {"sending_stopped": True, "platform_daily_limit": 500, "site_daily_limit": 50}


@on_staff
@gateway_on
class PolicyRouteTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.identity = self.enrolled()

    def policy(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def entries(self):
        return list(OperatorAuditEntry.objects.filter(action="sms_policy_change"))

    def test_the_routes_are_in_the_table_and_are_posts(self):
        self.assertEqual(sorted(staff.ROUTES), ["sms/policy/get/", "sms/policy/set/"])
        for path in staff.ROUTES:
            self.assertEqual(staff_views.ROUTES[path][0], "POST")

    def test_another_origin_is_refused(self):
        for path in PATHS:
            self.assertError(
                self.policy(path, GOOD, origin="https://evil.example"), 403, "origin_not_allowed"
            )

    def test_no_session_and_a_session_before_the_second_step_are_refused(self):
        for path in PATHS:
            self.assertError(self.policy(path, GOOD), 401, "unauthenticated")
        self.staff_email_step()
        for path in PATHS:
            self.assertError(self.policy(path, GOOD), 401, "second_step_required")
        self.assertFalse(SmsPolicy.objects.exists())
        self.assertEqual(self.entries(), [])

    @override_settings(SMS_GATEWAY_ENABLED=False)
    def test_switched_off_both_routes_are_404_and_touch_nothing(self):
        self.staff_sign_in()
        for path in PATHS:
            self.assertError(self.policy(path, GOOD), 404, "not_found")
        self.assertFalse(SmsPolicy.objects.exists())
        self.assertEqual(self.entries(), [])

    def test_get_answers_the_settings_and_creates_the_row_when_missing(self):
        self.staff_sign_in()
        response = self.policy("get/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        body = response.json()
        self.assertEqual(
            {k: body[k] for k in GOOD},
            {"sending_stopped": False, "platform_daily_limit": 2000, "site_daily_limit": 2000},
        )
        self.assertIn("updated_at", body)
        self.assertEqual(self.entries(), [])

    def test_set_saves_through_the_service_and_audits_what_changed(self):
        self.staff_sign_in()
        set_policy(platform_daily_limit=500)
        with mock.patch.object(
            staff.services, "change_policy", wraps=services.change_policy
        ) as service:
            response = self.policy("set/", GOOD)
        self.assertEqual(response.status_code, 200)
        service.assert_called_once()
        self.assertEqual(service.call_args.args[0], self.identity.user)
        row = SmsPolicy.objects.get(pk=1)
        self.assertEqual(
            (row.sending_stopped, row.platform_daily_limit, row.site_daily_limit), (True, 500, 50)
        )
        self.assertEqual(response.json()["site_daily_limit"], 50)
        (entry,) = self.entries()
        self.assertEqual(entry.operator, self.identity.user)
        self.assertEqual(entry.target_type, "sms_policy")
        self.assertEqual(entry.detail, {
            "before": {"sending_stopped": False, "site_daily_limit": 2000},
            "after": {"sending_stopped": True, "site_daily_limit": 50},
        })

    def test_saving_the_same_values_writes_no_audit_entry(self):
        self.staff_sign_in()
        self.policy("set/", GOOD)
        self.assertEqual(self.policy("set/", GOOD).status_code, 200)
        self.assertEqual(len(self.entries()), 1)

    def test_bad_values_are_refused_and_change_nothing(self):
        self.staff_sign_in()
        set_policy(platform_daily_limit=700)
        bad = {
            "sending_stopped": ("yes", 1, None),
            "platform_daily_limit": (-1, "5", 1.5, True, None, 2147483648),
            "site_daily_limit": (-1, "5", 1.5, True, None, 2147483648),
        }
        for field, values in bad.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    response = self.policy("set/", {**GOOD, field: value})
                    self.assertError(response, 400, "invalid_request")
                    self.assertEqual(list(response.json()["error"]["fields"]), [field])
        self.assertError(self.policy("set/", {}), 400, "invalid_request")
        row = SmsPolicy.objects.get(pk=1)
        self.assertEqual((row.sending_stopped, row.platform_daily_limit), (False, 700))
        self.assertEqual(self.entries(), [])
