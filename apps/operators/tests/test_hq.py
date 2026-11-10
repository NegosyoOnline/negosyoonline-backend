"""hq: host routing, the sign-in pages, the signed-in page and sign-out (T-HQ, T-HOST)."""
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.checks import Error
from django.test import Client, override_settings
from django.utils import timezone

from apps.notifications.models import EmailMessage
from apps.operators import audit, checks, hq_auth, two_step
from apps.operators.models import AdminIdentity, AdminSession, AdminSignInChallenge, OperatorAuditEntry
from apps.operators.tests.base import EMAIL, TwoStepTestCase

HQ = "hq.example.test"
OTHER = "api.example.test"
SESSION, PRE = "__Host-no_hq", "__Host-no_hq_pre"
FORBIDDEN_WORDS = ("twc", "merchant", "member", "shopper", "client")
GENERIC_CODE_ERROR = "That code did not work. Check it, or get a new code."
GENERIC_VERIFY_ERROR = "That code did not work. Try again."


@override_settings(HQ_HOST=HQ, ALLOWED_HOSTS=[HQ, OTHER, "testserver"])
class HqTestCase(TwoStepTestCase):
    def get(self, path, client=None, host=HQ):
        return (client or self.client).get(path, secure=True, HTTP_HOST=host)

    def post(self, path, data=None, client=None, host=HQ):
        return (client or self.client).post(path, data or {}, secure=True, HTTP_HOST=host)

    def emailed_code(self):
        return re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)

    def request_code(self, email=EMAIL):
        response = self.post("/sign-in/", {"email": email})
        self.assertEqual((response.status_code, response["Location"]), (303, "/code/"))
        return response

    def pass_email_step(self, email=EMAIL):
        self.request_code(email)
        response = self.post("/code/", {"code": self.emailed_code()})
        self.assertEqual((response.status_code, response["Location"]), (303, "/verify/"))
        return response

    def sign_in(self, email=EMAIL, offset=0):
        self.pass_email_step(email)
        response = self.post("/verify/", {"code": self.code(offset)})
        self.assertEqual((response.status_code, response["Location"]), (303, "/"))
        return response

    def assertGoesTo(self, response, path):
        self.assertEqual((response.status_code, response["Location"]), (303, path))


