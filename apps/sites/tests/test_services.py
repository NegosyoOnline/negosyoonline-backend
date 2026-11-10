from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import AccountAuditEvent
from apps.common.errors import ApiError
from apps.sites import onboarding, services, templates
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain
from apps.sites.tests.base import make_account, make_site


def create(account, **overrides):
    values = {"template": "sante", "subdomain": "anas-store", "display_name": "Ana's Store"}
    values.update(overrides)
    return services.create_first_site(account, **values)


class CreateFirstSiteTests(TestCase):
    def setUp(self):
        self.account = make_account()

    def assert_refused(self, code, status, account=None, **overrides):
        before = (Site.objects.count(), Subdomain.objects.count())
        with self.assertRaises(ApiError) as raised:
            create(account or self.account, **overrides)
        self.assertEqual((raised.exception.code, raised.exception.status), (code, status))
        self.assertEqual((Site.objects.count(), Subdomain.objects.count()), before)
        return raised.exception

    def test_create_makes_the_site_its_subdomain_its_profile_and_the_record_in_one_call(self):
        site = create(self.account, subdomain="  Anas-Store ", display_name="  Ana's Store ")

        self.assertEqual(site.account, self.account)
        self.assertEqual(site.template, "sante")
        self.assertEqual(site.display_name, "Ana's Store")
        self.assertEqual(services.active_subdomain(site), "anas-store")
        self.assertEqual(site.business_profile.business_name, "")
        record = Onboarding.objects.get(account=self.account)
        self.assertEqual(record.first_site, site)
        self.assertIsNotNone(record.started_at)
        self.assertIsNone(record.minimum_completed_at)

    def test_create_records_an_audit_event(self):
        site = create(self.account)

        event = AccountAuditEvent.objects.get(account=self.account, event="site_created")
        self.assertEqual(
            event.detail, {"site": site.public_id, "subdomain": "anas-store", "template": "sante"}
        )

    def test_any_template_other_than_sante_is_refused_and_nothing_is_created(self):
        for template in ("standard", "basic", "Sante", "", "shop", None):
            with self.subTest(template=template):
                error = self.assert_refused("invalid_template", 400, template=template)
                self.assertEqual(error.fields, {"template": ["Choose a site type."]})
        self.assertFalse(Onboarding.objects.filter(first_site__isnull=False).exists())

    def test_reserved_invalid_and_taken_answer_their_codes_and_create_nothing(self):
        make_site(make_account("ben@example.test"), name="bens-store")

        for subdomain, code, status in (
            ("api", "subdomain_reserved", 400),
            ("a_b", "subdomain_invalid", 400),
            ("bens-store", "subdomain_taken", 409),
        ):
            with self.subTest(subdomain=subdomain):
                error = self.assert_refused(code, status, subdomain=subdomain)
                self.assertEqual(list(error.fields), ["subdomain"])

    def test_display_name_is_trimmed_and_must_be_1_to_80_characters(self):
        for display_name in ("", "   ", None, "x" * 81):
            with self.subTest(display_name=display_name):
                error = self.assert_refused("invalid_request", 400, display_name=display_name)
                self.assertEqual(error.fields, {"display_name": ["Use 1 to 80 characters."]})

        self.assertEqual(create(self.account, display_name="x" * 80).display_name, "x" * 80)

    def test_a_second_create_is_409_and_creates_nothing(self):
        first = create(self.account)

        self.assert_refused("site_limit_reached", 409, subdomain="another-store")
        self.assert_refused("site_limit_reached", 409)

        self.assertEqual(Site.objects.get(), first)
        self.assertEqual(Onboarding.objects.get().first_site, first)

    def test_a_removed_first_site_refuses_creation(self):
        site = create(self.account)
        Site.objects.filter(pk=site.pk).update(deleted_at=timezone.now())

        self.assert_refused("first_site_removed", 409, subdomain="another-store")

    def test_an_unverified_account_cannot_create(self):
        account = make_account("new@example.test", verified=False)

        self.assert_refused("email_not_verified", 403, account=account)

    def test_a_failure_after_the_site_row_rolls_everything_back(self):
        with mock.patch.object(
            SiteBusinessProfile.objects, "create", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                create(self.account)

        self.assertFalse(Site.objects.exists())
        self.assertFalse(Subdomain.objects.exists())
        self.assertFalse(Onboarding.objects.exists())
        self.assertFalse(AccountAuditEvent.objects.filter(event="site_created").exists())

    def test_two_accounts_cannot_take_one_subdomain(self):
        create(self.account)
        other = make_account("ben@example.test")

        self.assert_refused("subdomain_taken", 409, account=other)

        self.assertFalse(Site.objects.filter(account=other).exists())

    def test_a_name_taken_between_the_check_and_the_insert_is_409_and_leaves_no_site(self):
        make_site(make_account("ben@example.test"), name="anas-store")

        with mock.patch.object(
            services.subdomains, "check", return_value=("anas-store", "available")
        ):
            self.assert_refused("subdomain_taken", 409)

        self.assertFalse(Site.objects.filter(account=self.account).exists())


class SiteForTests(TestCase):
    def test_site_for_answers_the_same_404_for_missing_foreign_and_deleted(self):
        account = make_account()
        mine = make_site(account)
        foreign = make_site(make_account("ben@example.test"), name="bens-store")
        deleted = make_site(account, name="old-store", first=False, deleted_at=timezone.now())

        self.assertEqual(services.site_for(account, mine.public_id), mine)
        answers = []
        for site_id in ("site_" + "0" * 20, foreign.public_id, deleted.public_id, "1", ""):
            with self.assertRaises(ApiError) as raised:
                services.site_for(account, site_id)
            error = raised.exception
            answers.append((error.code, error.message, error.status, error.fields))
        self.assertEqual(set(answers), {("not_found", "Not found.", 404, None)})


class SiteForTemplateTests(TestCase):
    """The capability check: a Santé feature reaches its site only through the stored template."""

    def setUp(self):
        self.account = make_account()
        self.mine = make_site(self.account)

    def refusal(self, site_id, **kwargs):
        with self.assertRaises(ApiError) as raised:
            services.site_for(self.account, site_id, **kwargs)
        error = raised.exception
        return (error.code, error.message, error.status, error.fields)

    def test_the_owners_sante_site_is_returned_with_and_without_the_template(self):
        self.assertEqual(services.site_for(self.account, self.mine.public_id), self.mine)
        self.assertEqual(
            services.site_for(self.account, self.mine.public_id, template=templates.SANTE),
            self.mine,
        )

    def test_a_site_of_another_type_is_404_with_the_template_and_returned_without_it(self):
        # The service refuses to create any other type, so the stored value is changed directly.
        Site.objects.filter(pk=self.mine.pk).update(template="basic")

        self.assertEqual(services.site_for(self.account, self.mine.public_id), self.mine)
        self.assertEqual(
            self.refusal(self.mine.public_id, template=templates.SANTE),
            ("not_found", "Not found.", 404, None),
        )

    def test_foreign_deleted_unknown_and_other_type_answer_one_identical_404(self):
        foreign = make_site(make_account("ben@example.test"), name="bens-store")
        deleted = make_site(
            self.account, name="old-store", first=False, deleted_at=timezone.now()
        )
        other_type = make_site(self.account, name="other-store", first=False)
        Site.objects.filter(pk=other_type.pk).update(template="basic")

        answers = [
            self.refusal(site_id, template=templates.SANTE)
            for site_id in (
                foreign.public_id, deleted.public_id, "site_" + "0" * 20, other_type.public_id,
            )
        ]

        self.assertEqual(set(answers), {("not_found", "Not found.", 404, None)})
        # The same answer as the check without a template gives for a missing site.
        self.assertEqual(self.refusal("site_" + "0" * 20), answers[0])


class UpdateSiteTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.site = make_site(self.account)

    def test_update_site_normalises_contacts_and_links(self):
        site = services.update_site(self.account, self.site, {
            "display_name": "  New Name ",
            "contact_email": " Hello@Example.Test ",
            "contact_mobile": "0917 123 4567",
            "instagram_url": "https://www.instagram.com/anas",
            "tiktok_url": "https://tiktok.com/@anas",
        })

        self.site.refresh_from_db()
        self.assertEqual(site, self.site)
        self.assertEqual(site.display_name, "New Name")
        self.assertEqual(site.contact_email, "hello@example.test")
        self.assertEqual(site.contact_mobile, "+639171234567")
        self.assertEqual(site.instagram_url, "https://www.instagram.com/anas")
        self.assertEqual(site.tiktok_url, "https://tiktok.com/@anas")

    def test_only_the_fields_sent_change_and_blank_or_null_clears(self):
        services.update_site(self.account, self.site, {"contact_email": "a@example.test"})
        services.update_site(self.account, self.site, {"contact_mobile": "09171234567"})
        self.site.refresh_from_db()
        self.assertEqual(self.site.contact_email, "a@example.test")
        self.assertEqual(self.site.display_name, "Ana's Store")

        services.update_site(self.account, self.site, {
            "instagram_url": "https://instagram.com/anas", "tiktok_url": "https://tiktok.com/@anas",
        })
        services.update_site(self.account, self.site, {"instagram_url": None, "tiktok_url": " "})
        self.site.refresh_from_db()
        self.assertEqual((self.site.instagram_url, self.site.tiktok_url), ("", ""))
        self.assertEqual(self.site.contact_email, "a@example.test")
        self.assertEqual(self.site.contact_mobile, "+639171234567")

    def test_a_blank_store_email_or_mobile_is_refused_and_nothing_in_the_request_saves(self):
        services.update_site(
            self.account, self.site,
            {"contact_email": "a@example.test", "contact_mobile": "09171234567"},
        )
        messages = {
            "contact_email": "Enter your store email.",
            "contact_mobile": "Enter your store mobile.",
        }
        for name, message in messages.items():
            for value in ("", "   ", None):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ApiError) as raised:
                        services.update_site(self.account, self.site, {
                            "display_name": "Changed",
                            "instagram_url": "https://instagram.com/anas",
                            name: value,
                        })
                    self.assertEqual(
                        (raised.exception.code, raised.exception.status), ("invalid_request", 400)
                    )
                    self.assertEqual(raised.exception.fields, {name: [message]})
        self.site.refresh_from_db()
        self.assertEqual(self.site.display_name, "Ana's Store")
        self.assertEqual(self.site.instagram_url, "")
        self.assertEqual(self.site.contact_email, "a@example.test")
        self.assertEqual(self.site.contact_mobile, "+639171234567")
        self.assertEqual(AccountAuditEvent.objects.filter(event="site_updated").count(), 1)

    def test_an_accent_colour_is_refused_and_the_stored_value_is_unchanged(self):
        Site.objects.filter(pk=self.site.pk).update(accent_color="#173DB8")
        for value in ("#000000", "", None):
            with self.subTest(value=value):
                with self.assertRaises(ApiError) as raised:
                    services.update_site(
                        self.account, self.site, {"display_name": "Changed", "accent_color": value}
                    )
                self.assertEqual(
                    raised.exception.fields, {"accent_color": ["This cannot be changed here."]}
                )
        self.site.refresh_from_db()
        self.assertEqual(self.site.accent_color, "#173DB8")
        self.assertEqual(self.site.display_name, "Ana's Store")

    def test_update_site_reports_every_invalid_field_in_one_error_and_saves_nothing(self):
        with self.assertRaises(ApiError) as raised:
            services.update_site(self.account, self.site, {
                "display_name": "Good Name",
                "contact_email": "not-an-email",
                "contact_mobile": "12345",
                "tiktok_url": "tiktok.com/@anas",
            })

        error = raised.exception
        self.assertEqual((error.code, error.status), ("invalid_request", 400))
        self.assertEqual(error.fields, {
            "contact_email": ["Enter a valid email address."],
            "contact_mobile": ["Enter a valid Philippine mobile number."],
            "tiktok_url": ["Enter the full https link to your TikTok page."],
        })
        self.site.refresh_from_db()
        self.assertEqual(self.site.display_name, "Ana's Store")

    def test_a_blank_site_name_is_refused(self):
        for value in ("", "  ", None, "x" * 81):
            with self.subTest(value=value):
                with self.assertRaises(ApiError) as raised:
                    services.update_site(self.account, self.site, {"display_name": value})
                self.assertEqual(
                    raised.exception.fields, {"display_name": ["Use 1 to 80 characters."]}
                )

    def test_template_subdomain_and_server_fields_cannot_be_changed(self):
        for name in ("template", "subdomain", "account", "operator_hold", "deleted_at",
                     "business_name", "public_id"):
            with self.subTest(name=name):
                with self.assertRaises(ApiError) as raised:
                    services.update_site(self.account, self.site, {name: "standard"})
                self.assertEqual(list(raised.exception.fields), [name])
        self.site.refresh_from_db()
        self.assertEqual(self.site.template, "sante")
        self.assertFalse(self.site.operator_hold)

    def test_social_links_must_be_https_on_their_own_host(self):
        bad = (
            "http://instagram.com/anas", "https://instagram.com.evil.test/anas",
            "https://evil.test/instagram.com", "instagram.com/anas", "javascript:alert(1)",
            "https://tiktok.com/@anas", "https://instagram.com/" + "a" * 200,
        )
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ApiError) as raised:
                    services.update_site(self.account, self.site, {"instagram_url": value})
                self.assertEqual(
                    raised.exception.fields,
                    {"instagram_url": ["Enter the full https link to your Instagram page."]},
                )
        with self.assertRaises(ApiError) as raised:
            services.update_site(
                self.account, self.site, {"tiktok_url": "https://instagram.com/anas"}
            )
        self.assertEqual(
            raised.exception.fields,
            {"tiktok_url": ["Enter the full https link to your TikTok page."]},
        )

    def test_a_facebook_link_is_saved_on_the_facebook_hosts_only_and_can_be_cleared(self):
        for host in ("facebook.com", "www.facebook.com", "m.facebook.com"):
            with self.subTest(host=host):
                services.update_site(
                    self.account, self.site, {"facebook_url": f"https://{host}/anas"}
                )
                self.site.refresh_from_db()
                self.assertEqual(self.site.facebook_url, f"https://{host}/anas")

        bad = (
            "http://facebook.com/anas", "https://fb.com/anas", "https://facebook.com.evil.test/anas",
            "https://evil.test/facebook.com", "facebook.com/anas", "https://instagram.com/anas",
            "https://facebook.com/" + "a" * 200,
        )
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ApiError) as raised:
                    services.update_site(self.account, self.site, {"facebook_url": value})
                self.assertEqual(
                    raised.exception.fields,
                    {"facebook_url": ["Enter the full https link to your Facebook page."]},
                )
        self.site.refresh_from_db()
        self.assertEqual(self.site.facebook_url, "https://m.facebook.com/anas")

        for value in ("", "  ", None):
            with self.subTest(clear=value):
                services.update_site(
                    self.account, self.site, {"facebook_url": "https://facebook.com/anas"}
                )
                services.update_site(self.account, self.site, {"facebook_url": value})
                self.site.refresh_from_db()
                self.assertEqual(self.site.facebook_url, "")

    def test_another_accounts_site_cannot_be_updated(self):
        other = make_account("ben@example.test")

        with self.assertRaises(ApiError) as raised:
            services.update_site(other, self.site, {"display_name": "Taken Over"})

        self.assertEqual(raised.exception.status, 404)
        self.site.refresh_from_db()
        self.assertEqual(self.site.display_name, "Ana's Store")

    def test_the_audit_event_names_fields_but_holds_no_values(self):
        services.update_site(self.account, self.site, {"contact_email": "a@example.test"})

        event = AccountAuditEvent.objects.get(event="site_updated")
        self.assertEqual(
            event.detail, {"site": self.site.public_id, "fields": ["contact_email"]}
        )


class UpdateBusinessProfileTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.site = make_site(self.account)

    def update(self, **data):
        return services.update_business_profile(self.account, self.site, data)

    def test_update_business_profile_cleans_tin_dti_and_serial(self):
        profile = self.update(
            business_name="  Ana   Trading ",
            registered_name="Ana Trading Inc.",
            tin="123 456 789",
            dti_business_number="1234-567",
            bir_rsn="113rc 20260000017598",
            privacy_contact_email="Privacy@Example.Test",
            shipping_estimates_confirmed=True,
        )

        self.assertEqual(profile.business_name, "Ana Trading")
        self.assertEqual(profile.tin, "123-456-789")
        self.assertEqual(profile.dti_business_number, "1234567")
        self.assertEqual(profile.bir_rsn, "113RC20260000017598")
        self.assertEqual(profile.privacy_contact_email, "privacy@example.test")
        self.assertTrue(profile.shipping_estimates_confirmed)
        self.assertEqual(self.update(tin="12345678900000").tin, "123-456-789-00000")

    def test_invalid_values_are_reported_together_and_nothing_saves(self):
        with self.assertRaises(ApiError) as raised:
            self.update(
                business_name="Ana Trading", tin="12345", dti_business_number="12a",
                bir_rsn="nope", privacy_contact_email="nope", shipping_luzon_estimate="x" * 61,
                shipping_estimates_confirmed="yes",
            )

        self.assertEqual(
            sorted(raised.exception.fields),
            ["bir_rsn", "dti_business_number", "privacy_contact_email",
             "shipping_estimates_confirmed", "shipping_luzon_estimate", "tin"],
        )
        self.assertEqual(
            raised.exception.fields["bir_rsn"],
            ["Enter your BIR Official Control Number (OCN), like 113RC20260000017598."],
        )
        self.assertEqual(self.site.business_profile.business_name, "")

    def test_a_blank_value_clears_a_profile_field(self):
        self.update(tin="123456789", shipping_luzon_estimate="3 days")

        profile = self.update(tin="", shipping_luzon_estimate=None)

        self.assertEqual(profile.tin, "")
        self.assertEqual(profile.shipping_luzon_estimate, "")

    def test_a_blank_business_name_is_refused_and_nothing_in_the_request_saves(self):
        self.update(business_name="Ana Trading")
        for value in ("", "   ", None):
            with self.subTest(value=value):
                with self.assertRaises(ApiError) as raised:
                    self.update(business_name=value, business_address="1 Rizal St, Manila")
                self.assertEqual(
                    (raised.exception.code, raised.exception.status), ("invalid_request", 400)
                )
                self.assertEqual(
                    raised.exception.fields, {"business_name": ["Enter your business name."]}
                )
        profile = SiteBusinessProfile.objects.get(site=self.site)
        self.assertEqual(profile.business_name, "Ana Trading")
        self.assertEqual(profile.business_address, "")

    def test_business_name_is_160_at_most(self):
        self.assertEqual(self.update(business_name="x" * 160).business_name, "x" * 160)

        with self.assertRaises(ApiError) as raised:
            self.update(business_name="x" * 161)

        self.assertEqual(
            raised.exception.fields, {"business_name": ["Use up to 160 characters."]}
        )

    def test_unknown_and_server_fields_are_refused(self):
        for name in ("site", "updated_at", "seal_key", "state"):
            with self.subTest(name=name):
                with self.assertRaises(ApiError) as raised:
                    self.update(**{name: "x"})
                self.assertEqual(list(raised.exception.fields), [name])

    def test_another_accounts_profile_cannot_be_updated(self):
        other = make_account("ben@example.test")

        with self.assertRaises(ApiError) as raised:
            services.update_business_profile(other, self.site, {"business_name": "Taken"})

        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(self.site.business_profile.business_name, "")

    def test_completing_the_last_required_field_stamps_minimum_completed_at(self):
        services.update_site(
            self.account, self.site,
            {"contact_email": "a@example.test", "contact_mobile": "09171234567"},
        )
        self.assertIsNone(Onboarding.objects.get().minimum_completed_at)
        self.assertEqual(onboarding.evaluate(self.account).missing, ["business_name"])

        self.update(business_name="Ana Trading")

        setup = onboarding.evaluate(self.account)
        self.assertEqual(setup.state, "minimum_complete")
        self.assertIsNotNone(setup.minimum_completed_at)
        self.assertIsNotNone(setup.started_at)
