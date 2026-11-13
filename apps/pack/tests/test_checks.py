from django.test import SimpleTestCase, override_settings

from apps.pack.checks import check_pack_dependencies


class PackChecksTests(SimpleTestCase):
    def ids(self):
        return {m.id for m in check_pack_dependencies(None)}

    @override_settings(PACK_ENABLED=False, MEMBERS_ENABLED=False)
    def test_off_means_silent(self):
        self.assertEqual(self.ids(), set())

    @override_settings(PACK_ENABLED=True, MEMBERS_ENABLED=False, SANTE_AGREEMENT_ENABLED=False)
    def test_on_needs_members_and_the_agreement(self):
        self.assertTrue({"pack.E001", "pack.E002"} <= self.ids())

    @override_settings(PACK_ENABLED=True, MEMBERS_ENABLED=True, SANTE_AGREEMENT_ENABLED=True)
    def test_on_with_dependencies_has_no_errors(self):
        self.assertFalse({"pack.E001", "pack.E002"} & self.ids())

    @override_settings(PACK_ENABLED=False, PACK_SHIPPING_ENABLED=True)
    def test_shipping_needs_the_pack_switch(self):
        self.assertIn("pack.E003", self.ids())

    @override_settings(PACK_ENABLED=False, PACK_SHIPPING_ENABLED=False)
    def test_shipping_off_is_silent(self):
        self.assertNotIn("pack.E003", self.ids())

    @override_settings(PACK_ENABLED=False, PACK_PURGE_ENABLED=True)
    def test_the_purge_needs_the_pack_switch(self):
        self.assertIn("pack.E004", self.ids())

    @override_settings(PACK_ENABLED=True, PACK_PURGE_ENABLED=False, PACK_BUYER_NOTICE_ENABLED=True)
    def test_the_buyer_notice_needs_the_purge(self):
        self.assertIn("pack.E005", self.ids())

    @override_settings(PACK_ENABLED=False, PACK_PURGE_ENABLED=False, PACK_BUYER_NOTICE_ENABLED=False)
    def test_both_off_is_silent(self):
        self.assertFalse({"pack.E004", "pack.E005"} & self.ids())
