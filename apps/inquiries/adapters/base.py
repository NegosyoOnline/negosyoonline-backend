"""The provider-neutral contract. An adapter turns one provider's API into these shapes and the
fixed outcome codes (apps/inquiries/outcomes.py). Nothing here names a channel as supported:
`channel_of` is for display only, and a channel is promised only after a real authorised
connection has been tested with it.
"""
import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple

REDACT_RE = re.compile(r"(page_access_token|access_token)=[^&\s\"']+", re.IGNORECASE)


def redact(text):
    """Remove a token query parameter from any provider-authored text that is ever kept."""
    return REDACT_RE.sub(r"\1=[redacted]", text or "")


@dataclass(frozen=True)
class Credentials:
    page_id: str
    token: str = field(repr=False)  # never printed, even in a traceback


class Result(NamedTuple):
    code: str
    value: Any = None

    @property
    def ok(self):
        return self.code == "ok"


@dataclass(frozen=True)
class ConversationSummary:
    ref: str
    display_name: str
    snippet: str
    updated_at: str | None
    channel: str


@dataclass(frozen=True)
class Message:
    id: str
    direction: str  # "in" (from the customer) or "out" (from the page)
    sender_kind: str  # "customer" or "page"
    text: str
    attachments: tuple  # attachment types only ("image", "file"); never a media URL
    at: str | None


@dataclass(frozen=True)
class ReplyWindow:
    open: bool
    expires_at: str | None
    reason: str


@dataclass(frozen=True)
class Thread:
    ref: str
    display_name: str
    messages: tuple
    window: ReplyWindow
    next_cursor: str | None


@dataclass(frozen=True)
class Page:
    items: tuple
    next_cursor: str | None


class ProviderAdapter:
    """Every method returns a Result (a fixed outcome code and, when ok, a value) and never
    raises. The token is used only inside the adapter's single transport function."""

    provider = ""
    stores_history = False  # True only for a channel with no external history to read

    def test_connection(self, creds):  # -> Result(None)
        raise NotImplementedError

    def list_conversations(self, creds, cursor, limit):  # -> Result(Page of ConversationSummary)
        raise NotImplementedError

    def get_thread(self, creds, ref, cursor):  # -> Result(Thread)
        raise NotImplementedError

    def send_reply(self, creds, ref, text):  # -> Result(provider message ref, "" when none)
        raise NotImplementedError

    def ref_ok(self, page_id, ref):  # -> bool: could this ref be a conversation of this page?
        return False

    def provider_url(self, creds, ref):  # -> str | None; only a verified pattern
        return None

    def channel_of(self, ref):  # -> str, display only
        return "other"