class HqStageTests(HqTestCase):
    def test_home_without_a_session_goes_to_sign_in(self):
        self.assertGoesTo(self.get("/"), "/sign-in/")

    def test_code_page_without_a_pre_cookie_goes_to_sign_in(self):
        self.assertGoesTo(self.get("/code/"), "/sign-in/")
        self.assertGoesTo(self.post("/code/", {"code": "123456"}), "/sign-in/")

    def test_a_forged_or_stale_pre_cookie_goes_to_sign_in(self):
        self.enrolled()
        self.request_code()
        value = self.client.cookies[PRE].value
        self.client.cookies[PRE] = value[:-2] + "xx"
        self.assertGoesTo(self.get("/code/"), "/sign-in/")
        self.client.cookies[PRE] = value
        with mock.patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 601):
            self.assertGoesTo(self.get("/code/"), "/sign-in/")

    def test_verify_needs_the_email_step(self):
        self.enrolled()
        self.assertGoesTo(self.get("/verify/"), "/sign-in/")
        self.request_code()
        self.assertGoesTo(self.get("/verify/"), "/sign-in/")
        self.assertGoesTo(self.post("/verify/", {"code": self.code()}), "/sign-in/")

    def test_email_step_alone_does_not_open_home(self):
        self.enrolled()
        self.pass_email_step()
        self.assertGoesTo(self.get("/"), "/verify/")
        self.assertEqual(self.get("/verify/").status_code, 200)

    def test_a_verified_session_skips_the_sign_in_pages(self):
        self.enrolled()
        self.sign_in()
        for path in ("/sign-in/", "/code/", "/verify/", "/verify/?method=recovery"):
            self.assertGoesTo(self.get(path), "/")

    def test_the_full_path(self):
        identity = self.enrolled()
        page = self.get("/sign-in/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "<h1>Admin sign-in</h1>", html=True)
        self.assertContains(page, "Enter your admin email. We will send you a 6-digit code.")
        self.assertContains(page, "<title>Sign in | NegosyoOnline admin</title>", html=True)

        self.request_code()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        code = self.emailed_code()
        self.assertEqual(message.to, [EMAIL])
        self.assertEqual(message.subject, "Your NegosyoOnline admin sign-in code")
        self.assertEqual(
            message.body,
            f"Your NegosyoOnline admin sign-in code is {code}. It expires in 10 minutes. "
            "If you did not ask for it, ignore this email.",
        )
        self.assertTrue(EmailMessage.objects.filter(category="admin_signin_code").exists())
        challenge = AdminSignInChallenge.objects.get()
        self.assertNotIn(code, challenge.code_hmac)

        page = self.get("/code/")
        self.assertContains(page, "If this address is allowed, a code was sent. It expires in 10 minutes.")
        self.assertContains(page, f"Code sent to {EMAIL}.")
        self.assertContains(page, "Check your inbox and spam folder.")
        self.assertContains(page, "Use a different email or get a new code")

        response = self.post("/code/", {"code": code})
        self.assertGoesTo(response, "/verify/")
        first_token = response.cookies[SESSION].value
        session = AdminSession.objects.get()
        self.assertEqual(session.stage, "email_ok")
        self.assertNotEqual(session.token_hash, first_token)
        self.assertEqual(len(session.token_hash), 64)

        page = self.get("/verify/")
        self.assertContains(page, "Enter the 6-digit code from your authenticator app.")
        self.assertContains(page, "Use a recovery code instead")
        page = self.get("/verify/?method=recovery")
        self.assertContains(page, "Enter one of your 10 recovery codes. Each code works once.")
        self.assertContains(page, "Use your authenticator app instead")

        response = self.post("/verify/", {"code": self.code()})
        self.assertGoesTo(response, "/")
        self.assertNotEqual(response.cookies[SESSION].value, first_token)  # rotated
        session.refresh_from_db()
        self.assertEqual(session.stage, "verified")
        self.assertEqual(session.credential_version, identity.credential_version)
        self.assertAlmostEqual(
            session.expires_at - session.created_at, timedelta(hours=12), delta=timedelta(seconds=2)
        )

        # The old token is dead.
        stale = Client()
        stale.cookies[SESSION] = first_token
        self.assertGoesTo(self.get("/", client=stale), "/sign-in/")

        page = self.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "<h1>Admin console</h1>", html=True)
        self.assertContains(page, f"Signed in as {EMAIL}.")

    def test_a_recovery_code_passes_the_second_step_once(self):
        self.enrolled()
        self.pass_email_step()
        code = self.recovery_codes[0]
        typed = f"{code[:5].lower()}-{code[5:]}"
        self.assertGoesTo(self.post("/verify/?method=recovery", {"code": typed}), "/")
        self.post("/sign-out/")
        mail.outbox.clear()
        self.pass_email_step()
        response = self.post("/verify/?method=recovery", {"code": code})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, GENERIC_VERIFY_ERROR)
        self.assertContains(response, "Recovery code")  # stays in recovery mode

    def test_wrong_and_replayed_authenticator_codes_are_refused(self):
        self.enrolled()
        self.sign_in()
        used = self.code()
        self.post("/sign-out/")
        self.pass_email_step()
        for code in (used, self.wrong_code()):
            response = self.post("/verify/", {"code": code})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, GENERIC_VERIFY_ERROR)
            self.assertContains(response, 'aria-invalid="true"')
        self.assertGoesTo(self.get("/"), "/verify/")


