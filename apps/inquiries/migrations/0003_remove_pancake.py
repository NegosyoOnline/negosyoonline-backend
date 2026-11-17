"""Remove the Pancake integration (task z8uq9mg1n9, Coach Ed's ruling of 2026-11-17).

The data step deletes every InquiryConnection row (all of them were Pancake connections, with
their encrypted page tokens), every InquiryConversation whose provider is "pancake" and those
conversations' InquiryReply audit rows. Stored-channel rows (website chat, email) are not
touched. The InquiryConnection table stays for the coming direct Messenger connection; its
provider column loses the "pancake" default and its choices, and the conversation provider
choices lose "pancake". The data step is not reversible (the rows are gone)."""
from django.db import migrations, models

PANCAKE = "pancake"  # the value of the rows deleted; no code refers to it any more


def delete_pancake(apps, schema_editor):
    Connection = apps.get_model("inquiries", "InquiryConnection")
    Conversation = apps.get_model("inquiries", "InquiryConversation")
    Reply = apps.get_model("inquiries", "InquiryReply")
    Message = apps.get_model("inquiries", "InquiryMessage")
    pancake = Conversation.objects.filter(provider=PANCAKE)
    # Reply and message rows first: both point at the conversation (the reply protects it).
    Reply.objects.filter(conversation__in=pancake).delete()
    Message.objects.filter(conversation__in=pancake).delete()
    pancake.delete()
    # A stored conversation never has a connection; clear the link if one somehow does, so the
    # connection rows can go (the link is protected).
    Conversation.objects.filter(connection__isnull=False).update(connection=None)
    Connection.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inquiries', '0002_stored_channels'),
    ]

    operations = [
        migrations.RunPython(delete_pancake, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='inquiryconnection',
            name='provider',
            field=models.CharField(max_length=16),
        ),
        migrations.AlterField(
            model_name='inquiryconversation',
            name='provider',
            field=models.CharField(choices=[('webchat', 'Website chat'), ('email', 'Email')], max_length=16),
        ),
    ]
