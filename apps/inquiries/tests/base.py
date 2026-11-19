"""Helpers for the inquiries tests: the switches and the stored-channel fixtures."""
from cryptography.fernet import Fernet
from django.test import override_settings
from django.utils import timezone

from apps.inquiries import crypto
from apps.inquiries.models import InquiryChannelSettings, InquiryConnection, InquiryConversation, InquiryMessage

KEY = Fernet.generate_key().decode()  # made for this test run; never a real value
TOKEN = "TESTCONNECTIONTOKEN" + "abcdefghij" * 6  # made up
REPLY_TEXT = "Hello, the sizes are in stock - this exact text must never be stored."

switched_on = override_settings(INQUIRIES_ENABLED=True, INQUIRIES_CREDENTIALS_KEY=KEY)

WC_REF = "wc_" + "a1b2c3d4e5f6" * 2  # a stored website-chat ref, made up


def chat_on(site, **fields):
    """The site's chat switched on (the owner's choice), as the channels route would."""
    row, _ = InquiryChannelSettings.objects.update_or_create(
        site=site, defaults={"webchat_enabled": True, **fields},
    )
    return row


def make_stored(site, *, ref=WC_REF, name="Maria Santos", email="", text="Do you deliver to Cebu?",
                status=InquiryConversation.OPEN, visitor_hash="", at=None, unread=True, mobile=""):
    """A website-chat conversation with one customer message, as the chat route would make it."""
    at = at or timezone.now()
    conversation = InquiryConversation.objects.create(
        site=site, provider=InquiryConversation.WEBCHAT, external_id=ref, stores_history=True,
        display_name=name, contact_name=name, contact_email=email, contact_mobile=mobile, visitor_hash=visitor_hash,
        status=status, unread=unread, last_message_at=at, last_inbound_at=at,
    )
    InquiryMessage.objects.create(
        site=site, conversation=conversation, direction=InquiryMessage.IN,
        sender_kind=InquiryMessage.CUSTOMER, body=text, created_at=at,
    )
    return conversation


def make_connection(site, *, token=TOKEN, status=InquiryConnection.ACTIVE, provider="direct"):
    """A site's connection row holding an encrypted token. The table is kept for the direct
    Messenger connection to come; no provider exists yet, so `provider` is an arbitrary value."""
    with override_settings(INQUIRIES_CREDENTIALS_KEY=KEY):
        return InquiryConnection.objects.create(
            site=site, provider=provider, status=status, external_page_id="1000000000001",
            token_encrypted=crypto.encrypt(token) if token else "",
        )