class HqEmailCodeTests(HqTestCase):
    def assertGenericError(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, GENERIC_CODE_ERROR)
        self.assertNotIn(SESSION, response.cookies)
        self.assertFalse(AdminSession.objects.exists())

    def wrong_email_code(self):
        return "000000" if self.emailed_code() != "000000" else "111111"

    def test_a_wrong_code_is_refused_and_audited(self):
        identity = self.enrolled()
        self.request_code()
        self.assertGenericError(self.post("/code/", {"code": self.wrong_email_code()}))
        entry = OperatorAuditEntry.objects.get()
        self.assertEqual(entry.action, "admin_sign_in_failed")
        self.assertEqual(entry.detail, {"surface": "hq", "step": "email"})
        self.assertEqual((entry.operator, entry.target_id), (identity.user, str(identity.pk)))
        self.assertEqual(entry.personal, {})

    def test_a_malformed_code_is_refused(self):
        self.enrolled()
        self.request_code()
        for code in ("", "12345", "abcdef", "1234567"):
            self.assertGenericError(self.post("/code/", {"code": code}))

    def test_an_expired_code_is_refused(self):
        self.enrolled()
        self.request_code()
        AdminSignInChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertGenericError(self.post("/code/", {"code": self.emailed_code()}))

    def test_a_code_works_once(self):
        self.enrolled()
        self.request_code()
        pre = self.client.cookies[PRE].value
        self.assertGoesTo(self.post("/code/", {"code": self.emailed_code()}), "/verify/")
        other = Client()
        other.cookies[PRE] = pre
        response = self.post("/code/", {"code": self.emailed_code()}, client=other)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, GENERIC_CODE_ERROR)
        self.assertEqual(AdminSession.objects.count(), 1)

    def test_the_sixth_try_is_refused_even_when_right(self):
        self.enrolled()
        self.request_code()
        for _ in range(5):
            self.assertGenericError(self.post("/code/", {"code": self.wrong_email_code()}))
        self.assertGenericError(self.post("/code/", {"code": self.emailed_code()}))
        self.assertEqual(AdminSignInChallenge.objects.get().attempts, 5)

    def test_a_new_code_replaces_the_old_one(self):
        self.enrolled()
        self.request_code()
        old_code, old_pre = self.emailed_code(), self.client.cookies[PRE].value
        self.request_code()
        stale = Client()
        stale.cookies[PRE] = old_pre
        response = self.post("/code/", {"code": old_code}, client=stale)
        self.assertContains(response, GENERIC_CODE_ERROR)
        self.assertGoesTo(self.post("/code/", {"code": self.emailed_code()}), "/verify/")

    def test_a_code_is_bound_to_the_browser_that_asked(self):
        self.enrolled()
        self.request_code()
        code = self.emailed_code()
        other = Client()
        self.post("/sign-in/", {"email": EMAIL}, client=other)  # replaces the code
        self.assertGenericError(self.post("/code/", {"code": code}))

    def test_the_code_page_shows_the_address_as_typed(self):
        self.enrolled()
        self.request_code("  Admin@Example.TEST ")
        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        self.assertContains(self.get("/code/"), "Code sent to Admin@Example.TEST.")
        error = self.post("/code/", {"code": self.wrong_email_code()})
        self.assertContains(error, "Code sent to Admin@Example.TEST.")
        self.assertGoesTo(self.post("/code/", {"code": self.emailed_code()}), "/verify/")

    def test_an_unknown_address_gets_the_same_error(self):
        self.request_code("nobody@example.test")
        self.assertEqual(mail.outbox, [])
        self.assertGenericError(self.post("/code/", {"code": "123456"}))
        self.assertFalse(OperatorAuditEntry.objects.exists())

    def test_a_locked_identity_gets_the_generic_error_not_the_paused_page(self):
        identity = self.enrolled()
        self.request_code()
        AdminIdentity.objects.filter(pk=identity.pk).update(
            locked_until=timezone.now() + timedelta(minutes=15)
        )
        response = self.post("/code/", {"code": self.emailed_code()})
        self.assertGenericError(response)
        self.assertNotContains(response, "Sign-in paused")
        mail.outbox.clear()
        self.request_code()
        self.assertEqual(mail.outbox, [])  # no code is sent while paused


