"""Comment auto-replies (task z8uq9mg9pj; Coach Ed 2026-11-18 01:00 HKT, CM1-CM6): the evaluation
of a new comment against the site's rules, the queued sends, the caps and the back-off.

Nothing here runs in the webhook request beyond `schedule`, which only queues a Celery task. Each
task re-checks the platform switch, the site master, the pause and the rule before it sends. The
limits are in comment_limits.py. No token, Meta body or URL is logged or stored: a failure is a
fixed outcome code and, for the back-off, Meta's numeric error code read through
messenger.last_error_code().
"""
import logging
import random
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.inquiries import comment_limits as limits
from apps.inquiries import comment_rules, comments, crypto, limiter, messenger, outcomes, switch
from apps.inquiries.models import (
    InquiryAutoReply, InquiryComment, InquiryCommentAction, InquiryCommentAutomation, InquiryCommentRule,
    InquiryConnection,
)

logger = logging.getLogger(__name__)

MANILA = ZoneInfo("Asia/Manila")
A = InquiryCommentAction
COUNTED = (A.QUEUED, A.SENDING, A.SENT)
HOUR_CAP, DAY_CAP = "hour_cap", "day_cap"
LABELS = {
    A.QUEUED: "Auto-reply queued", A.SENT: "Auto-replied", A.FAILED: "Auto-reply failed",
    A.CANCELLED: "Auto-reply cancelled", A.LIMIT: "Not auto-answered (limit)",
}
BANNERS = {
    "paused_rate_limit": "Facebook asked us to slow down, so auto-replies are paused for 1 hour.",
    "needs_reenable": "Auto-replies were switched off after Facebook slowed us down 3 times today. "
                      "Turn them back on when you are ready.",
}


def platform_on():
    """INQUIRY_COMMENT_AUTOMATION_ENABLED (with comments) and the tables of inquiries.0007 and 0006."""
    return switch.automation_on() and switch.automation_tables_exist() and switch.comment_tables_exist()


# -- the site's switch and counters ---------------------------------------------------------------

def automation_of(site):
    row, _ = InquiryCommentAutomation.objects.get_or_create(site=site)
    return row


def _locked(site):
    automation_of(site)
    return InquiryCommentAutomation.objects.select_for_update().get(site=site)


def day_start(now):
    local = now.astimezone(MANILA)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def counted(site, since):
    """Automatic replies (public and private each) queued, sending or sent since `since`."""
    return InquiryCommentAction.objects.filter(
        site=site, source=A.RULE, state__in=COUNTED, kind__in=(A.PUBLIC, A.PRIVATE), created_at__gte=since,
    ).count()


def paused(row, now):
    return bool(row.paused_until and row.paused_until > now)


def banner(row, now):
    if row.pause_reason == InquiryCommentAutomation.REPEATED and not row.enabled:
        return {"code": "needs_reenable", "message": BANNERS["needs_reenable"], "paused_until": None}
    if paused(row, now):
        return {"code": "paused_rate_limit", "message": BANNERS["paused_rate_limit"],
                "paused_until": row.paused_until.isoformat()}
    return None


