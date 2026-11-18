"""A commenter's photo and a post's text + link (task z8uq9mgkpy, added scope).

Two best-effort Graph reads with the Page's own token, queued after the webhook transaction commits
(a Celery task, apps/inquiries/tasks.py), never inside the webhook:

* the commenter's profile picture: the URL is never saved in a table. It lives in the Django cache
  under `inbox:cavatar:{comment pk}` for 48 hours (as apps/inquiries/profile.py does for Messenger
  customers) and every erasure path deletes that key (`forget`): a `remove` tombstone, retention, the
  data-deletion callback, a disconnect or deauthorize. A cached empty value means Meta gave no
  picture (or only the grey default one) and the initials stay.
* the post's text (`message`, 140 characters) and `permalink_url` (verified pattern, no query), read
  once when the post is new or its caption is empty.

Nothing here logs a customer id, a name, a picture URL or a token: a failure is one warning with a
fixed code or an exception class name."""
import logging
import re

from django.core.cache import cache
from django.db import transaction

from apps.inquiries import crypto, messenger
from apps.inquiries.models import InquiryComment, InquiryConnection, InquiryPost
from apps.inquiries.profile import AVATAR_TTL, MAX_URL, RETRY_AFTER, _photo

logger = logging.getLogger(__name__)

POST_DONE_TTL = 24 * 60 * 60  # after a good read, the post is not asked again for a day
CAPTION_MAX = 140
AUTHOR_ID_RE = re.compile(r"^[0-9]{1,64}$")


def avatar_key(pk):
    return f"inbox:cavatar:{pk}"


def _retry_key(pk):
    return f"inbox:cavatar_try:{pk}"


def _post_key(pk):
    return f"inbox:cpost_try:{pk}"


def avatar_urls(pks):
    """{comment pk: url or None} (one cache read). A cached empty value means no picture."""
    pks = list(pks)
    found = cache.get_many([avatar_key(pk) for pk in pks]) if pks else {}
    return {pk: (found.get(avatar_key(pk)) or None) for pk in pks}


def forget(pks):
    """Erasure: delete the cached picture (and the retry mark) of these comments."""
    keys = []
    for pk in pks:
        keys += [avatar_key(pk), _retry_key(pk)]
    if keys:
        cache.delete_many(keys)


# -- queueing ---------------------------------------------------------------------------------

def schedule(comment, post):
    """Queue the reads that are missing, after the surrounding transaction commits."""
    if not comment.is_page and comment.author_psid and cache.get(avatar_key(comment.pk)) is None:
        transaction.on_commit(lambda pk=comment.pk: _enqueue(_retry_key(pk), "fetch_comment_avatar", pk))
    if _needs_post(post):
        transaction.on_commit(lambda pk=post.pk: _enqueue(_post_key(pk), "fetch_post_text", pk))


def _enqueue(mark, task_name, pk):
    from apps.inquiries import tasks
    try:
        if cache.add(mark, 1, RETRY_AFTER):
            getattr(tasks, task_name).delay(pk)
    except Exception as error:  # noqa: BLE001 - a broker or cache failure never fails a webhook
        logger.warning("Comment media not queued (%s)", type(error).__name__)


def _fallback_link(post):
    return f"https://www.facebook.com/{post.external_id}"


def _needs_post(post):
    return not post.caption or not post.permalink or post.permalink == _fallback_link(post)


# -- the reads --------------------------------------------------------------------------------

def _token(connection):
    if connection is None or connection.status != InquiryConnection.ACTIVE or not connection.token_encrypted \
            or not messenger.configured():
        return None
    try:
        return crypto.decrypt(connection.token_encrypted)
    except crypto.TokenKeyError:
        return None


def fetch_avatar(pk):
    """Fetch the picture of comment `pk`. Silent on every failure."""
    try:
        _fetch_avatar(pk)
    except Exception as error:  # noqa: BLE001 - only the class name is logged
        logger.warning("Comment photo failed (%s)", type(error).__name__)


def _fetch_avatar(pk):
    comment = InquiryComment.objects.select_related("connection").filter(pk=pk, removed=False, is_page=False).first()
    if comment is None or not AUTHOR_ID_RE.match(comment.author_psid or ""):
        return
    token = _token(comment.connection)
    if token is None or comment.connection.external_page_id == "":
        return
    result = messenger.commenter_picture(comment.author_psid, token)
    if not result.ok:
        logger.warning("Comment photo not fetched (%s)", result.code)
        return
    data = result.value.get("data")
    data = data if isinstance(data, dict) else {}
    url = "" if data.get("is_silhouette") is True else _photo(data.get("url"))
    cache.set(avatar_key(pk), url, AVATAR_TTL)
    if not InquiryComment.objects.filter(pk=pk, removed=False).exists():  # removed or erased while we were asking
        forget([pk])


def fetch_post(pk):
    """Fetch the text and link of post `pk`. Silent on every failure."""
    try:
        _fetch_post(pk)
    except Exception as error:  # noqa: BLE001
        logger.warning("Post text failed (%s)", type(error).__name__)


def _fetch_post(pk):
    from apps.inquiries import comments  # comments imports this module
    post = InquiryPost.objects.select_related("connection").filter(pk=pk).first()
    if post is None or not comments.ID_RE.match(post.external_id):
        return
    token = _token(post.connection)
    if token is None:
        return
    result = messenger.post_text(post.external_id, token)
    if not result.ok:
        logger.warning("Post text not fetched (%s)", result.code)
        return
    body = result.value
    changes = {}
    caption = comments._text(body.get("message"), CAPTION_MAX)
    if caption and caption != post.caption:
        changes["caption"] = caption
    link = body.get("permalink_url")
    if isinstance(link, str) and len(link) <= MAX_URL and comments.PERMALINK_RE.match(link) and "?" not in link \
            and link != post.permalink:
        changes["permalink"] = link
    if changes:
        InquiryPost.objects.filter(pk=post.pk).update(**changes)
    cache.set(_post_key(pk), 1, POST_DONE_TTL)