class HqNoLeakTests(HqTestCase):
    """The answer at S1 never depends on whether the address is known."""

    def shape(self, response):
        cookie = response.cookies[PRE]
        attributes = {k: v for k, v in cookie.items() if v and k != "expires"}
        return (
            response.status_code, response["Location"], response.content,
            sorted(k.lower() for k in response.headers), sorted(response.cookies),
            attributes,
        )

    def submit(self, email):
        return self.shape(self.post("/sign-in/", {"email": email}, client=Client()))

    def test_every_well_formed_address_gets_the_same_answer(self):
        self.enrolled("known@example.test")
        self.enrolled("locked@example.test", self.staff("locked"))
        AdminIdentity.objects.filter(email="locked@example.test").update(
            locked_until=timezone.now() + timedelta(minutes=5)
        )
        AdminIdentity.objects.create(user=self.staff("off"), email="off@example.test", is_active=False)
        AdminIdentity.objects.create(user=self.staff("new"), email="unenrolled@example.test")
        known = self.submit("known@example.test")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(known[0:2], (303, "/code/"))
        self.assertEqual(
            known[5], {"path": "/", "max-age": 600, "secure": True, "httponly": True, "samesite": "Strict"}
        )
        for email in ("nobody@example.test", "off@example.test", "unenrolled@example.test",
                      "locked@example.test"):
            self.assertEqual(self.submit(email), known, email)
        self.assertEqual(len(mail.outbox), 1)

    def test_an_over_limit_address_gets_the_same_answer_and_no_mail(self):
        self.enrolled("known@example.test")
        answers = [self.submit("known@example.test") for _ in range(7)]
        self.assertEqual(len(set(map(repr, answers))), 1)
        self.assertEqual(len(mail.outbox), 5)

    def test_the_limit_is_also_per_ip(self):
        self.enrolled("known@example.test")
        for n in range(5):
            self.submit(f"someone{n}@example.test")
        self.submit("known@example.test")
        self.assertEqual(mail.outbox, [])

    def test_the_request_only_hands_the_work_over(self):
        self.enrolled()
        self.run_later.side_effect = None
        with self.assertNumQueries(0):
            response = self.post("/sign-in/", {"email": f"  {EMAIL.upper()} "})
        self.assertGoesTo(response, "/code/")
        self.run_later.assert_called_once()
        fn, email, nonce = self.run_later.call_args.args
        self.assertEqual((fn, email), (hq_auth.issue_email_code, EMAIL))
        self.assertTrue(nonce)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(AdminSignInChallenge.objects.exists())

    def test_a_failing_hand_over_changes_nothing(self):
        self.enrolled()
        normal = self.submit(EMAIL)
        self.run_later.side_effect = RuntimeError("no thread")
        with self.assertLogs("apps.operators", level="ERROR"):
            failed = self.submit(EMAIL)
        self.assertEqual(failed, normal)

    def test_a_failing_email_changes_nothing(self):
        self.enrolled()
        with mock.patch("apps.operators.hq_auth.send_email", side_effect=RuntimeError("smtp down")):
            # The real run_later logs and swallows; here the step runs inline, so it must not escape.
            self.run_later.side_effect = lambda fn, *args: None
            self.assertGoesTo(self.post("/sign-in/", {"email": EMAIL}), "/code/")

    @override_settings(ADMIN_SECRETS_KEY="")
    def test_with_no_secrets_key_nothing_is_sent_and_one_line_is_logged(self):
        with override_settings(ADMIN_SECRETS_KEY=self.settings_key()):
            self.enrolled()
            normal = self.submit(EMAIL)
            mail.outbox.clear()
        with self.assertLogs("apps.operators", level="ERROR") as logs:
            first = self.submit(EMAIL)
            self.submit("nobody@example.test")
        self.assertEqual(first, normal)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(len([line for line in logs.output if "admin-secrets-key-empty" in line]), 1)

    def settings_key(self):
        from apps.operators.tests.base import TEST_KEY
        return TEST_KEY

    def test_a_malformed_address_is_refused_before_any_counter(self):
        for email in ("", "   ", "not-an-address", "a@b", "x" * 250 + "@example.test"):
            with mock.patch("apps.operators.hq_auth.within_limits") as limits:
                response = self.post("/sign-in/", {"email": email})
            self.assertEqual(response.status_code, 200, email)
            self.assertContains(response, "Enter an email address.")
            self.assertContains(response, 'aria-invalid="true"')
            self.assertNotIn(PRE, response.cookies)
            limits.assert_not_called()
        self.run_later.assert_not_called()


