"""The one access check, and the command that grants access."""
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.inbox.access import scope_for
from apps.inbox.models import Conversation, InboxAccess
from apps.inbox.tests.base import SENDER, InboxTestCase
from apps.operators import audit
from apps.operators.models import OperatorAuditEntry
from apps.sites.tests.base import make_account, make_site

inbox_off = override_settings(SMS_INBOX_ENABLED=False)


class ScopeTests(InboxTestCase):
    def grant(self, **fields):
        return InboxAccess.objects.create(operator=self.staff, platform="sms", granted_by=self.staff, **fields)

    def conversation(self, **fields):
        fields.setdefault("owner_kind", "hq")
        return Conversation.objects.create(
            platform="sms", line=self.phone, contact=SENDER, last_message_at=timezone.now(), **fields
        )

    def test_1_no_row_no_scope(self):
        self.assertIsNone(scope_for(self.staff, "sms"))

    def test_2_a_superuser_without_a_row_has_no_scope(self):
        boss = get_user_model().objects.create_superuser("boss", password="pw")
        self.assertIsNone(scope_for(boss, "sms"))

    def test_3_a_row_gives_hq_sms_conversations(self):
        self.grant()
        account = make_account()
        mine = self.conversation()
        self.conversation(owner_kind="site", site=make_site(account))
        self.conversation(owner_kind="account", account=account)
        scope = scope_for(self.staff, "sms")
        self.assertEqual(list(scope.conversations), [mine])
        self.assertEqual((scope.actor, scope.platform, scope.can_send), (self.staff, "sms", False))

    def test_4_can_send_follows_the_row(self):
        self.grant(can_send=True)
        self.assertIs(scope_for(self.staff, "sms").can_send, True)

    def test_5_any_other_platform_has_no_scope(self):
        self.grant()
        for platform in ("messenger", "instagram", "tiktok", "email", "webchat", "fax", None):
            with self.subTest(platform=platform), self.assertNumQueries(0):
                self.assertIsNone(scope_for(self.staff, platform))

    def test_6_no_scope_and_no_query_while_the_inbox_is_off(self):
        self.grant()
        with inbox_off, self.assertNumQueries(0):
            self.assertIsNone(scope_for(self.staff, "sms"))

    def test_7_only_an_active_staff_user(self):
        users = get_user_model().objects
        visitor = users.create_user("visitor", password="pw")
        gone = users.create_user("gone", password="pw", is_staff=True, is_active=False)
        for user in (visitor, gone):
            InboxAccess.objects.create(operator=user, platform="sms", granted_by=self.staff)
        for actor in (visitor, gone, AnonymousUser(), None):
            with self.subTest(actor=actor):
                self.assertIsNone(scope_for(actor, "sms"))


class AccessCommandTests(InboxTestCase):
    def setUp(self):
        super().setUp()
        self.actor = get_user_model().objects.create_user("coach", password="pw", is_staff=True)

    def run_command(self, action, **options):
        out = StringIO()
        if action != "list":
            options.setdefault("user", "ana")
            options.setdefault("actor", "coach")
        call_command("sms_inbox_access", action, stdout=out, **options)
        return out.getvalue()

    def entries(self):
        return [
            (e.action, e.operator, e.target_type, e.target_id, e.detail, e.personal)
            for e in OperatorAuditEntry.objects.order_by("pk")
        ]

    def entry(self, change, level):
        detail = {"platform": "sms", "change": change, "level": level}
        return ("sms_inbox_access", self.actor, "inbox_access", str(self.staff.pk), detail, {})

    def test_8_grant_gives_read(self):
        self.assertIn("Granted: read.", self.run_command("grant"))
        row = InboxAccess.objects.get()
        self.assertEqual((row.operator, row.platform, row.can_send, row.granted_by), (self.staff, "sms", False, self.actor))
        self.assertEqual(self.entries(), [self.entry("grant", "read")])

    def test_9_grant_with_send(self):
        self.assertIn("Granted: read and send.", self.run_command("grant", send=True))
        self.assertIs(InboxAccess.objects.get().can_send, True)
        self.assertEqual(self.entries(), [self.entry("grant", "send")])

    def test_10_grant_again_changes_the_level(self):
        self.run_command("grant", send=True)
        self.assertIn("Granted: read.", self.run_command("grant"))
        self.assertIs(InboxAccess.objects.get().can_send, False)
        self.assertEqual(self.entries(), [self.entry("grant", "send"), self.entry("grant", "read")])
        self.assertIn("Already granted: read.", self.run_command("grant"))
        self.assertEqual(len(self.entries()), 2)

    def test_11_revoke_removes_the_row(self):
        self.run_command("grant")
        self.assertIn("Revoked.", self.run_command("revoke"))
        self.assertEqual(InboxAccess.objects.count(), 0)
        self.assertEqual(self.entries()[-1], self.entry("revoke", "none"))
        self.assertIn("No access to revoke.", self.run_command("revoke"))
        self.assertEqual(len(self.entries()), 2)

    def test_12_list_shows_who_and_what(self):
        self.assertIn("No one has access.", self.run_command("list"))
        self.run_command("grant", send=True)
        granted = InboxAccess.objects.get().granted_at
        self.assertIn(f"ana\tsms\tread and send\tgranted_by=coach\t{granted:%Y-%m-%d}", self.run_command("list"))

    def test_13_the_user_and_the_actor_must_be_active_staff(self):
        users = get_user_model().objects
        users.create_user("visitor", password="pw")
        users.create_user("gone", password="pw", is_staff=True, is_active=False)
        for option in ("user", "actor"):
            for name in (None, "missing", "visitor", "gone"):
                for action in ("grant", "revoke"):
                    with self.subTest(option=option, name=name, action=action):
                        with self.assertRaisesMessage(CommandError, f"--{option} must be an active staff user."):
                            self.run_command(action, **{option: name})
        self.assertEqual((InboxAccess.objects.count(), OperatorAuditEntry.objects.count()), (0, 0))

    def test_14_every_action_is_refused_while_the_inbox_is_off(self):
        for action in ("grant", "revoke", "list"):
            with self.subTest(action=action), inbox_off, self.assertNumQueries(0):
                with self.assertRaisesMessage(CommandError, "SMS_INBOX_ENABLED is off."):
                    self.run_command(action)

    def test_15_the_audit_actions_are_declared(self):
        self.assertEqual(audit.ACTIONS["sms_inbox_access"], audit.KEPT)
