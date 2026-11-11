"""Helpers for the Bulk SMS tests. Every name and number in them is made up."""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings

from apps.inbox.bulk import bulk_scope
from apps.inbox.models import Contact, ContactGroup, InboxAccess
from apps.inbox.tests.base import InboxTestCase
from apps.operators.models import OperatorAuditEntry

bulk_on = override_settings(SMS_INBOX_ENABLED=True, SMS_CONTACTS_ENABLED=True, SMS_BULK_ENABLED=True)
TEXT = "Our shop opens Monday"


def mobile(n):
    return f"+6391700{n:05d}"


def lines(*numbers):
    return "\n".join(numbers)


@bulk_on
class BulkCase(InboxTestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_user("boss", password="pw", is_staff=True)
        self.grant(self.staff)
        self.n = 0

    def grant(self, user, *, can_send=True, can_bulk=True):
        return InboxAccess.objects.create(
            operator=user, platform="sms", can_send=can_send, can_bulk=can_bulk, granted_by=self.admin
        )

    def scope(self, user=None):
        scope = bulk_scope(user or self.staff)
        self.assertIsNotNone(scope)
        return scope

    def pasted(self, *numbers, text=TEXT, **extra):
        return {"text": text, "source": "pasted", "numbers": lines(*numbers), **extra}

    def request_id(self):
        self.n += 1
        return f"request-id-{self.n:08d}"

    def start(self, count, *numbers, **extra):
        from apps.inbox import bulk
        data = self.pasted(*numbers, count=count, request_id=self.request_id(), **extra)
        return bulk.confirm(self.scope(), data)

    def entries(self, *actions):
        rows = OperatorAuditEntry.objects.order_by("pk")
        return list(rows.filter(action__in=actions) if actions else rows)

    def assertNothingPersonalIn(self, entries, *values):
        text = json.dumps([[e.action, e.target_type, e.target_id, e.reason, e.detail, e.personal] for e in entries])
        for value in values:
            self.assertNotIn(value, text)

    def make_group(self, name, *numbers):
        group = ContactGroup.objects.create(owner_kind="hq", name=name)
        for number in numbers:
            contact, _ = Contact.objects.get_or_create(
                owner_kind="hq", mobile=number, defaults={"first_name": "Test", "created_by": self.staff}
            )
            group.contacts.add(contact)
        return group