def automation_out(site, now=None):
    now = now or timezone.now()
    row = InquiryCommentAutomation.objects.filter(site=site).first() or InquiryCommentAutomation(site=site)  # a read writes nothing
    _, reason = comments.readiness(site)
    ready = reason == ""
    return {
        "enabled": row.enabled, "active": row.enabled and ready and not paused(row, now),
        "ready": ready, "reason": reason, "banner": banner(row, now),
        "today": {"count": counted(site, day_start(now)), "cap": limits.DAY_CAP,
                  "date": now.astimezone(MANILA).date().isoformat()},
        "hour": {"count": counted(site, now - timedelta(hours=1)), "cap": limits.HOUR_CAP},
        "limits": {
            "per_person_hours": int(limits.PER_PERSON_WINDOW.total_seconds() // 3600),
            "delay_min_seconds": limits.DELAY_MIN, "delay_max_seconds": limits.DELAY_MAX,
            "max_rules": limits.MAX_RULES, "max_keywords": limits.MAX_KEYWORDS,
            "keyword_max_chars": limits.KEYWORD_MAX_CHARS, "max_posts": limits.MAX_POSTS,
            "public_max_chars": limits.PUBLIC_MAX, "private_max_chars": limits.PRIVATE_MAX,
            "max_age_days": limits.MAX_AGE.days,
        },
    }


def set_enabled(site, enabled):
    """The owner's master switch. On clears any pause and the day's strikes; off cancels the queue."""
    with transaction.atomic():
        row = _locked(site)
        row.enabled = bool(enabled)
        row.paused_until = None
        row.pause_reason = InquiryCommentAutomation.NONE
        if enabled:
            row.strikes, row.strikes_date = 0, None
        row.save()
    if not enabled:
        cancel_site(site)
    return automation_of(site)


# -- cancelling ---------------------------------------------------------------------------------

def cancel_site(site):
    return InquiryCommentAction.objects.filter(site=site, source=A.RULE, state=A.QUEUED).update(state=A.CANCELLED)


def cancel_rule(rule):
    ids = InquiryAutoReply.objects.filter(rule=rule, action__state=A.QUEUED).values_list("action_id", flat=True)
    return InquiryCommentAction.objects.filter(pk__in=list(ids), state=A.QUEUED).update(state=A.CANCELLED)


def cancel_unmatched(rule):
    """After a rule changed: cancel its queued replies for comments it no longer answers."""
    if not rule.enabled:
        return cancel_rule(rule)
    queued = InquiryCommentAction.objects.filter(
        auto__rule=rule, state=A.QUEUED).select_related("comment", "comment__post")
    gone = [a.pk for a in queued if not comment_rules.matches(rule, a.comment.text, a.comment.post.external_id)]
    return InquiryCommentAction.objects.filter(pk__in=gone, state=A.QUEUED).update(state=A.CANCELLED)


# -- back-off -----------------------------------------------------------------------------------

def strike(site, now=None):
    """One Meta rate-limit or spam error: pause 1 hour; the third in a Manila day switches the
    master off until the owner turns it on again. Queued replies are cancelled, not retried."""
    now = now or timezone.now()
    if not InquiryCommentAutomation.objects.filter(site=site).exists():
        return
    with transaction.atomic():
        row = _locked(site)
        today = now.astimezone(MANILA).date()
        if row.strikes_date != today:
            row.strikes, row.strikes_date = 0, today
        row.strikes += 1
        if row.strikes >= limits.STRIKES_PER_DAY:
            row.enabled, row.paused_until, row.pause_reason = False, None, InquiryCommentAutomation.REPEATED
        else:
            row.paused_until, row.pause_reason = now + limits.PAUSE, InquiryCommentAutomation.RATE_LIMIT
        row.save()
    cancel_site(site)


def is_strike(code):
    """True for a send that failed with a rate-limit outcome or a Meta spam/limit error code."""
    return code == outcomes.RATE_LIMITED or messenger.last_error_code() in limits.STRIKE_CODES


def note_manual(site, code):
    """A manual reply that Meta refused with a rate-limit or spam error is a strike too. The pause
    never blocks manual replies."""
    try:
        if platform_on() and is_strike(code) and automation_of(site).enabled:
            strike(site)
    except Exception as error:  # noqa: BLE001 - never break the owner's reply
        logger.error("Auto-reply back-off not recorded (%s)", type(error).__name__)


# -- the webhook hook ---------------------------------------------------------------------------

def schedule(comment_pk):
    """After the comment is stored: queue the evaluation task. Never raises into the webhook."""
    if not platform_on():
        return
    from apps.inquiries import tasks
    try:
        transaction.on_commit(lambda: tasks.evaluate_comment_rules.delay(comment_pk))
    except Exception as error:  # noqa: BLE001
        logger.error("Auto-reply evaluation not queued (%s)", type(error).__name__)


# -- evaluation ---------------------------------------------------------------------------------

def _create_row(comment, rule, kind, state, now, skip="", delay=0):
    action = InquiryCommentAction.objects.create(
        site=comment.site, comment=comment, kind=kind, source=A.RULE, state=state, created_at=now,
    )
    InquiryAutoReply.objects.create(
        action=action, site=comment.site, rule=rule, skip_reason=skip,
        send_after=now + timedelta(seconds=delay) if state == A.QUEUED else None,
    )
    return action


def evaluate(comment_pk, now=None):
    """Decide what to do with one stored comment. Returns a word for the outcome: "queued", "limit",
    or the reason it was skipped. Safe to run twice (the unique constraint stops a second reply)."""
    now = now or timezone.now()
    if not platform_on():
        return "off"
    comment = InquiryComment.objects.select_related("post", "site").filter(pk=comment_pk).first()
    if comment is None or comment.removed:
        return "gone"
    site = comment.site
    row = InquiryCommentAutomation.objects.filter(site=site).first()
    if row is None or not row.enabled:
        return "site_off"
    if paused(row, now):
        return "paused"
    if comments.readiness(site)[1]:
        return "not_ready"
    if comment.is_page:
        return "own_comment"
    if comment.is_hidden:
        return "hidden"
    if not comment.author_psid:
        return "no_author"
    if comment.parent_external_id != comment.post.external_id:
        return "not_top_level"
    if now - comment.created_at > limits.MAX_AGE:
        return "too_old"
    if comment.public_reply_at or comment.private_reply_at or InquiryComment.objects.filter(
            site=site, post=comment.post, parent_external_id=comment.external_id, is_page=True).exists():
        return "answered"
    if InquiryCommentAction.objects.filter(comment=comment, source=A.RULE).exists():
        return "already"
    rule = next(
        (r for r in InquiryCommentRule.objects.filter(site=site, enabled=True).order_by("pk")
         if comment_rules.matches(r, comment.text, comment.post.external_id)), None)
    if rule is None:
        return "no_rule"
    if InquiryCommentAction.objects.filter(
            site=site, source=A.RULE, state__in=COUNTED, comment__post=comment.post,
            comment__author_psid=comment.author_psid, created_at__gt=now - limits.PER_PERSON_WINDOW,
    ).exclude(comment=comment).exists():
        return "person_limit"
    kinds = [A.PUBLIC] + ([A.PRIVATE] if rule.private_text else [])
    queued, limited = [], False
    with transaction.atomic():
        locked = _locked(site)
        if not locked.enabled or paused(locked, now):
            return "paused"
        hour_n = counted(site, now - timedelta(hours=1))
        day_n = counted(site, day_start(now))
        for kind in kinds:
            reason = HOUR_CAP if hour_n >= limits.HOUR_CAP else DAY_CAP if day_n >= limits.DAY_CAP else ""
            delay = random.randint(limits.DELAY_MIN, limits.DELAY_MAX)
            try:
                with transaction.atomic():
                    action = _create_row(comment, rule, kind, A.LIMIT if reason else A.QUEUED, now, reason, delay)
            except IntegrityError:  # already answered once (auto_once) or a private reply exists
                continue
            if reason:
                limited = True
            else:
                hour_n += 1
                day_n += 1
                queued.append((action.pk, delay))
    if queued:
        from apps.inquiries import tasks
        transaction.on_commit(lambda: [
            tasks.send_comment_auto_reply.apply_async(args=[pk], countdown=delay) for pk, delay in queued
        ])
    return "queued" if queued else ("limit" if limited else "already")


# -- sending ------------------------------------------------------------------------------------

def _cancel(action):
    InquiryCommentAction.objects.filter(pk=action.pk, state=A.QUEUED).update(state=A.CANCELLED)
    return "cancelled"


def _recheck(action, auto, comment, now):
    """The reason this queued reply must not go out now, or ''."""
    site = action.site
    if not platform_on():
        return "off"
    row = InquiryCommentAutomation.objects.filter(site=site).first()
    if row is None or not row.enabled:
        return "site_off"
    if paused(row, now):
        return "paused"
    if comments.readiness(site)[1]:
        return "not_ready"
    rule = auto.rule
    if rule is None or not rule.enabled or not comment_rules.matches(rule, comment.text, comment.post.external_id):
        return "rule_off"
    if comment.removed or comment.is_hidden or comment.is_page:
        return "comment_gone"
    if action.kind == A.PUBLIC and comment.public_reply_at:
        return "answered"
    if action.kind == A.PRIVATE and comment.private_reply_at:
        return "answered"
    if action.kind == A.PRIVATE and now - comment.created_at > comments.PRIVATE_WINDOW:
        return "window_closed"
    return ""


def send(action_pk, now=None):
    """Send one queued automatic reply. Returns the final state word, or "skipped"."""
    now = now or timezone.now()
    action = InquiryCommentAction.objects.select_related("site", "comment", "comment__post").filter(
        pk=action_pk, source=A.RULE).first()
    if action is None or action.state != A.QUEUED:
        return "skipped"
    comment = action.comment
    auto = InquiryAutoReply.objects.select_related("rule").filter(action=action).first()
    if auto is None:
        return _cancel(action)
    if _recheck(action, auto, comment, now):
        return _cancel(action)
    rule = auto.rule
    site = action.site
    connection = InquiryConnection.objects.filter(site=site, provider=messenger.PROVIDER).first()
    template_text = rule.public_text if action.kind == A.PUBLIC else rule.private_text
    if connection is None or not template_text:
        return _cancel(action)
    with transaction.atomic():
        _locked(site)  # one send at a time per site, so "not the same variant twice in a row" holds
        claimed = InquiryCommentAction.objects.filter(pk=action.pk, state=A.QUEUED).update(state=A.SENDING)
        if not claimed:
            return "skipped"
        last = InquiryAutoReply.objects.filter(
            site=site, action__kind=action.kind, action__comment__post=comment.post, chosen_at__isnull=False,
        ).exclude(variant_hash="").order_by("-chosen_at", "-pk").values_list("variant_hash", flat=True).first()
        variant, digest = comment_rules.choose(comment_rules.parse(template_text), last or "")
        InquiryAutoReply.objects.filter(pk=auto.pk).update(variant_hash=digest, chosen_at=now)
    text = comment_rules.fill(variant, comment_rules.first_name(comment.author_name))
    action.refresh_from_db()
    action.body_length = len(text)
    if not limiter.acquire(connection.pk):
        InquiryCommentAction.objects.filter(pk=action.pk).update(state=A.QUEUED)
        from apps.inquiries import tasks
        tasks.send_comment_auto_reply.apply_async(args=[action.pk], countdown=15)
        return "retry"
    try:
        token = crypto.decrypt(connection.token_encrypted)
    except crypto.TokenKeyError:
        action.state, action.error_code = A.FAILED, outcomes.NOT_CONFIGURED
        action.save(update_fields=["state", "error_code", "body_length"])
        return action.state
    if action.kind == A.PUBLIC:
        result = messenger.reply_comment(token, comment.external_id, text)
    else:
        result = messenger.private_reply(connection.external_page_id, token, comment.external_id, text)
    done = timezone.now()
    if result.ok:
        action.state, action.error_code = A.SENT, ""
        action.meta_id = result.value if isinstance(result.value, str) else ""
        if action.kind == A.PUBLIC:
            InquiryComment.objects.filter(pk=comment.pk).update(public_reply_at=done, unread=False)
            if comments.ID_RE.match(action.meta_id):
                comments._store_page_reply(connection, comment, action.meta_id, text, done)
        else:
            InquiryComment.objects.filter(pk=comment.pk).update(private_reply_at=done, unread=False)
            action.text = text[:comments.MAX_PRIVATE]
    else:
        struck = is_strike(result.code)
        action.state = A.BLOCKED_RATE if result.code == outcomes.RATE_LIMITED else A.FAILED
        action.error_code = result.code
        if result.code in outcomes.CREDENTIAL_FAILURES:
            InquiryConnection.objects.filter(pk=connection.pk).update(
                status=InquiryConnection.INVALID, last_test_at=done, last_test_outcome=result.code,
            )
        if struck:
            strike(site, done)
    action.save(update_fields=["state", "error_code", "meta_id", "text", "body_length"])
    return action.state


# -- what the comment list shows ----------------------------------------------------------------

def _summary(rows):
    """{state, label, reason, public, private} of one comment's automatic rows, or None."""
    if not rows:
        return None
    per = {A.PUBLIC: None, A.PRIVATE: None}
    reason = ""
    for action, skip in rows:
        state = "queued" if action.state == A.SENDING else "failed" if action.state == A.BLOCKED_RATE else action.state
        per[action.kind] = state
        reason = reason or (skip if action.state == A.LIMIT else "")
    states = [s for s in per.values() if s]
    for state in ("queued", "limit", "failed", "cancelled", "sent"):
        if state in states:
            break
    return {"state": state, "label": LABELS[state], "reason": reason if state == "limit" else "",
            "public": per[A.PUBLIC], "private": per[A.PRIVATE]}


def prefetch(comments_list):
    """Attach the auto_reply summary to each comment object with one query."""
    if not platform_on() or not comments_list:
        return
    by_comment = {}
    rows = InquiryCommentAction.objects.filter(
        comment__in=[c.pk for c in comments_list], source=A.RULE, kind__in=(A.PUBLIC, A.PRIVATE),
    ).select_related("auto").order_by("pk")
    for action in rows:
        auto = getattr(action, "auto", None)
        by_comment.setdefault(action.comment_id, []).append((action, auto.skip_reason if auto else ""))
    for comment in comments_list:
        comment._auto_reply = _summary(by_comment.get(comment.pk, []))


def auto_reply_of(comment):
    """The contract's `auto_reply` for one comment: null while automation is off."""
    if not platform_on():
        return None
    if not hasattr(comment, "_auto_reply"):
        prefetch([comment])
    return comment._auto_reply


def skip_reason_of(action):
    if action.source != A.RULE or action.state != A.LIMIT or not platform_on():
        return ""
    auto = InquiryAutoReply.objects.filter(action=action).first()
    return auto.skip_reason if auto else ""
