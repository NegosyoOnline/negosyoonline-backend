"""Simultaneous requests. These need real row locks and threads that share a database, so they
run on PostgreSQL only (CI's "PostgreSQL and Redis" job)."""
import threading
from unittest import skipUnless

from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase

from apps.accounts import codes
from apps.common.errors import ApiError
from apps.members import services, signin
from apps.members.models import Member, MemberSession
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site


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
        code = codes.issue_code(member.email, "203.0.113.5", signin.purpose(self.site))

        def verify():
            return signin.verify_code(
                Site.objects.get(pk=self.site.pk), member.email, code, ""
            )

        results = run_together(verify, verify, verify)

        self.assertEqual(sum(isinstance(r, tuple) for r in results), 1, results)
        failed = [r for r in results if isinstance(r, ApiError)]
        self.assertEqual({r.code for r in failed}, {"invalid_code"})
        self.assertEqual(MemberSession.objects.count(), 1)

    def test_a_contact_change_racing_a_verify_leaves_no_live_session_for_the_old_contact(self):
        member = services.create_member(
            self.site, full_name="Maria", mobile="09171234567", email="maria@buyer.test"
        )
        code = codes.issue_code(member.email, "203.0.113.5", signin.purpose(self.site))

        def verify():
            return signin.verify_code(
                Site.objects.get(pk=self.site.pk), "maria@buyer.test", code, ""
            )

        def change():
            return services.change_contacts(Member.objects.get(pk=member.pk), email="new@buyer.test")

        run_together(verify, change)

        member.refresh_from_db()
        self.assertEqual(member.email, "new@buyer.test")
        self.assertEqual(MemberSession.objects.filter(revoked_at__isnull=True).count(), 0)