class HqSessionTests(HqTestCase):
    def test_sign_out_is_post_only(self):
        self.enrolled()
        self.sign_in()
        self.assertEqual(self.get("/sign-out/").status_code, 405)
        self.assertEqual(self.get("/").status_code, 200)

    def test_sign_out_revokes_clears_and_audits(self):
        identity = self.enrolled()
        self.sign_in()
        token = self.client.cookies[SESSION].value
        response = self.post("/sign-out/")
        self.assertGoesTo(response, "/sign-in/")
        for name in (SESSION, PRE):
            self.assertEqual(response.cookies[name].value, "")
            self.assertEqual(response.cookies[name]["max-age"], 0)
        self.assertIsNotNone(AdminSession.objects.get().revoked_at)
        entry = OperatorAuditEntry.objects.filter(action="admin_sign_out").get()
        self.assertEqual((entry.operator, entry.target_id), (identity.user, str(identity.pk)))
        self.assertEqual(entry.detail, {"surface": "hq"})
        again = Client()
        again.cookies[SESSION] = token
        self.assertGoesTo(self.get("/", client=again), "/sign-in/")
        page = self.get("/sign-in/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "role=\"status\"")  # no banner

    def test_sign_out_without_a_session_just_goes_to_sign_in(self):
        self.assertGoesTo(self.post("/sign-out/"), "/sign-in/")
        self.assertFalse(OperatorAuditEntry.objects.exists())

    def test_every_post_needs_the_csrf_token(self):
        self.enrolled()
        self.sign_in()
        strict = Client(enforce_csrf_checks=True)
        strict.cookies[SESSION] = self.client.cookies[SESSION].value
        for path in ("/sign-in/", "/code/", "/verify/", "/sign-out/"):
            response = self.post(path, {"email": EMAIL, "code": "123456"}, client=strict)
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["X-Robots-Tag"], "noindex")
            self.assertNotIn("twc", response.content.decode().lower())
        self.assertEqual(self.get("/", client=strict).status_code, 200)  # still signed in

    def test_a_post_with_the_token_from_the_page_passes(self):
        strict = Client(enforce_csrf_checks=True)
        page = self.get("/sign-in/", client=strict)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        response = strict.post(
            "/sign-in/", {"email": EMAIL, "csrfmiddlewaretoken": token}, secure=True, HTTP_HOST=HQ,
            HTTP_ORIGIN=f"https://{HQ}",
        )
        self.assertGoesTo(response, "/code/")

    def test_five_wrong_codes_pause_and_end_the_session(self):
        identity = self.enrolled()
        self.pass_email_step()
        mail.outbox.clear()
        for _ in range(4):
            self.assertContains(self.post("/verify/", {"code": self.wrong_code()}), GENERIC_VERIFY_ERROR)
        with self.committed():
            response = self.post("/verify/", {"code": self.wrong_code()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<h1>Sign-in paused</h1>", html=True)
        self.assertContains(response, "Too many wrong codes. Sign-in is paused for 15 minutes.")
        self.assertContains(
            response, "We sent an email about this to your admin address. If this was not you, "
            "tell the admin owner.",
        )
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, '<a class="lnk" href="/sign-in/">Back to sign-in</a>', html=True)
        self.assertEqual(response.cookies[SESSION].value, "")
        self.assertEqual([m.subject for m in mail.outbox], ["NegosyoOnline admin sign-in paused"])
        self.assertGoesTo(self.get("/verify/"), "/sign-in/")
        identity.refresh_from_db()
        self.assertEqual(identity.credential_version, 3)  # enrol, then the lock

    def test_disable_reset_or_lock_elsewhere_ends_access_on_the_next_request(self):
        def disable(identity):
            identity.is_active = False

        def lock(identity):
            identity.locked_until = timezone.now() + timedelta(minutes=15)

        def demote(identity):
            identity.user.is_staff = False
            identity.user.save()

        def reset(identity):
            two_step.clear_enrolment(identity, timezone.now())

        def bump(identity):
            identity.credential_version += 1

        for n, change in enumerate((disable, lock, demote, reset, bump)):
            with self.subTest(change.__name__):
                self.client = Client()
                mail.outbox.clear()
                identity = self.enrolled(f"admin{n}@example.test", self.staff(f"admin{n}"))
                self.sign_in(identity.email, offset=0)
                self.assertEqual(self.get("/").status_code, 200)
                change(identity)
                identity.save()
                self.assertGoesTo(self.get("/"), "/sign-in/")

    def test_an_expired_session_ends(self):
        self.enrolled()
        self.sign_in()
        AdminSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertGoesTo(self.get("/"), "/sign-in/")

    def test_a_lock_between_the_check_and_the_session_leaves_a_dead_session(self):
        identity = self.enrolled()
        self.pass_email_step()
        real = two_step.verify_second_step

        def verify_then_lock(*args, **kwargs):
            result = real(*args, **kwargs)
            row = AdminIdentity.objects.get(pk=identity.pk)
            two_step.bump_credentials(row, timezone.now())
            row.save()
            return result

        with mock.patch("apps.operators.hq_views.two_step.verify_second_step", verify_then_lock):
            self.assertGoesTo(self.post("/verify/", {"code": self.code()}), "/")
        self.assertGoesTo(self.get("/"), "/sign-in/")

    def test_old_challenges_and_sessions_are_purged_when_a_code_is_issued(self):
        identity = self.enrolled()
        now = timezone.now()
        old_challenge = AdminSignInChallenge.objects.create(
            identity=identity, code_hmac="x", nonce="old", expires_at=now - timedelta(hours=25)
        )
        kept_challenge = AdminSignInChallenge.objects.create(
            identity=identity, code_hmac="x", nonce="kept", expires_at=now - timedelta(hours=23),
            consumed_at=now,
        )

        def session(token, **fields):
            return AdminSession.objects.create(
                identity=identity, token_hash=token, credential_version=1, **fields
            )

        old_expired = session("a", expires_at=now - timedelta(days=31))
        old_revoked = session("b", expires_at=now, revoked_at=now - timedelta(days=31))
        kept = session("c", expires_at=now - timedelta(days=29))
        self.request_code()
        self.assertFalse(AdminSignInChallenge.objects.filter(pk=old_challenge.pk).exists())
        self.assertTrue(AdminSignInChallenge.objects.filter(pk=kept_challenge.pk).exists())
        self.assertEqual(
            set(AdminSession.objects.values_list("pk", flat=True)), {kept.pk},
            (old_expired.pk, old_revoked.pk),
        )


class HqHomeTests(HqTestCase):
    def test_home_shows_the_card_the_events_and_nothing_else(self):
        other = self.enrolled("other@example.test", self.staff("other"))
        identity = self.enrolled()
        audit.record(
            other.user, "admin_sign_in_ok", target_type="admin_identity", target_id=other.pk,
            detail={"surface": "hq", "step": "totp"},
        )
        audit.record(
            identity.user, "admin_sign_out", target_type="admin_identity", target_id=identity.pk,
            detail={"surface": "django_admin"},
        )
        audit.record(
            identity.user, "admin_locked", target_type="admin_identity", target_id=identity.pk,
            detail={"surface": "django_admin", "step": "recovery"},
        )
        self.request_code()
        self.post("/code/", {"code": "000000" if self.emailed_code() != "000000" else "111111"})
        self.post("/code/", {"code": self.emailed_code()})
        self.post("/verify/", {"code": self.code()})
        page = self.get("/")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.content.decode()))
        self.assertContains(page, "<title>Admin console | NegosyoOnline admin</title>", html=True)
        self.assertContains(page, f"Signed in as {EMAIL}.")
        self.assertContains(page, "Recent sign-in activity")
        self.assertContains(
            page, "Your last 10 sign-in events on the admin console and the operator admin."
        )
        self.assertContains(page, "Verified")
        session = AdminSession.objects.get()
        local = timezone.localtime
        expected = (
            f"Two-step sign-in: verified at {self.manila(local(session.verified_at))}; "
            f"session ends {self.manila(local(session.expires_at))}."
        )
        self.assertIn(expected, text)
        self.assertEqual(str(local(session.verified_at).tzinfo), "Asia/Manila")
        # Newest first: this sign-in, the wrong email code, then the two older events.
        order = [text.index(label) for label in (
            "Signed in Admin console · authenticator code",
            "Wrong code Admin console · email code",
            "Sign-in paused Operator admin · recovery code",
            "Signed out Operator admin",
        )]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("Signed out Operator admin ·", text)  # a missing step and its separator
        self.assertEqual(page.content.decode().count('<li class="row">'), 4)  # not the other admin's
        self.assertContains(page, '<form method="post" action="/sign-out/">')
        self.assertContains(page, "Sign out")
        self.assertEqual(page.content.decode().count("<a "), 0)  # no links out
        self.assertNotContains(page, "127.0.0.1")

    def manila(self, moment):
        hour = moment.hour % 12 or 12
        return f"{moment.day} {moment:%b} {moment.year}, {hour}:{moment:%M} {moment:%p}"

    def test_home_lists_at_most_ten_events(self):
        identity = self.enrolled()
        for _ in range(14):
            audit.record(
                identity.user, "admin_sign_in_failed", target_type="admin_identity",
                target_id=identity.pk, detail={"surface": "hq", "step": "totp"},
            )
        self.sign_in()
        self.assertEqual(self.get("/").content.decode().count('<li class="row">'), 10)


