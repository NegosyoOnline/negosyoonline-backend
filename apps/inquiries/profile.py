"""A Messenger customer's name and photo (INBOX-UI U1, task z8uq9mg7c5).

A Celery task (apps/inquiries/tasks.py) asks Graph for the customer's first name, last name and
profile picture with the Page's own token. The name fills `display_name` only while it is blank or
still the old fallback. The picture URL is never saved in a table: it lives in the Django cache under
`inbox:avatar:{conversation pk}` for 48 hours, and every erasure path deletes that key (`forget`).

Nothing here logs a customer id, a name, a picture URL or a token: a failure is one warning with a
fixed code or an exception class name. The Graph call itself gives up after 3 seconds."""
import logging
from urllib.parse import urlsplit

from django.core.cache import cache
from django.db import transaction

from apps.inquiries import crypto, messenger
from apps.inquiries.models import InquiryConnection, InquiryConversation

logger = logging.getLogger(__name__)

AVATAR_TTL = 48 * 60 * 60  # seconds
RETRY_AFTER = 60 * 60  # after a failed or skipped fetch, the next try is an hour later at the earliest
MAX_NAME = 120
MAX_URL = 1000
FALLBACK_NAMES = ("", "Messenger customer")  # a name that may be replaced (stored.MESSENGER_CUSTOMER)
PHOTO_HOSTS = ("fbsbx.com", "fbcdn.net")  # where Meta serves profile pictures (the dash img-src list)


def avatar_key(pk):
    return f"inbox:avatar:{pk}"


def _retry_key(pk):
    return f"inbox:avatar_try:{pk}"


def avatar_urls(pks):
    """{pk: url or None} for the given conversations (one cache read). A cached empty value means Meta
    has no picture for the customer."""
    pks = list(pks)
    found = cache.get_many([avatar_key(pk) for pk in pks]) if pks else {}
    return {pk: (found.get(avatar_key(pk)) or None) for pk in pks}


def forget(pks):
    """Erasure: delete the cached picture (and the retry mark) of these conversations."""
    keys = []
    for pk in pks:
        keys += [avatar_key(pk), _retry_key(pk)]
    if keys:
        cache.delete_many(keys)


def schedule(pk):
    """After the surrounding transaction commits, queue a profile fetch when the cached picture is
    missing (new customer, or older than 48 hours). At most one try per hour per conversation."""
    if cache.get(avatar_key(pk)) is not None:
        return
    transaction.on_commit(lambda: _enqueue(pk))


def _enqueue(pk):
    from apps.inquiries import tasks
    try:
        if cache.add(_retry_key(pk), 1, RETRY_AFTER):
            tasks.fetch_messenger_profile.delay(pk)
    except Exception as error:  # noqa: BLE001 - a broker or cache failure never fails a webhook
        logger.warning("Messenger profile not queued (%s)", type(error).__name__)


def _name(body):
    parts = [str(body.get(k)) for k in ("first_name", "last_name") if isinstance(body.get(k), str)]
    return " ".join(" ".join(parts).split())[:MAX_NAME]


def _photo(value):
    """The picture URL when it is a plain https URL on one of Meta's picture hosts, else ""."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL:
        return ""
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
    except ValueError:
        return ""
    if parts.scheme != "https" or parts.username or parts.password:
        return ""
    return value if any(host == h or host.endswith("." + h) for h in PHOTO_HOSTS) else ""


def fetch(pk):
    """Fetch and apply the profile of conversation `pk`. Silent on every failure."""
    try:
        _fetch(pk)
    except Exception as error:  # noqa: BLE001 - only the class name is logged
        logger.warning("Messenger profile failed (%s)", type(error).__name__)


def _fetch(pk):
    conversation = InquiryConversation.objects.select_related("connection").filter(
        pk=pk, provider=InquiryConversation.MESSENGER, stores_history=True,
    ).exclude(psid="").first()
    connection = conversation.connection if conversation else None
    if connection is None or connection.status != InquiryConnection.ACTIVE or not connection.token_encrypted \
            or not messenger.configured() or conversation.page_id != connection.external_page_id:
        return
    try:
        token = crypto.decrypt(connection.token_encrypted)
    except crypto.TokenKeyError:
        return
    result = messenger.profile(conversation.psid, token)
    if not result.ok:
        logger.warning("Messenger profile not fetched (%s)", result.code)
        return
    body = result.value
    name = _name(body)
    if name:
        InquiryConversation.objects.filter(pk=pk, display_name__in=FALLBACK_NAMES).update(display_name=name)
    cache.set(avatar_key(pk), _photo(body.get("profile_pic")), AVATAR_TTL)
    if not InquiryConversation.objects.filter(pk=pk).exclude(psid="").exists():  # erased while we were asking
        forget([pk])
