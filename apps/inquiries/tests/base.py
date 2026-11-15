"""Helpers for the inquiries tests. Pancake is never called: pancake._open is replaced by a fake
that records each request and answers with the recorded payloads below (the shapes TWCinc's
client and Pancake's public reference showed; field names the live gate has not confirmed are
marked in adapters/pancake.py). Every token and id here is made up."""
import io
import json
import urllib.error
import urllib.parse
from unittest import mock

from cryptography.fernet import Fernet
from django.test import override_settings

from django.utils import timezone

from apps.inquiries import crypto
from apps.inquiries.models import InquiryChannelSettings, InquiryConnection, InquiryConversation, InquiryMessage

KEY = Fernet.generate_key().decode()  # made for this test run; never a real value
TOKEN = "TESTPAGETOKEN" + "abcdefghij" * 6  # 73 characters, made up
PAGE = "1000000000001"
REF = f"{PAGE}_7000000000001"
OTHER_REF = f"{PAGE}_7000000000002"
SECRET_TEXT = "SECRET MESSAGE FROM PANCAKE"
REPLY_TEXT = "Hello, the sizes are in stock - this exact text must never be stored."

switched_on = override_settings(INQUIRIES_ENABLED=True, INQUIRIES_CREDENTIALS_KEY=KEY)

# -- recorded payloads --------------------------------------------------------------------------
LIST_OK = {
    "success": True,
    "conversations": [
        {
            "id": REF, "snippet": "Do you have this in medium?", "updated_at": "2026-11-12T09:30:00.000000",
            "customers": [{"name": "Maria Santos", "fb_id": "7000000000001"}], "type": "INBOX",
        },
        {
            "id": OTHER_REF, "snippet": "Thank you!", "updated_at": "2026-11-11T18:00:00Z",
            "customers": [{"name": "Jun Reyes", "fb_id": "7000000000002"}], "type": "INBOX",
        },
    ],
}
THREAD_OPEN = {
    "success": True, "can_inbox": True,
    "messages": [
        {
            "id": "m_1", "message": "Do you have this in medium?", "inserted_at": "2026-11-12T09:30:00.000000",
            "from": {"id": "7000000000001", "name": "Maria Santos"},
            "attachments": [{"type": "photo", "url": "https://scontent.fbcdn.example/signed?x=1"}],
        },
        {
            "id": "m_2", "message": "Yes, we do.", "inserted_at": "2026-11-12T09:31:00.000000",
            "from": {"id": PAGE, "name": "Store page"},
        },
    ],
}
THREAD_CLOSED = {**THREAD_OPEN, "can_inbox": False}
SEND_OK = {"success": True, "message_id": "m_abc123"}
BAD_TOKEN = {"success": False, "error_code": 102, "message": SECRET_TEXT}
UNKNOWN_FAILURE = {"success": False, "error_code": 999, "message": SECRET_TEXT}


def configure(site, *, page=PAGE, token=TOKEN, status=InquiryConnection.ACTIVE):
    """A site's connection, with the token encrypted as the route would."""
    with override_settings(INQUIRIES_CREDENTIALS_KEY=KEY):
        return InquiryConnection.objects.update_or_create(
            site=site, provider=InquiryConnection.PANCAKE,
            defaults={
                "external_page_id": page, "token_encrypted": crypto.encrypt(token) if token else "",
                "status": status,
            },
        )[0]


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self, size=-1):
        return self._raw if size < 0 else self._raw[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, payload=None):
    body = json.dumps(payload or {"message": SECRET_TEXT}).encode()
    return urllib.error.HTTPError("https://pages.fm/api/public_api/v1/x?page_access_token=LEAK", status, "x", {}, io.BytesIO(body))


def kind_of(request):
    path = urllib.parse.urlsplit(request.full_url).path
    if request.get_method() == "POST":
        return "send"
    return "thread" if path.endswith("/messages") else "list"


class FakePancake:
    """Patches the network. `listing`, `thread` and `send` each take one answer or a list used in
    order (the last repeats). An answer is a payload dict (HTTP 200), a (status, payload) pair,
    raw bytes, or an exception to raise."""

    def __init__(self, *, listing=LIST_OK, thread=THREAD_OPEN, send=SEND_OK):
        self.answers = {"list": self._seq(listing), "thread": self._seq(thread), "send": self._seq(send)}
        self.requests = []

    @staticmethod
    def _seq(answer):
        return answer if isinstance(answer, list) else [answer]

    def __call__(self, request, timeout):
        kind = kind_of(request)
        self.requests.append(request)
        sequence = self.answers[kind]
        seen = sum(1 for r in self.requests if kind_of(r) == kind)
        answer = sequence[min(seen, len(sequence)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        status, payload = answer if isinstance(answer, tuple) else (200, answer)
        if status >= 400:
            raise http_error(status, payload if isinstance(payload, dict) else None)
        return FakeResponse(status, payload)

    def __enter__(self):
        self.patcher = mock.patch("apps.inquiries.adapters.pancake._open", self)
        self.patcher.start()
        return self

    def __exit__(self, *exc):
        self.patcher.stop()
        return False

    def kinds(self):
        return [kind_of(r) for r in self.requests]

    def sent_bodies(self):
        return [json.loads(r.data) for r in self.requests if kind_of(r) == "send"]


def no_limiter(testcase):
    """Give every call a permit at once, so route tests do not wait on the clock."""
    patcher = mock.patch("apps.inquiries.limiter.acquire", return_value=True)
    patcher.start()
    testcase.addCleanup(patcher.stop)


WC_REF = "wc_" + "a1b2c3d4e5f6" * 2  # a stored website-chat ref, made up


def chat_on(site, **fields):
    """The site's chat switched on (the owner's choice), as the channels route would."""
    row, _ = InquiryChannelSettings.objects.update_or_create(
        site=site, defaults={"webchat_enabled": True, **fields},
    )
    return row


def make_stored(site, *, ref=WC_REF, name="Maria Santos", email="", text="Do you deliver to Cebu?",
                status=InquiryConversation.OPEN, visitor_hash="", at=None, unread=True):
    """A website-chat conversation with one customer message, as the chat route would make it."""
    at = at or timezone.now()
    conversation = InquiryConversation.objects.create(
        site=site, provider=InquiryConversation.WEBCHAT, external_id=ref, stores_history=True,
        display_name=name, contact_name=name, contact_email=email, visitor_hash=visitor_hash,
        status=status, unread=unread, last_message_at=at, last_inbound_at=at,
    )
    InquiryMessage.objects.create(
        site=site, conversation=conversation, direction=InquiryMessage.IN,
        sender_kind=InquiryMessage.CUSTOMER, body=text, created_at=at,
    )
    return conversation