class HqPageRuleTests(HqTestCase):
    def pages(self):
        """Every page hq can render, with the status it must have."""
        self.enrolled()
        yield "S1", self.get("/sign-in/"), 200
        yield "S1 error", self.post("/sign-in/", {"email": "nope"}), 200
        self.request_code()
        yield "S2", self.get("/code/"), 200
        yield "S2 error", self.post("/code/", {"code": "12"}), 200
        self.post("/code/", {"code": self.emailed_code()})
        yield "S3", self.get("/verify/"), 200
        yield "S3 recovery", self.get("/verify/?method=recovery"), 200
        yield "S3 error", self.post("/verify/", {"code": self.wrong_code()}), 200
        for _ in range(3):
            self.post("/verify/", {"code": self.wrong_code()})
        yield "S4", self.post("/verify/", {"code": self.wrong_code()}), 200
        AdminIdentity.objects.update(locked_until=None)
        mail.outbox.clear()
        self.sign_in()
        yield "S5", self.get("/"), 200
        yield "404", self.get("/nothing-here/"), 404

    def test_every_page_follows_the_rulebook(self):
        seen = []
        for name, response, status in self.pages():
            with self.subTest(name):
                seen.append(name)
                html = response.content.decode()
                self.assertEqual(response.status_code, status)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertEqual(response["X-Robots-Tag"], "noindex")
                self.assertIn('<meta name="robots" content="noindex">', html)
                self.assertIn('<meta name="color-scheme" content="light">', html)
                self.assertIn('<html lang="en">', html)
                self.assertIn("NegosyoOnline admin", html)
                self.assertEqual(html.count("<h1"), 1)
                lowered = html.lower()
                for needle in ("<script", "http://", "https://", 'src="', "url(", "@import", "<link",
                               "<img", "dm sans"):
                    self.assertNotIn(needle, lowered, needle)
                for word in FORBIDDEN_WORDS:
                    self.assertIsNone(re.search(rf"\b{word}s?\b", lowered), word)
                for label_for in re.findall(r'<label for="([^"]+)"', html):
                    self.assertIn(f'id="{label_for}"', html)
                self.assertEqual(len(re.findall(r"<input(?![^>]*hidden)", html)),
                                 len(re.findall(r"<label ", html)))
        self.assertEqual(len(seen), 10)

    def test_field_attributes(self):
        self.enrolled()
        s1 = self.get("/sign-in/").content.decode()
        for attribute in ('type="email"', 'name="email"', 'autocomplete="email"', 'autocapitalize="none"',
                          'spellcheck="false"', "required", 'maxlength="254"', "autofocus"):
            self.assertIn(attribute, s1)
        self.request_code()
        s2 = self.get("/code/").content.decode()
        self.post("/code/", {"code": self.emailed_code()})
        s3 = self.get("/verify/").content.decode()
        for html in (s2, s3):
            for attribute in ('type="text"', 'name="code"', 'inputmode="numeric"', 'pattern="[0-9]{6}"',
                              'maxlength="6"', 'autocomplete="one-time-code"', "required", "autofocus"):
                self.assertIn(attribute, html)
        recovery = self.get("/verify/?method=recovery").content.decode()
        for attribute in ('inputmode="text"', 'autocapitalize="characters"', 'autocomplete="off"',
                          'spellcheck="false"', 'maxlength="12"'):
            self.assertIn(attribute, recovery)
        error = self.post("/verify/", {"code": self.wrong_code()}).content.decode()
        self.assertIn('aria-describedby="code-msg"', error)
        self.assertIn('<p class="err" id="code-msg">', error)

    def test_an_email_is_escaped_on_the_code_page(self):
        self.post("/sign-in/", {"email": "\"<b>\"@example.test"})
        page = self.get("/code/")
        if page.status_code == 200:
            self.assertNotIn("<b>", page.content.decode())


