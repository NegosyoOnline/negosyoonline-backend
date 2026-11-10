"""Two-step sign-in on the Django admin, behind ADMIN_TWO_STEP_REQUIRED (T-ADM)."""
import re
from datetime import timedelta
from unittest import mock
from urllib.parse import quote

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.operators import two_step
from apps.operators.admin_site import FLAG, TwoStepAdminSite
from apps.operators.models import AdminIdentity, AdminSession, OperatorAuditEntry
from apps.operators.tests.base import PASSWORD, TwoStepTestCase

CODE_URL = "/admin/two-step/"
SIGN_OUT_URL = "/admin/two-step/sign-out/"
USERS_URL = "/admin/operators/user/"
GENERIC_ERROR = "That code did not work. Try again."
S7_SENTENCE = (
    "Your password is accepted. Enter the 6-digit code from your authenticator app to open "
    "the operator admin."
)
S8_SENTENCE = "Two-step sign-in is not set up for this account. Ask the admin owner to set it up."


class AdminTestCase(TwoStepTestCase):
    def get(self, path, **extra):
        return self.client.get(path, secure=True, **extra)

    def post(self, path, data=None, **extra):
        return self.client.post(path, data or {}, secure=True, **extra)

    def walk(self, path):
        """Follow redirects by hand: the test client's own follow drops `secure`."""
        response, hops = self.get(path), []
        while response.status_code in (301, 302, 303) and len(hops) < 6:
            hops.append(response["Location"])
            response = self.get(response["Location"])
        return response, hops

    def password_in(self, username="operator"):
        response = self.post(
            "/admin/login/", {"username": username, "password": PASSWORD, "next": "/admin/"}
        )
        self.assertEqual(response.status_code, 302)

    def enrolled_and_in(self):
        identity = self.enrolled()
        self.password_in()
        return identity

    def pass_code(self, next_url="/admin/", offset=0):
        response = self.post(CODE_URL, {"code": self.code(offset), "next": next_url})
        self.assertEqual(response.status_code, 303)
        return response

    def flag(self):
        return self.client.session.get(FLAG)


class SwitchOffTests(AdminTestCase):
    def test_the_admin_uses_the_two_step_site(self):
        self.assertIsInstance(admin.site, TwoStepAdminSite)
        self.assertIn(get_user_model(), admin.site._registry)

    def test_with_the_switch_off_the_admin_behaves_as_before(self):
        self.enrolled()
        self.password_in()
        self.assertEqual(self.get("/admin/").status_code, 200)
        self.assertEqual(self.get(USERS_URL).status_code, 200)
        self.assertIsNone(self.flag())
        response = self.get(CODE_URL)
        self.assertEqual((response.status_code, response["Location"]), (302, "/admin/"))
        response = self.post(CODE_URL, {"code": self.code()})
        self.assertEqual((response.status_code, response["Location"]), (302, "/admin/"))
        self.assertFalse(OperatorAuditEntry.objects.exists())
        self.assertEqual(self.post("/admin/logout/").status_code, 200)
        self.assertFalse(OperatorAuditEntry.objects.exists())


