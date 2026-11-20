"""SMP00004 (ClickUp z8uq9mh9ev): a Xendit order is Checked Out until paid, then Purchased, then
Activated once onboarding is completed (the completed session is issued)."""
from apps.pack import prospects
from apps.pack.tests.test_onboarding import ADDRESS, DETAILS, OnboardingCase
from apps.pack.tests.xendit_base import XenditPackCase


class XenditStageTests(XenditPackCase, OnboardingCase):
    def stage(self):
        (row,) = prospects.order_rows(self.site)
        return row["stage"], row["registration_status"]

    def test_unpaid_is_checked_out_paid_is_purchased_completed_is_activated(self):
        token, _, order = self.placed_xendit()
        self.assertEqual(self.stage()[0], "checked_out")
        self.webhook(order)
        self.assertEqual(self.stage()[0], "purchased")
        self.verify_both(token)  # codes verified, delivery and Sante form still open
        self.assertEqual(self.stage()[0], "purchased")
        self.assertEqual(self.call("onboarding/address/", token=token, **ADDRESS).status_code, 200)
        self.assertEqual(self.call("onboarding/details/", token=token, **DETAILS).status_code, 200)
        self.assertEqual(self.stage()[0], "purchased")  # every step done, the completed session not issued yet
        self.assertEqual(self.call("onboarding/completed/", token=token).json()["result"], "issued")
        self.assertEqual(self.stage()[0], "activated")
