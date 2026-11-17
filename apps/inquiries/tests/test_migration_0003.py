"""Migration 0003 (remove Pancake): with Pancake connection, conversation and reply rows next to
stored-channel rows, migrating 0002 -> 0003 deletes only the Pancake ones. The schema is really
migrated back to 0002, the rows are made, and it is migrated forward again."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.sites.tests.base import make_account, make_site

OLD = "pancake"  # the provider value of the rows the migration deletes


def migrate(*targets):
    executor = MigrationExecutor(connection)
    executor.migrate(list(targets))


def historical(target):
    """The models as they were at migration `target` (the current models have later columns)."""
    state = MigrationExecutor(connection).loader.project_state([("inquiries", target)])
    return {name: state.apps.get_model("inquiries", name) for name in (
        "InquiryChannelSettings", "InquiryConnection", "InquiryConversation", "InquiryMessage", "InquiryReply",
    )}


class RemovePancakeMigrationTests(TransactionTestCase):
    def setUp(self):
        migrate(("inquiries", "0002_stored_channels"))
        # Every app, not only inquiries: the schema is put back for the tests that follow.
        self.addCleanup(lambda: migrate(*MigrationExecutor(connection).loader.graph.leaf_nodes()))
        self.account = make_account()
        self.site = make_site(self.account)
        self.other = make_site(make_account("other@example.test"), name="other-store", first=False)

    def plant(self, site, suffix):
        m = historical("0002_stored_channels")
        InquiryConnection, InquiryConversation = m["InquiryConnection"], m["InquiryConversation"]
        InquiryMessage, InquiryReply, InquiryChannelSettings = m["InquiryMessage"], m["InquiryReply"], m["InquiryChannelSettings"]
        connection_row = InquiryConnection.objects.create(
            site_id=site.pk, provider=OLD, status="active", token_encrypted="CIPHER-" + suffix, external_page_id="p-" + suffix,
        )
        old = InquiryConversation.objects.create(
            site_id=site.pk, connection=connection_row, provider=OLD, external_id="old-" + suffix, display_name="Fb Person",
        )
        old_reply = InquiryReply.objects.create(
            site_id=site.pk, conversation=old, author_id=site.account_id, idempotency_key="old-key-" + suffix, body_length=4,
        )
        chat = InquiryConversation.objects.create(
            site_id=site.pk, provider="webchat", external_id="wc_" + suffix * 24, stores_history=True, contact_name="Chat Person",
        )
        message = InquiryMessage.objects.create(
            site_id=site.pk, conversation=chat, direction="in", sender_kind="customer", body="Hello",
        )
        mail = InquiryConversation.objects.create(
            site_id=site.pk, provider="email", external_id="em_" + suffix * 24, stores_history=True, contact_email="a@b.test",
        )
        chat_reply = InquiryReply.objects.create(
            site_id=site.pk, conversation=chat, author_id=site.account_id, idempotency_key="chat-key-" + suffix, body_length=4,
            message=message,
        )
        settings_row = InquiryChannelSettings.objects.create(site_id=site.pk, webchat_enabled=True)
        return {
            "old": [connection_row, old, old_reply],
            "kept": [chat, mail, message, chat_reply, settings_row],
        }

    def test_only_the_pancake_rows_are_deleted(self):
        a, b = self.plant(self.site, "a"), self.plant(self.other, "b")
        migrate(("inquiries", "0003_remove_pancake"))
        m = historical("0003_remove_pancake")
        InquiryConnection, InquiryConversation = m["InquiryConnection"], m["InquiryConversation"]
        InquiryMessage, InquiryReply = m["InquiryMessage"], m["InquiryReply"]
        for planted in (a, b):
            for row in planted["old"]:
                self.assertFalse(type(row).objects.filter(pk=row.pk).exists(), row)
            for row in planted["kept"]:
                self.assertTrue(type(row).objects.filter(pk=row.pk).exists(), row)
        self.assertEqual(InquiryConnection.objects.count(), 0)
        self.assertEqual(
            sorted(InquiryConversation.objects.values_list("provider", flat=True)), ["email", "email", "webchat", "webchat"],
        )
        kept = InquiryConversation.objects.get(pk=a["kept"][0].pk)
        self.assertEqual((kept.contact_name, kept.stores_history), ("Chat Person", True))
        self.assertEqual(InquiryReply.objects.count(), 2)
        self.assertEqual(InquiryMessage.objects.count(), 2)

    def test_it_runs_on_empty_tables_and_forward_twice_is_safe(self):
        migrate(("inquiries", "0003_remove_pancake"))
        m = historical("0003_remove_pancake")
        InquiryConnection, InquiryConversation = m["InquiryConnection"], m["InquiryConversation"]
        InquiryMessage, InquiryReply = m["InquiryMessage"], m["InquiryReply"]
        self.assertEqual(InquiryConnection.objects.count(), 0)
        migrate(("inquiries", "0003_remove_pancake"))
        m = historical("0003_remove_pancake")
        InquiryConnection, InquiryConversation = m["InquiryConnection"], m["InquiryConversation"]
        InquiryMessage, InquiryReply = m["InquiryMessage"], m["InquiryReply"]
