"""The admin lists: only on the hq host, only for an hq session that passed both steps (T-DOOR)."""
from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, override_settings
from django.utils import timezone

from apps.operators.models import AdminIdentity, AdminSession, OperatorAuditEntry
from apps.operators.tests.base import HQ, OTHER
from apps.operators.tests.test_hq import SESSION, HqTestCase

INDEX = "/admin/"
USERS = "/admin/operators/user/"
ADD_USER = "/admin/operators/user/add/"
STYLE = "/static/admin/css/base.css"
ADMIN_PATHS = (INDEX, USERS, ADD_USER, "/admin/login/", "/admin/password_change/")
GONE = (
    INDEX, "/admin/login/", "/admin/logout/", "/admin/two-step/", "/admin/two-step/sign-out/",
    USERS, "/admin/password_change/", STYLE,
)


class HqAdminDoorTests(HqTestCase):
    def walk(self, path):
        """Follow redirects on the hq host. The last response and the paths passed through."""
        response, hops = self.get(path), []
        while response.status_code in (301, 302, 303):
            hops.append(response["Location"])
            response = self.get(response["Location"])
        return response, hops

    def test_without_a_session_every_admin_path_ends_at_sign_in(self):
        self.enrolled()
        for path in ADMIN_PATHS:
            with self.subTest(path):
                response, hops = self.walk(path)
                self.assertEqual(hops[-1], "/sign-in/")
                self.assertContains(response, "Sign in")
                self.assertNotContains(response, "Admin lists")

    def test_the_emailed_code_alone_does_not_open_the_lists(self):
        self.enrolled()
        self.pass_email_step()
        for path in ADMIN_PATHS:
            with self.subTest(path):
                _response, hops = self.walk(path)
                self.assertEqual(hops[-1], "/verify/")
        self.assertEqual(self.get(STYLE).status_code, 404)

    def test_a_django_password_session_opens_nothing(self):
        identity = self.enrolled()
        self.client.force_login(identity.user)
        for path in ADMIN_PATHS:
            with self.subTest(path):
                _response, hops = self.walk(path)
                self.assertEqual(hops[-1], "/sign-in/")
        self.assertEqual(self.get(STYLE).status_code, 404)

    def test_both_steps_open_the_lists_as_the_identitys_user(self):
        identity = self.enrolled()
        self.sign_in()
        index = self.get(INDEX)
        self.assertEqual(index.status_code, 200)
        self.assertEqual(index.wsgi_request.user, identity.user)
        self.assertEqual(self.get(USERS).status_code, 200)
        self.assertEqual(self.get(ADD_USER).status_code, 200)

    def test_the_password_session_of_another_user_is_ignored(self):
        identity = self.enrolled()
        self.client.force_login(self.staff("other"))
        self.sign_in()
        self.assertEqual(self.get(INDEX).wsgi_request.user, identity.user)

    def test_the_lists_carry_our_words_and_no_password_link(self):
        self.enrolled()
        self.sign_in()
        page = self.get(INDEX)
        self.assertContains(page, "<title>Admin lists | NegosyoOnline admin</title>", html=True)
        self.assertContains(page, '<a href="/admin/">NegosyoOnline admin</a>', html=True)
        self.assertContains(page, '<a href="/">Admin console</a>', html=True)
        self.assertContains(page, '<button type="submit">Sign out</button>', html=True)
        self.assertContains(page, 'action="/admin/logout/"')
        for gone in ("Django", "Change password", "View site", "Log out"):
            self.assertNotContains(page, gone)

    def test_the_password_pages_do_not_exist(self):
        self.enrolled()
        self.sign_in()
        for path in ("/admin/password_change/", "/admin/password_change/done/"):
            self.assertEqual(self.get(path).status_code, 404, path)
        self.assertEqual(self.get("/admin/login/")["Location"], "/")

    def test_the_admin_styles_need_a_verified_session(self):
        self.enrolled()
        self.assertEqual(self.get(STYLE).status_code, 404)
        self.sign_in()
        response = self.get(STYLE)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response["Content-Type"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.get("/static/admin/css/nothing.css").status_code, 404)
        self.assertIn(self.get("/static/admin/../../../apps.py").status_code, (400, 404))
        self.assertIn(self.get("/static/admin/%2e%2e/%2e%2e/apps.py").status_code, (400, 404))

    def test_every_list_answer_is_private(self):
        self.enrolled()
        self.sign_in()
        for path in (INDEX, USERS):
            response = self.get(path)
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_a_list_post_needs_the_csrf_token(self):
        self.enrolled()
        client = Client(enforce_csrf_checks=True)
        self.sign_in_with(client)
        response = client.post(ADD_USER, {"username": "new"}, secure=True, HTTP_HOST=HQ)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.filter(username="new").exists())

    def sign_in_with(self, client):
        self.sign_in()
        client.cookies[SESSION] = self.client.cookies[SESSION].value

    def test_access_ends_on_the_next_request(self):
        def expire(identity):
            AdminSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        def lock(identity):
            AdminIdentity.objects.update(locked_until=timezone.now() + timedelta(minutes=15))

        def reset(identity):
            call_command("admin_identity", "reset", identity.email, actor="staff0", stdout=StringIO())

        def disable(identity):
            call_command("admin_identity", "disable", identity.email, actor="staff0", stdout=StringIO())

        def not_staff(identity):
            get_user_model().objects.filter(pk=identity.user_id).update(is_staff=False)

        def sign_out(identity):
            self.post("/sign-out/")

        for number, end in enumerate((expire, lock, reset, disable, not_staff, sign_out)):
            with self.subTest(end.__name__):
                self.client = Client()
                cache.clear()  # the sign-in limit counts every sign-in from this address
                identity = self.enrolled(f"admin{number}@example.test", self.staff(f"staff{number}"))
                self.sign_in(identity.email)
                self.assertEqual(self.get(USERS).status_code, 200)
                end(identity)
                _response, hops = self.walk(USERS)
                self.assertEqual(hops[-1], "/sign-in/")
                self.assertEqual(self.get(STYLE).status_code, 404)

    def test_permissions_are_the_users_own(self):
        user = get_user_model().objects.create_user("plain", password="x", is_staff=True)
        self.enrolled(user=user)
        self.sign_in()
        self.assertEqual(self.get(INDEX).status_code, 200)
        self.assertEqual(self.get(USERS).status_code, 403)

    def test_sign_out_on_a_list_ends_the_hq_session_and_is_audited(self):
        identity = self.enrolled()
        self.sign_in()
        self.assertEqual(self.get("/admin/logout/").status_code, 405)
        response = self.post("/admin/logout/")
        self.assertGoesTo(response, "/sign-in/")
        self.assertEqual(response.cookies[SESSION].value, "")
        self.assertIsNotNone(AdminSession.objects.get().revoked_at)
        entry = OperatorAuditEntry.objects.order_by("-pk").first()
        self.assertEqual((entry.action, entry.operator), ("admin_sign_out", identity.user))
        self.assertEqual(entry.detail, {"surface": "hq"})
        _response, hops = self.walk(INDEX)
        self.assertEqual(hops[-1], "/sign-in/")


class NoAdminElsewhereTests(HqTestCase):
    def assert_gone(self, host):
        for path in GONE:
            response = self.get(path, host=host)
            self.assertEqual(response.status_code, 404, f"{host}{path}")
        response = self.client.post(
            "/admin/login/", {"username": "operator", "password": "x"}, secure=True, HTTP_HOST=host
        )
        self.assertEqual(response.status_code, 404)

    def test_no_admin_page_answers_off_the_hq_host(self):
        identity = self.enrolled()
        for host in (OTHER, "testserver"):
            self.assert_gone(host)
        self.client.force_login(identity.user)
        self.sign_in()
        for host in (OTHER, "testserver"):
            self.assert_gone(host)

    @override_settings(HQ_HOST="")
    def test_with_no_hq_host_the_lists_do_not_exist(self):
        self.client.force_login(self.staff())
        for host in (HQ, OTHER):
            self.assert_gone(host)

    def test_the_switch_is_gone(self):
        self.assertFalse(hasattr(settings, "ADMIN_TWO_STEP_REQUIRED"))
