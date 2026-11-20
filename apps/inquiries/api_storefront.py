"""The website chat routes of the storefront surface (registered by one line in
apps/storefront/api.py). Server to server only: the storefronts server calls them with the server
credential; the browser never does.

    POST /storefront/v1/chat/send/  {host, visitor_ip, visitor_secret?, name?, email?, text,
        website, opened_at, after_id?} -> {state, visitor_secret, messages, notice}
    POST /storefront/v1/chat/poll/  {host, visitor_ip, visitor_secret, after_id?}
        -> {state, visitor_secret: "", messages, notice}
    POST /storefront/v1/chat/contact/  {host, visitor_ip, visitor_secret, contact} -> {saved, kind}
        `contact` is an email or a Philippine mobile number (stored as +639XXXXXXXXX); anything
        else is 422 on `contact`. It fills only an empty field of the visitor's own conversation.
        kind is "email" or "mobile"; {saved: false, kind: ""} while chat is off or paused. A
        secret that owns no conversation here gets the same answer as one that does.

`state` is "none" (no conversation for this secret on this site), "open", "closed" or "paused".
`visitor_secret` is filled only on the send that made it; the storefronts server keeps it in an
HttpOnly cookie scoped to the store's host. `messages` are the owner's replies after `after_id`.
`opened_at` is when the widget was opened (an ISO 8601 time); a first send less than two seconds
after it answers 422 `too_fast`. A wrong or unknown secret is treated as no secret.

The site comes only from `host`; a body naming anything else is refused (422). While
INQUIRY_WEBCHAT_ENABLED is off, before inquiries.0002 is applied, and for an unknown, held or
non-Santé host, all three routes answer the 404 of an unknown site, and the switch check runs before
the body is read and before any table is touched.
"""
from datetime import datetime

from ninja import Router, Schema
from pydantic import ConfigDict

from apps.common.errors import ApiError
from apps.inquiries import webchat
from apps.inquiries.switch import stored_tables_exist, webchat_on
from apps.storefront.services import served_sante_site


def chat_gate(request):
    """The router's first step: closed unless the chat switch is on and the tables are there."""
    if not webchat_on() or not stored_tables_exist():
        raise ApiError("not_found", "Not found.", status=404)
    return True


router = Router(auth=chat_gate)


class SendIn(Schema):
    model_config = ConfigDict(extra="forbid")
    host: str = ""
    visitor_ip: str = ""
    visitor_secret: str = ""
    name: str = ""
    email: str = ""
    text: str = ""
    website: str = ""
    opened_at: datetime | None = None
    after_id: int = 0
    pack_visitor_ref: str = ""  # see ContactIn


class PollIn(Schema):
    model_config = ConfigDict(extra="forbid")
    host: str = ""
    visitor_ip: str = ""
    visitor_secret: str = ""
    after_id: int = 0


class ContactIn(Schema):
    model_config = ConfigDict(extra="forbid")
    host: str = ""
    visitor_ip: str = ""
    visitor_secret: str = ""
    contact: str = ""
    pack_visitor_ref: str = ""  # the browser's Membership Pack visit ref; ignored unless it names a visit of this site


class ContactOut(Schema):
    saved: bool
    kind: str = ""


class MessageOut(Schema):
    id: int
    text: str
    at: str


class ChatOut(Schema):
    state: str
    visitor_secret: str = ""
    messages: list[MessageOut]
    notice: str = ""


def _site(host):
    site = served_sante_site(host)
    if site is None:
        raise ApiError("not_found", "Not found.", status=404, event="site_not_found", reason="missing")
    return site


@router.post("/chat/send/", response=ChatOut)
def chat_send(request, body: SendIn):
    site = _site(body.host)
    return webchat.send(
        site, visitor_ip=body.visitor_ip, visitor_secret=body.visitor_secret, name=body.name,
        email=body.email, text=body.text, website=body.website, opened_at=body.opened_at,
        after_id=body.after_id, pack_visitor_ref=body.pack_visitor_ref,
    )


@router.post("/chat/poll/", response=ChatOut)
def chat_poll(request, body: PollIn):
    site = _site(body.host)
    return webchat.poll(
        site, visitor_ip=body.visitor_ip, visitor_secret=body.visitor_secret, after_id=body.after_id,
    )


@router.post("/chat/contact/", response=ContactOut)
def chat_contact(request, body: ContactIn):
    site = _site(body.host)
    return webchat.save_contact(
        site, visitor_ip=body.visitor_ip, visitor_secret=body.visitor_secret, contact=body.contact,
        pack_visitor_ref=body.pack_visitor_ref,
    )
