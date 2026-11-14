"""The member's dashboard reads, added to the storefront surface next to the member routes
(apps/storefront/api.py mounts this router under /member/):

    POST /storefront/v1/member/notices/                     {host, token} -> {notices: [notice]}
    POST /storefront/v1/member/notifications/               {host, token, limit?} -> {notifications, unread_count}
    POST /storefront/v1/member/notifications/{id}/read/     {host, token} -> {notification, unread_count}
    POST /storefront/v1/member/notifications/read-all/      {host, token} -> {marked, unread_count}

Same gates as the other member routes: the storefronts server's credential, MEMBERS_ENABLED, a
served Santé host (404), and a live member session of that host's site (401 member_session_invalid).
Everything is read under the session's own site and member; a notification id of another member
or site answers the same 404 as a missing one.
"""
from datetime import datetime

from ninja import Router, Schema

from . import notices
from .api import _session, _site

router = Router()

DEFAULT_LIMIT, MAX_LIMIT = 20, 50


class SessionIn(Schema):
    host: str = ""
    token: str = ""


class NoticeOut(Schema):
    id: str
    kind: str
    title: str
    body: str
    image_url: str | None
    starts_at: datetime | None
    ends_at: datetime | None


class NoticesOut(Schema):
    notices: list[NoticeOut]


class NotificationOut(Schema):
    id: str
    kind: str
    title: str
    url: str
    created_at: datetime
    read_at: datetime | None


class NotificationsIn(SessionIn):
    limit: int = DEFAULT_LIMIT


class NotificationsOut(Schema):
    notifications: list[NotificationOut]
    unread_count: int


class ReadOut(Schema):
    notification: NotificationOut
    unread_count: int


class ReadAllOut(Schema):
    marked: int
    unread_count: int


def _unread(member):
    return notices.notifications_of(member).filter(read_at__isnull=True).count()


@router.post("/notices/", response=NoticesOut)
def member_notices(request, body: SessionIn):
    site = _site(body.host)
    _session(site, body.token)
    return {"notices": [notices.member_notice_out(n) for n in notices.visible_to_members(site)]}


@router.post("/notifications/", response=NotificationsOut)
def member_notifications(request, body: NotificationsIn):
    member = _session(_site(body.host), body.token).member
    limit = min(max(body.limit, 1), MAX_LIMIT)
    rows = notices.notifications_of(member)[:limit]
    return {"notifications": [notices.notification_out(r) for r in rows], "unread_count": _unread(member)}


@router.post("/notifications/read-all/", response=ReadAllOut)
def member_read_all(request, body: SessionIn):
    member = _session(_site(body.host), body.token).member
    marked = notices.mark_all_read(member)
    return {"marked": marked, "unread_count": _unread(member)}


@router.post("/notifications/{notification_id}/read/", response=ReadOut)
def member_read_one(request, notification_id: str, body: SessionIn):
    member = _session(_site(body.host), body.token).member
    row = notices.mark_read(member, notification_id)
    return {"notification": notices.notification_out(row), "unread_count": _unread(member)}
