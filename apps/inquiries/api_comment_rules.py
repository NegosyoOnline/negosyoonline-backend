"""The comment auto-reply rules routes (task z8uq9mg9pj), under /v1/sites/{site_id}/inquiries/comment-rules/.
The contract is docs/c2-api-contract.md; this docstring only lists the routes:

    GET    comment-rules/               {automation, rules}
    PUT    comment-rules/automation/    {enabled}   the site master
    GET    comment-rules/posts/         ?q=&cursor=  this site's posts and ads, for the picker
    POST   comment-rules/preview/       {template, kind}
    POST   comment-rules/               create a rule
    GET    comment-rules/{ref}/         PUT (any subset of fields)    DELETE

While INQUIRY_COMMENT_AUTOMATION_ENABLED (with comments), the Meta app values or the tables of
inquiries.0007 are missing, every route answers 404, signed in or not."""
import re

from django.db import transaction
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.inquiries import api_owner, comment_automation, comment_limits as limits, comment_rules, comments
from apps.inquiries import messenger
from apps.inquiries.models import InquiryCommentRule, InquiryPost
from apps.inquiries.switch import comment_tables_exist, comments_on

PAGE = 30
CURSOR_RE = re.compile(r"^[0-9]{1,18}$")


def rules_site(request):
    """The router's sign-in step: closed unless automation, comments, Messenger and the Meta app values are on."""
    if not comment_automation.platform_on() or not comments_on() or not messenger.configured() \
            or not comment_tables_exist():
        raise api_owner._not_found()
    return api_owner.inquiries_site(request)


router = Router(tags=["inquiries"], auth=rules_site)


class AutomationIn(Schema):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class PreviewIn(Schema):
    model_config = ConfigDict(extra="forbid")
    template: str
    kind: str


class RuleIn(Schema):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    trigger: str = InquiryCommentRule.ANY
    keywords: list[str] = []
    scope: str = InquiryCommentRule.ALL
    post_ids: list[str] = []
    public_text: str = ""
    private_text: str = ""


class RulePatch(Schema):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    trigger: str | None = None
    keywords: list[str] | None = None
    scope: str | None = None
    post_ids: list[str] | None = None
    public_text: str | None = None
    private_text: str | None = None


FIELDS = ("enabled", "trigger", "keywords", "scope", "post_ids", "public_text", "private_text")


def _read(request):
    hit("inq-comment-read", request.account.pk, limit=api_owner.READ_LIMIT, window=api_owner.WINDOW)


def _write(request):
    hit("inq-comment-rule-write", request.account.pk, limit=api_owner.REPLY_LIMIT, window=api_owner.WINDOW)


def _find(site, ref):
    rule = InquiryCommentRule.objects.filter(site=site, ref=ref).first() if comment_rules.REF_RE.match(ref or "") else None
    if rule is None:
        raise api_owner._not_found()
    return rule


@router.get("/sites/{site_id}/inquiries/comment-rules/")
def rule_list(request, site_id: str):
    _read(request)
    site = request.inquiry_site
    rules = list(InquiryCommentRule.objects.filter(site=site).order_by("pk"))
    ids = {i for r in rules for i in r.post_ids}
    posts = {p.external_id: p for p in InquiryPost.objects.filter(site=site, external_id__in=ids)}
    return {"automation": comment_automation.automation_out(site),
            "rules": [comment_rules.rule_out(r, posts) for r in rules]}


@router.put("/sites/{site_id}/inquiries/comment-rules/automation/")
def automation_set(request, site_id: str, payload: AutomationIn):
    _write(request)
    site = request.inquiry_site
    comment_automation.set_enabled(site, payload.enabled)
    return comment_automation.automation_out(site)


@router.get("/sites/{site_id}/inquiries/comment-rules/posts/")
def post_list(request, site_id: str, q: str | None = None, cursor: str | None = None):
    _read(request)
    rows = InquiryPost.objects.filter(site=request.inquiry_site).order_by("-pk")
    if q is not None:
        query = comments.valid_query(q)
        if query is None:
            message = f"Search needs {comments.SEARCH_MIN} to {comments.SEARCH_MAX} characters."
            raise ApiError("invalid_query", message, status=422, fields={"q": [message]})
        rows = rows.filter(caption__icontains=query)
    if cursor:
        if not CURSOR_RE.match(cursor):
            message = "That cursor does not look right."
            raise ApiError("invalid_cursor", message, status=422, fields={"cursor": [message]})
        rows = rows.filter(pk__lt=int(cursor))
    rows = list(rows[: PAGE + 1])
    more, rows = len(rows) > PAGE, rows[:PAGE]
    return {
        "items": [{"id": p.external_id, "is_ad": p.is_ad, "caption": p.caption, "permalink": p.permalink or None}
                  for p in rows],
        "next_cursor": str(rows[-1].pk) if more else None,
    }


@router.post("/sites/{site_id}/inquiries/comment-rules/preview/")
def preview(request, site_id: str, payload: PreviewIn):
    _read(request)
    if payload.kind not in ("public", "private"):
        message = "Kind must be one of: public, private."
        raise ApiError("invalid_kind", message, status=422, fields={"kind": [message]})
    return comment_rules.preview(payload.kind, payload.template)


@router.post("/sites/{site_id}/inquiries/comment-rules/", response={201: dict})
def rule_create(request, site_id: str, payload: RuleIn):
    _write(request)
    site = request.inquiry_site
    values = comment_rules.validate(site, payload.model_dump())
    with transaction.atomic():
        comment_automation.automation_of(site)
        comment_automation._locked(site)  # serialises two creates, so the limit holds
        if InquiryCommentRule.objects.filter(site=site).count() >= limits.MAX_RULES:
            raise ApiError("too_many_rules", f"You can have up to {limits.MAX_RULES} auto-reply rules.", status=409)
        rule = InquiryCommentRule.objects.create(site=site, **values)
    return 201, comment_rules.rule_out(rule)


@router.get("/sites/{site_id}/inquiries/comment-rules/{ref}/")
def rule_detail(request, site_id: str, ref: str):
    _read(request)
    return comment_rules.rule_out(_find(request.inquiry_site, ref))


@router.put("/sites/{site_id}/inquiries/comment-rules/{ref}/")
def rule_update(request, site_id: str, ref: str, payload: RulePatch):
    _write(request)
    site = request.inquiry_site
    rule = _find(site, ref)
    merged = {name: getattr(rule, name) for name in FIELDS}
    merged.update({name: getattr(payload, name) for name in payload.model_fields_set if getattr(payload, name) is not None})
    values = comment_rules.validate(site, merged)
    for name, value in values.items():
        setattr(rule, name, value)
    rule.save()
    comment_automation.cancel_unmatched(rule)
    return comment_rules.rule_out(rule)


@router.delete("/sites/{site_id}/inquiries/comment-rules/{ref}/")
def rule_delete(request, site_id: str, ref: str):
    _write(request)
    rule = _find(request.inquiry_site, ref)
    comment_automation.cancel_rule(rule)
    rule.delete()
    return {"deleted": True}
