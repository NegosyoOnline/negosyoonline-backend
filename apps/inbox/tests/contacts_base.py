"""Helpers for the contacts tests. Every name and number in them is made up."""
import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.inbox.contacts import contact_scope
from apps.inbox.models import InboxAccess
from apps.operators.models import OperatorAuditEntry

contacts_on = override_settings(SMS_INBOX_ENABLED=True, SMS_CONTACTS_ENABLED=True)

ANA = {"first_name": "Ana", "last_name": "Sample", "mobile": "0917 000 0101"}
BEN = {"first_name": "Ben", "last_name": "Example", "mobile": "+639170000102"}


def without_ids(text):
    """The text with every public id (cont_..., grp_...) removed. The ids are random, so a short probe such
    as "917" or "0101" can occur inside one by chance; a probe is searched for in what is left."""
    return re.sub(r"\b[a-z]{2,8}_[0-9a-z]{16,}\b", "", text)


@contacts_on
class ContactsCase(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user("boss", password="pw", is_staff=True)
        self.staff = get_user_model().objects.create_user("ana", password="pw", is_staff=True)
        self.grant(self.staff)

    def grant(self, user, can_send=True):
        return InboxAccess.objects.create(operator=user, platform="sms", can_send=can_send, granted_by=self.admin)

    def scope(self, user=None):
        scope = contact_scope(user or self.staff)
        self.assertIsNotNone(scope)
        return scope

    def read_only(self):
        reader = get_user_model().objects.create_user("reader", password="pw", is_staff=True)
        self.grant(reader, can_send=False)
        return self.scope(reader)

    def entries(self, *actions):
        rows = OperatorAuditEntry.objects.order_by("pk")
        return list(rows.filter(action__in=actions) if actions else rows)

    def assertNothingPersonalIn(self, entries, *values):
        text = json.dumps(
            [[e.action, e.target_type, e.target_id, e.reason, e.detail, e.personal] for e in entries]
        )
        text = without_ids(text)
        for value in values:
            self.assertNotIn(value, text)
