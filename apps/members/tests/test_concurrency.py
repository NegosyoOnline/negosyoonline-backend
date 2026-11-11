"""Simultaneous requests. These need real row locks and threads that share a database, so they
run on PostgreSQL only (CI's "PostgreSQL and Redis" job)."""
import re
import threading
from unittest import skipUnless

from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase

from apps.common.errors import ApiError
from apps.members import services, signin
from apps.members.models import Member, MemberSession
from apps.members.tests.base import BINDING
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site


def issue_code(site, email):
    """Request a sign-in code the way the route does and read it from the sent email."""
    sends = []
    signin.request_code(site, email, binding_raw=BINDING, ip="203.0.113.5", defer=sends.append)
    for send in sends:
        send()
    return re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)


def run_together(*calls):
    """Run each call in its own thread and connection, released at the same moment."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def worker(index, call):
        try:
            barrier.wait(timeout=10)
            results[index] = call()
        except Exception as exc:  # the test inspects what each call raised
            results[index] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=item) for item in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
class MemberConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.site = make_site(make_account())

    def test_two_simultaneous_creates_of_one_contact_make_one_member(self):
        def create():
            return services.create_member(
                Site.objects.get(pk=self.site.pk),
                full_name="Maria", mobile="09171234567", email="maria@buyer.test",
            )

        results = run_together(create, create, create)

        self.assertEqual(sum(isinstance(r, Member) for r in results), 1, results)
        refused = [r for r in results if isinstance(r, ApiError)]
        self.assertEqual({r.code for r in refused}, {"contact_in_use"})
        self.assertEqual(Member.objects.count(), 1)

    def test_two_simultaneous_verifies_of_one_code_make_one_session(self):
        member = services.create_member(
            self.site, full_name="Maria", mobile="09171234567", email="maria@buyer.test"
        )
        code = issue_code(self.site, member.email)

        def verify():
            return signin.verify(
                Site.objects.get(pk=self.site.pk), binding_raw=BINDING, code=code, ip="203.0.113.5"
            )

        results = run_together(verify, verify, verify)

        self.assertEqual(sum(isinstance(r, tuple) for r in results), 1, results)
        failed = [r for r in results if isinstance(r, ApiError)]
        self.assertEqual({r.code for r in failed}, {"code_expired"})
        self.assertEqual(MemberSession.objects.count(), 1)

    def test_a_contact_release_racing_a_verify_leaves_no_live_session(self):
        member = services.create_member(
            self.site, full_name="Maria", mobile="09171234567", email="maria@buyer.test"
        )
        code = issue_code(self.site, member.email)

        def verify():
            return signin.verify(
                Site.objects.get(pk=self.site.pk), binding_raw=BINDING, code=code, ip="203.0.113.5"
            )

        def change():
            return services.release_contacts(Site.objects.get(pk=self.site.pk), member)

        run_together(verify, change)

        member.refresh_from_db()
        self.assertTrue(services.contacts_released(member))
        self.assertEqual(MemberSession.objects.filter(revoked_at__isnull=True).count(), 0)