class HqHostTests(HqTestCase):
    HQ_PATHS = ("/sign-in/", "/code/", "/verify/", "/sign-out/")

    def test_the_hq_host_serves_only_hq(self):
        for path in ("/admin/", "/admin/login/", "/admin/two-step/", "/healthz/", "/v1/openapi.json",
                     "/v1/account", "/sign-in"):
            response = self.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertContains(response, "Page not found.", status_code=404)
            self.assertEqual(response["Cache-Control"], "no-store")
        response = self.client.post(
            "/v1/auth/email/request", "{}", content_type="application/json", secure=True,
            HTTP_HOST=HQ, HTTP_ORIGIN="https://evil.example",
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_the_hq_host_matches_with_a_port_and_any_case(self):
        self.assertEqual(self.get("/sign-in/", host=f"{HQ.upper()}:8003").status_code, 200)

    def test_other_hosts_serve_no_hq_path(self):
        for host in (OTHER, "testserver"):
            for path in self.HQ_PATHS:
                self.assertEqual(self.get(path, host=host).status_code, 404, path)
            self.assertEqual(self.get("/healthz/", host=host).status_code, 200)
            response = self.get("/healthz/", host=host)
            self.assertNotIn("X-Robots-Tag", response.headers)

    @override_settings(HQ_HOST="")
    def test_with_no_hq_host_hq_does_not_exist(self):
        for host in (HQ, OTHER):
            for path in self.HQ_PATHS:
                self.assertEqual(self.get(path, host=host).status_code, 404, path)
            self.assertEqual(self.get("/healthz/", host=host).status_code, 200)

    def test_the_neutral_answers(self):
        from apps.operators import hq_views

        request = self.get("/sign-in/").wsgi_request
        for view, status, sentence in (
            (hq_views.bad_request, 400, "This page is not available right now."),
            (hq_views.forbidden, 403, "This page is not available right now."),
            (hq_views.not_found, 404, "Page not found."),
        ):
            response = view(request, Exception())
            self.assertEqual(response.status_code, status)
            self.assertContains(response, sentence, status_code=status)
        response = hq_views.server_error(request)
        self.assertContains(response, "This page is not available right now.", status_code=500)

    def test_a_server_error_on_hq_is_neutral_and_carries_the_headers(self):
        self.client.raise_request_exception = False
        with mock.patch("apps.operators.hq_views.page", side_effect=RuntimeError("boom")):
            response = self.get("/sign-in/")
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "This page is not available right now.", status_code=500)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_a_clashing_or_malformed_host_is_refused_and_hq_stays_off(self):
        cases = {
            "hq.example.test:8003": {},
            "https://hq.example.test": {},
            "hq.example.test/": {},
            "localhost": {},
            "dash.example.test": {"DASHBOARD_URL": "https://dash.example.test"},
            "www.example.test": {"SITE_URL": "https://www.example.test"},
            "app.example.test": {"API_ALLOWED_ORIGINS": ["https://x.test", "https://app.example.test"]},
            "mystore.example.test": {"SITE_PARENT_HOST": "example.test"},
            "example.test": {"SITE_PARENT_HOST": "example.test"},
        }
        for host, extra in cases.items():
            with self.subTest(host), override_settings(
                HQ_HOST=host, ALLOWED_HOSTS=["*"], **extra
            ), self.assertLogs("apps.operators", level="ERROR"):
                self.assertTrue(checks.hq_host_problem())
                self.assertEqual(checks.active_hq_host(), "")
                errors = checks.check_hq_host(None)
                self.assertEqual([type(e) for e in errors], [Error])
                self.assertEqual(errors[0].id, "operators.E001")
                bare = host.split("/")[-1].split(":")[0] or "hq.example.test"
                self.assertEqual(self.get("/sign-in/", host=bare).status_code, 404)

    def test_a_good_host_passes_the_check(self):
        with override_settings(SITE_PARENT_HOST="example.test"):
            self.assertEqual(checks.hq_host_problem(), "")
            self.assertEqual(checks.check_hq_host(None), [])
            self.assertEqual(self.get("/sign-in/").status_code, 200)
        with override_settings(HQ_HOST=""):
            self.assertEqual(checks.check_hq_host(None), [])
