from django.test import SimpleTestCase, override_settings

from apps.ledger.checks import check_ledger_switches


def ids():
    return [e.id for e in check_ledger_switches(None)]


class CheckTests(SimpleTestCase):
    @override_settings(LEDGER_ENABLED=False, LEDGER_POST_BILLING_ENABLED=True)
    def test_5_post_billing_without_ledger_is_check_error_e001(self):
        self.assertEqual(ids(), ["ledger.E001"])

    @override_settings(LEDGER_ENABLED=True, LEDGER_LIVE_BOOKS=True, DEBUG=True)
    def test_6_live_books_in_debug_is_check_error_e002(self):
        self.assertEqual(ids(), ["ledger.E002"])

    @override_settings(
        LEDGER_ENABLED=False, LEDGER_POST_BILLING_ENABLED=False, LEDGER_LIVE_BOOKS=False, DEBUG=True
    )
    def test_7_no_error_with_everything_off(self):
        self.assertEqual(ids(), [])

    @override_settings(
        LEDGER_ENABLED=True, LEDGER_POST_BILLING_ENABLED=True, LEDGER_LIVE_BOOKS=True, DEBUG=False
    )
    def test_8_no_error_for_a_valid_combination(self):
        self.assertEqual(ids(), [])
