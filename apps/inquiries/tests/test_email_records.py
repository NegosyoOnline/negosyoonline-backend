"""The send records of inquiry email replies: their retention rule, their erasure with the site, and
the deploy check of the contact-inbox switch."""
from datetime import timedelta

from django.core.checks import run_checks
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.common import retention
from apps.inquiries import retention as inquiry_retention
from apps.inquiries.models import InquiryConversation
from apps.inquiries.tests.base import KEY
from apps.inquiries.tests.test_email_out import make_email
from apps.notifications.models import EmailMessage
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site

RULE = "inquiry_email_records"


def record(address, category="inquiry_reply", age_days=0):
    row = EmailMessage.objects.create(recipient=address, category=category, status="sent")
    if age_days:
        EmailMessage.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=age_days))
    return row


@override_settings(RETENTION_ENABLED=True, INQUIRIES_ENABLED=True)
class RuleTests(TestCase):
    def test_old_inquiry_reply_records_go_and_nothing_else(self):
        old = record("a@x.test", age_days=181)
        new = record("a@x.test", age_days=179)
        other = record("a@x.test", category="members_signin", age_days=181)
        dry = retention.run_all(only=[RULE], dry_run=True).counts[RULE]
        self.assertEqual(dry, 1)
        self.assertEqual(EmailMessage.objects.count(), 3)
        ran = retention.run_all(only=[RULE]).counts[RULE]
        self.assertEqual(ran, dry)
        self.assertFalse(EmailMessage.objects.filter(pk=old.pk).exists())
        self.assertTrue(EmailMessage.objects.filter(pk__in=[new.pk, other.pk]).count() == 2)

    def test_the_rule_waits_for_both_switches(self):
        record("a@x.test", age_days=400)
        with override_settings(INQUIRIES_ENABLED=False):
            self.assertEqual(retention.run_all(only=[RULE]).counts.get(RULE, 0), 0)
        with override_settings(RETENTION_ENABLED=False):
            retention.run_all(only=[RULE])
        self.assertEqual(EmailMessage.objects.count(), 1)

    def test_the_period_is_the_message_period_which_is_shorter_than_the_generic_rule(self):
        from apps.notifications.retention import EMAIL_RECORD_LIFETIME

        self.assertLess(inquiry_retention.MESSAGE_RETENTION, EMAIL_RECORD_LIFETIME)


class ErasureTests(TestCase):
    def setUp(self):
        self.a = make_site(make_account("ana@example.test"), name="anas-store")
        self.b = make_site(make_account("ben@example.test"), name="bens-store", display_name="Ben")
        make_email(self.a, ref="em_" + "a" * 24, email="maria@visitor.test")
        make_email(self.b, ref="em_" + "b" * 24, email="jun@visitor.test")
        self.mine = record("maria@visitor.test")
        self.mine_other_category = record("maria@visitor.test", category="storefront_contact")
        self.theirs = record("jun@visitor.test")
        self.stranger = record("nobody@visitor.test")  # an address no conversation of this site holds

    def sites(self, site):
        return Site.objects.filter(pk=site.pk)

    def test_the_dry_run_count_equals_the_run_count(self):
        dry = inquiry_retention.blank_inquiries(self.sites(self.a), dry_run=True)
        self.assertEqual(dry[inquiry_retention.EMAIL_RECORDS], 1)
        self.assertEqual(EmailMessage.objects.count(), 4)  # a dry run changes nothing
        ran = inquiry_retention.blank_inquiries(self.sites(self.a))
        self.assertEqual(ran, dry)

    def test_only_the_sites_inquiry_reply_records_of_its_own_customers_go(self):
        inquiry_retention.blank_inquiries(self.sites(self.a))
        left = set(EmailMessage.objects.values_list("pk", flat=True))
        self.assertEqual(left, {self.mine_other_category.pk, self.theirs.pk, self.stranger.pk})
        self.assertEqual(InquiryConversation.objects.get(site=self.a).contact_email, "")
        self.assertEqual(InquiryConversation.objects.get(site=self.b).contact_email, "jun@visitor.test")

    def test_a_recipient_shared_with_another_sites_conversation_is_deleted_too(self):
        make_email(self.b, ref="em_" + "c" * 24, email="maria@visitor.test")
        inquiry_retention.blank_inquiries(self.sites(self.b))
        self.assertFalse(EmailMessage.objects.filter(pk=self.mine.pk).exists())  # EmailMessage has no site column

    @override_settings(INQUIRIES_ENABLED=False)
    def test_erasure_runs_with_the_switch_off(self):
        inquiry_retention.blank_inquiries(self.sites(self.a))
        self.assertFalse(EmailMessage.objects.filter(pk=self.mine.pk).exists())

    def test_the_audit_names_the_new_group(self):
        self.assertIn(inquiry_retention.EMAIL_RECORDS, inquiry_retention.blanked())


class SwitchCheckTests(TestCase):
    def ids(self, **settings_):
        with override_settings(INQUIRIES_CREDENTIALS_KEY=KEY, **settings_):
            return sorted(m.id for m in run_checks(include_deployment_checks=True) if m.id and m.id.startswith("inquiries."))

    def test_each_combination(self):
        for inquiries in (False, True):
            for storefront in (False, True):
                for chat in (False, True):
                    for inbox in (False, True):
                        with self.subTest(inquiries=inquiries, storefront=storefront, chat=chat, inbox=inbox):
                            ids = self.ids(
                                INQUIRIES_ENABLED=inquiries, STOREFRONT_ENABLED=storefront,
                                INQUIRY_WEBCHAT_ENABLED=chat, INQUIRY_CONTACT_INBOX_ENABLED=inbox,
                            )
                            self.assertEqual("inquiries.E003" in ids, inbox and not (inquiries and storefront))
                            self.assertEqual("inquiries.E002" in ids, chat and not (inquiries and storefront))
                            self.assertNotIn("inquiries.E001", ids)

    def test_the_message_names_what_is_missing(self):
        with override_settings(
            INQUIRIES_ENABLED=True, STOREFRONT_ENABLED=False, INQUIRY_CONTACT_INBOX_ENABLED=True,
            INQUIRIES_CREDENTIALS_KEY=KEY,
        ):
            (error,) = [m for m in run_checks(include_deployment_checks=True) if m.id == "inquiries.E003"]
        self.assertIn("STOREFRONT_ENABLED is off", error.msg)

    def test_the_switch_is_off_by_default(self):
        from django.conf import settings

        self.assertFalse(settings.INQUIRY_CONTACT_INBOX_ENABLED)