@override_settings(ADMIN_TWO_STEP_REQUIRED=True)
class SwitchOnTests(AdminTestCase):
    def test_password_alone_opens_nothing(self):
        self.enrolled_and_in()
        for path in ("/admin/", USERS_URL, "/admin/password_change/", "/admin/operators/user/add/"):
            response, hops = self.walk(path)
            self.assertEqual(hops[-1], f"{CODE_URL}?next={quote(path, safe='/')}", path)
            self.assertEqual(sum(url.startswith(CODE_URL) for url in hops), 1)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, S7_SENTENCE)
            self.assertNotContains(response, "Site administration")

    def test_the_code_page(self):
        self.enrolled_and_in()
        response = self.get(f"{CODE_URL}?next={USERS_URL}")
        html = response.content.decode()
        self.assertContains(response, "<title>Two-step sign-in | NegosyoOnline admin</title>", html=True)
        self.assertContains(response, "<h1>Two-step sign-in</h1>", html=True)
        self.assertContains(response, S7_SENTENCE)
        self.assertContains(response, "Authenticator code")
        self.assertContains(response, "Verify")
        self.assertContains(response, "Use a recovery code instead")
        self.assertIn(f'<input type="hidden" name="next" value="{USERS_URL}">', html)
        self.assertIn(f'href="{CODE_URL}?method=recovery&amp;next={quote(USERS_URL, safe="")}"', html)
        self.assertIn(f'<form method="post" action="{SIGN_OUT_URL}">', html)
        self.assertContains(response, "Sign out")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        for needle in ("<script", "http://", "https://", "<link", "<img", "Django"):
            self.assertNotIn(needle, html)
        for attribute in ('inputmode="numeric"', 'pattern="[0-9]{6}"', 'maxlength="6"',
                          'autocomplete="one-time-code"', "required", "autofocus"):
            self.assertIn(attribute, html)

        response = self.get(f"{CODE_URL}?method=recovery&next={USERS_URL}")
        html = response.content.decode()
        self.assertContains(response, "Recovery code")
        self.assertContains(response, "Use your authenticator app instead")
        self.assertIn(f'<input type="hidden" name="next" value="{USERS_URL}">', html)
        self.assertIn(f'href="{CODE_URL}?next={quote(USERS_URL, safe="")}"', html)
        for attribute in ('autocapitalize="characters"', 'autocomplete="off"', 'maxlength="12"'):
            self.assertIn(attribute, html)

    def test_the_right_code_opens_the_admin(self):
        identity = self.enrolled_and_in()
        before = self.client.session.session_key
        response = self.pass_code(USERS_URL)
        self.assertEqual(response["Location"], USERS_URL)
        self.assertNotEqual(self.client.session.session_key, before)  # rotated
        flag = self.flag()
        self.assertEqual(
            (flag["identity_id"], flag["credential_version"]), (identity.pk, identity.credential_version)
        )
        self.assertAlmostEqual(
            flag["exp"], (timezone.now() + timedelta(hours=12)).timestamp(), delta=5
        )
        self.assertEqual(self.get(USERS_URL).status_code, 200)
        self.assertEqual(self.get("/admin/").status_code, 200)
        entry = OperatorAuditEntry.objects.get()
        self.assertEqual(entry.action, "admin_sign_in_ok")
        self.assertEqual(entry.detail, {"surface": "django_admin", "step": "totp"})
        # With a valid flag the code page and the login page have nothing to ask.
        response = self.get(f"{CODE_URL}?next={USERS_URL}")
        self.assertEqual((response.status_code, response["Location"]), (303, USERS_URL))
        self.assertEqual(self.get("/admin/login/")["Location"], "/admin/")

    def test_a_recovery_code_opens_the_admin_once(self):
        self.enrolled_and_in()
        code = self.recovery_codes[3]
        response = self.post(f"{CODE_URL}?method=recovery", {"code": code.lower(), "next": "/admin/"})
        self.assertEqual(response.status_code, 303)
        self.post(SIGN_OUT_URL)
        self.password_in()
        response = self.post(f"{CODE_URL}?method=recovery", {"code": code, "next": "/admin/"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, GENERIC_ERROR)
        self.assertContains(response, "Recovery code")
        self.assertIsNone(self.flag())

    def test_an_unsafe_or_missing_next_goes_to_the_admin_index(self):
        self.enrolled_and_in()
        for offset, next_url in enumerate(
            ("https://evil.example/", "//evil.example/admin/", "javascript:alert(1)", "", None)
        ):
            self.client.session.flush()
            self.password_in()
            AdminIdentity.objects.update(totp_last_step=0)
            data = {"code": self.code()}
            if next_url is not None:
                data["next"] = next_url
            response = self.post(CODE_URL, data)
            self.assertEqual((response.status_code, response["Location"]), (303, "/admin/"), next_url)
            page = self.get(f"{CODE_URL}?next={quote(next_url or '', safe='')}")
            self.assertEqual(page["Location"], "/admin/")

    def test_without_an_enrolled_identity_the_admin_is_closed(self):
        def nothing(user):
            pass

        def disabled(user):
            identity = self.enrolled(f"{user.username}@example.test", user)
            identity.is_active = False
            identity.save()

        def unenrolled(user):
            AdminIdentity.objects.create(user=user, email=f"{user.username}@example.test")

        def reset(user):
            identity = self.enrolled(f"{user.username}@example.test", user)
            two_step.clear_enrolment(identity, timezone.now())

        for case in (nothing, disabled, unenrolled, reset):
            with self.subTest(case.__name__):
                user = self.staff(case.__name__)
                case(user)
                self.client.session.flush()
                self.password_in(user.username)
                response, hops = self.walk("/admin/")
                self.assertEqual(hops[-1], f"{CODE_URL}?next=/admin/")
                self.assertEqual(response.status_code, 403)
                html = response.content.decode()
                self.assertIn("<title>Two-step sign-in not set up | NegosyoOnline admin</title>", html)
                self.assertIn("<h1>Two-step sign-in is not set up</h1>", html)
                self.assertIn(S8_SENTENCE, html)
                self.assertIn(f'<form method="post" action="{SIGN_OUT_URL}">', html)
                self.assertNotIn("<a ", html)
                self.assertNotIn("admin_identity", html)
                self.assertNotIn('name="code"', html)
                response = self.post(CODE_URL, {"code": "123456"})
                self.assertEqual(response.status_code, 403)
                self.assertIsNone(self.flag())
                self.assertEqual(response["Cache-Control"], "no-store")

    def test_another_users_identity_is_never_used(self):
        self.enrolled()
        self.staff("second")
        self.password_in("second")
        self.assertEqual(self.get(CODE_URL).status_code, 403)
        session = self.client.session
        mine = AdminIdentity.objects.get()
        session[FLAG] = {
            "identity_id": mine.pk, "credential_version": mine.credential_version,
            "exp": (timezone.now() + timedelta(hours=1)).timestamp(),
        }
        session.save()
        response, _hops = self.walk("/admin/")
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.flag())

    def test_wrong_replayed_and_used_codes_get_one_error(self):
        self.enrolled_and_in()
        self.pass_code()
        used = self.code()
        self.post(SIGN_OUT_URL)
        self.password_in()
        for code in (used, self.wrong_code(), "", "abc"):
            response = self.post(CODE_URL, {"code": code, "next": USERS_URL})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, GENERIC_ERROR)
            self.assertContains(response, 'aria-invalid="true"')
            self.assertIn(f'name="next" value="{USERS_URL}"', response.content.decode())
            self.assertIsNone(self.flag())
        self.assertEqual(self.get("/admin/").status_code, 302)

    @override_settings(ADMIN_SECRETS_KEY="")
    def test_with_no_secrets_key_the_error_is_generic_and_nothing_is_counted(self):
        from apps.operators.tests.base import TEST_KEY

        with override_settings(ADMIN_SECRETS_KEY=TEST_KEY):
            identity = self.enrolled_and_in()
            code = self.code()
        with self.assertLogs("apps.operators", level="ERROR"):
            response = self.post(CODE_URL, {"code": code})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, GENERIC_ERROR)
        identity.refresh_from_db()
        self.assertEqual((identity.failed_attempts, identity.locked_until), (0, None))

    def test_the_fifth_wrong_code_pauses_both_surfaces(self):
        identity = self.enrolled_and_in()
        hq_session = AdminSession.objects.create(
            identity=identity, token_hash="t", credential_version=identity.credential_version,
            stage="verified", expires_at=timezone.now() + timedelta(hours=12),
        )
        for _ in range(4):
            self.assertContains(self.post(CODE_URL, {"code": self.wrong_code()}), GENERIC_ERROR)
        with self.committed():
            response = self.post(CODE_URL, {"code": self.wrong_code()})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("<h1>Sign-in paused</h1>", html)
        self.assertIn("Too many wrong codes. Sign-in is paused for 15 minutes.", html)
        self.assertIn(f'<form method="post" action="{SIGN_OUT_URL}">', html)
        self.assertIn("Sign out", html)
        self.assertNotIn("Back to sign-in", html)
        self.assertNotIn("<a ", html)
        self.assertIsNone(self.flag())
        self.assertEqual([m.subject for m in mail.outbox], ["NegosyoOnline admin sign-in paused"])
        hq_session.refresh_from_db()
        self.assertIsNotNone(hq_session.revoked_at)
        # While paused the code page shows the paused page, also for the right code.
        self.assertContains(self.get(CODE_URL), "Sign-in paused")
        self.assertContains(self.post(CODE_URL, {"code": self.code()}), "Sign-in paused")
        self.assertIsNone(self.flag())
        self.assertEqual(self.get("/admin/").status_code, 302)

    def test_a_stale_flag_is_dropped_with_one_redirect_and_no_loop(self):
        def lock(identity):
            identity.locked_until = timezone.now() + timedelta(minutes=15)
            two_step.bump_credentials(identity, timezone.now())
            identity.save()
            return 200, "Sign-in paused"

        def lock_ended(identity):
            two_step.bump_credentials(identity, timezone.now())
            identity.save()
            return 200, S7_SENTENCE

        def reset_then_enrol(identity):
            two_step.clear_enrolment(identity, timezone.now())
            two_step.store_enrolment(
                identity, self.secret, two_step.new_recovery_codes(), timezone.now()
            )
            return 200, S7_SENTENCE

        def disable(identity):
            identity.is_active = False
            identity.save()
            return 403, S8_SENTENCE

        def expire(identity):
            session = self.client.session
            session[FLAG] = {**session[FLAG], "exp": timezone.now().timestamp() - 1}
            session.save()
            return 200, S7_SENTENCE

        for n, change in enumerate((lock, lock_ended, reset_then_enrol, disable, expire)):
            with self.subTest(change.__name__):
                self.client.session.flush()
                user = self.staff(f"op{n}")
                identity = self.enrolled(f"op{n}@example.test", user)
                self.password_in(user.username)
                self.pass_code()
                self.assertEqual(self.get(USERS_URL).status_code, 200)
                status, sentence = change(identity)
                response, hops = self.walk(USERS_URL)
                self.assertEqual(hops[-1], f"{CODE_URL}?next={USERS_URL}")
                self.assertEqual(sum(url.startswith(CODE_URL) for url in hops), 1)
                self.assertEqual(response.status_code, status)
                self.assertContains(response, sentence, status_code=status)
                self.assertIsNone(self.flag())
                self.assertEqual(self.get(CODE_URL).status_code, status)  # stays put

    def test_a_lock_between_the_check_and_the_flag_leaves_a_refused_flag(self):
        identity = self.enrolled_and_in()
        real = two_step.verify_second_step

        def verify_then_lock(*args, **kwargs):
            result = real(*args, **kwargs)
            row = AdminIdentity.objects.get(pk=identity.pk)
            two_step.bump_credentials(row, timezone.now())
            row.save()
            return result

        with mock.patch("apps.operators.admin_views.two_step.verify_second_step", verify_then_lock):
            self.pass_code()
        response, _hops = self.walk("/admin/")
        self.assertContains(response, S7_SENTENCE)
        self.assertIsNone(self.flag())

    def test_sign_out_is_post_only_and_audited(self):
        identity = self.enrolled_and_in()
        self.pass_code()
        self.assertEqual(self.get(SIGN_OUT_URL).status_code, 405)
        self.assertIsNotNone(self.flag())
        response = self.post(SIGN_OUT_URL)
        self.assertEqual((response.status_code, response["Location"]), (303, "/admin/login/"))
        self.assertIsNone(self.flag())
        self.assertNotIn("_auth_user_id", self.client.session)
        entry = OperatorAuditEntry.objects.filter(action="admin_sign_out").get()
        self.assertEqual((entry.operator, entry.target_id), (identity.user, str(identity.pk)))
        self.assertEqual(entry.detail, {"surface": "django_admin"})
        response, _hops = self.walk("/admin/")
        self.assertContains(response, 'name="password"')

    def test_the_admins_own_log_out_is_audited_too(self):
        self.enrolled_and_in()
        self.pass_code()
        self.assertEqual(self.post("/admin/logout/").status_code, 200)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="admin_sign_out").count(), 1)

    def test_sign_out_without_an_identity_writes_no_row(self):
        self.staff()
        self.password_in()
        self.assertEqual(self.post(SIGN_OUT_URL).status_code, 303)
        self.assertFalse(OperatorAuditEntry.objects.exists())

    def test_sign_out_needs_the_csrf_token(self):
        from django.test import Client

        self.enrolled()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(get_user_model().objects.get())
        self.assertEqual(strict.post(SIGN_OUT_URL, secure=True).status_code, 403)
        self.assertEqual(strict.post(CODE_URL, {"code": self.code()}, secure=True).status_code, 403)
        page = strict.get(CODE_URL, secure=True)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        response = strict.post(
            CODE_URL, {"code": self.code(), "csrfmiddlewaretoken": token}, secure=True,
            HTTP_ORIGIN="https://testserver",
        )
        self.assertEqual(response.status_code, 303)

    def test_a_visitor_who_is_not_staff_gets_the_normal_login(self):
        self.enrolled()
        get_user_model().objects.create_user(username="plain", password=PASSWORD)
        for sign_in in (lambda: None, lambda: self.client.login(username="plain", password=PASSWORD)):
            sign_in()
            response, hops = self.walk("/admin/")
            self.assertEqual(hops, ["/admin/login/?next=/admin/"])
            self.assertContains(response, 'name="password"')
            response = self.get(f"{CODE_URL}?next={USERS_URL}")
            self.assertEqual(
                (response.status_code, response["Location"]),
                (302, f"/admin/login/?next={quote(USERS_URL, safe='/')}"),
            )
            response = self.post(CODE_URL, {"code": self.code()})
            self.assertEqual(response.status_code, 302)
            self.assertIsNone(self.flag())

    def test_a_code_used_on_one_surface_is_refused_on_the_other(self):
        identity = self.enrolled_and_in()
        self.assertTrue(two_step.verify_second_step(identity.pk, self.code(), surface="hq").ok)
        self.assertContains(self.post(CODE_URL, {"code": self.code()}), GENERIC_ERROR)
        self.pass_code(offset=1)
        self.assertFalse(two_step.verify_second_step(identity.pk, self.code(1), surface="hq").ok)
        recovery = self.recovery_codes[0]
        self.assertTrue(two_step.verify_second_step(identity.pk, recovery, surface="hq").ok)
        self.post(SIGN_OUT_URL)
        self.password_in()
        self.assertContains(
            self.post(f"{CODE_URL}?method=recovery", {"code": recovery}), GENERIC_ERROR
        )
