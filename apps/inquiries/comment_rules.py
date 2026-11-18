"""Comment auto-reply rules (task z8uq9mg9pj): the spintax language, the link and length checks,
keyword and post matching, and the validation of a rule. Pure functions and the rule's own model;
nothing here calls Meta or reads the webhook.

Spintax: `{a|b|c}` is a group that picks one option, `{name}` is the commenter's first name.
No nesting, a group needs 2 or more distinct options, no option is empty, any other `{...}` is an
error. The name token is replaced by a private-use character while a template is parsed.
"""
import hashlib
import itertools
import random
import re
from dataclasses import dataclass

from apps.common.errors import ApiError
from apps.inquiries import comment_limits as limits
from apps.inquiries.models import InquiryCommentRule, InquiryPost

NAME_TOKEN = "{name}"
NAME_MARK = ""
FALLBACK_NAME = "there"
POST_ID_RE = re.compile(r"^[0-9]{1,30}_[0-9]{1,30}$")
REF_RE = re.compile(r"^cr_[0-9a-f]{24}$")

MESSAGES = {
    "public_required": "Write the public reply.",
    "spintax_variants_required": "Add at least 2 variants, for example {Hi|Hello} {name}.",
    "invalid_spintax": "Check the { } and | in your text: each group needs 2 or more options, with no nesting and no empty option.",
    "link_in_public_reply": "Links cannot go in a public reply. Put the link in the private message.",
    "keywords_required": "Add at least one word to look for.",
    "invalid_keywords": "Keywords are at most 20, each 1 to 40 characters.",
    "posts_required": "Pick at least one post or ad.",
    "unknown_post": "One of those posts is not one of your Page's posts.",
    "invalid_posts": "You can pick up to 50 posts.",
    "invalid_trigger": "Trigger must be one of: any, keywords.",
    "invalid_scope": "Scope must be one of: all, selected.",
}
_TLDS = (
    "com|net|org|ph|io|co|me|info|biz|shop|store|xyz|ly|gl|app|link|site|online|dev|fb|ee|be|tv|us|uk|ai|ws|cc|click|page"
)
_LINK = re.compile(
    rf"(https?\s*:\s*/\s*/|\bwww\s*\.|\b[a-z0-9][a-z0-9-]*\.(?:{_TLDS})\b(?![a-z0-9-]))", re.IGNORECASE,
)


class SpintaxError(ValueError):
    """The template is not valid spintax."""


@dataclass(frozen=True)
class Template:
    parts: tuple  # str literals and tuples of option strings, in order

    @property
    def variant_count(self):
        total = 1
        for part in self.parts:
            if isinstance(part, tuple):
                total *= len(part)
        return total

    @property
    def max_length(self):
        """Length of the longest possible variant, a first name counted as NAME_MAX characters."""
        total = 0
        for part in self.parts:
            options = part if isinstance(part, tuple) else (part,)
            total += max(len(o.replace(NAME_MARK, "x" * limits.NAME_MAX)) for o in options)
        return total


def parse(text):
    """The Template of `text`, or SpintaxError."""
    if not isinstance(text, str):
        raise SpintaxError("not text")
    if NAME_MARK in text:
        raise SpintaxError("reserved character")
    work = text.replace(NAME_TOKEN, NAME_MARK)
    parts, literal, i = [], [], 0
    while i < len(work):
        char = work[i]
        if char == "}":
            raise SpintaxError("unmatched }")
        if char != "{":
            literal.append(char)
            i += 1
            continue
        end = work.find("}", i)
        inner = work[i + 1:end] if end != -1 else ""
        if end == -1 or "{" in inner:
            raise SpintaxError("unclosed or nested group")
        options = []
        for option in inner.split("|"):
            if not option.strip():
                raise SpintaxError("empty option")
            if option not in options:
                options.append(option)
        if "|" not in inner or len(options) < 2:
            raise SpintaxError("a group needs 2 or more options")
        if literal:
            parts.append("".join(literal))
            literal = []
        parts.append(tuple(options))
        i = end + 1
    if literal:
        parts.append("".join(literal))
    return Template(tuple(parts))


def has_link(text):
    """True when the template holds anything link-like, in any of its options."""
    plain = text.replace("{", " ").replace("}", " ").replace("|", " ")
    return bool(_LINK.search(text) or _LINK.search(plain))


def _pick(template, rng):
    return "".join(rng.choice(p) if isinstance(p, tuple) else p for p in template.parts)


def variant_hash(variant):
    return hashlib.sha256(variant.encode()).hexdigest()[:16]


def choose(template, avoid_hash="", rng=random):
    """(variant, hash): a random variant (the name still as its mark) whose hash is not `avoid_hash`
    when the template has another one."""
    variant = _pick(template, rng)
    if variant_hash(variant) != avoid_hash or template.variant_count < 2:
        return variant, variant_hash(variant)
    for _ in range(30):
        variant = _pick(template, rng)
        if variant_hash(variant) != avoid_hash:
            return variant, variant_hash(variant)
    options = [p if isinstance(p, tuple) else (p,) for p in template.parts]
    for combo in itertools.product(*options):
        variant = "".join(combo)
        if variant_hash(variant) != avoid_hash:
            return variant, variant_hash(variant)
    return variant, variant_hash(variant)  # not reached: variant_count >= 2


def fill(variant, name):
    return variant.replace(NAME_MARK, name)


def first_name(author_name):
    """The commenter's first name, at most NAME_MAX characters; FALLBACK_NAME when there is none."""
    words = (author_name or "").split()
    return words[0][: limits.NAME_MAX] if words else FALLBACK_NAME


def samples(template, count=3, name="Maria", rng=random):
    seen, out = set(), []
    for _ in range(count * 6):
        variant = _pick(template, rng)
        if variant not in seen:
            seen.add(variant)
            out.append(fill(variant, name))
        if len(out) == count:
            break
    return out


# -- validation ---------------------------------------------------------------------------------

def _invalid(code, field, message=None):
    message = message or MESSAGES[code]
    return ApiError(code, message, status=422, fields={field: [message]})


def check_template(kind, text):
    """Raise the 422 for a bad template; return its Template. `kind` is "public" or "private"."""
    field = f"{kind}_text"
    text = text or ""
    if kind == "public" and not text.strip():
        raise _invalid("public_required", field)
    try:
        template = parse(text)
    except SpintaxError:
        raise _invalid("invalid_spintax", field) from None
    if kind == "public":
        if template.variant_count < limits.MIN_PUBLIC_VARIANTS:
            raise _invalid("spintax_variants_required", field)
        if has_link(text):
            raise _invalid("link_in_public_reply", field)
        if template.max_length > limits.PUBLIC_MAX:
            raise _invalid("public_too_long", field, f"A public reply is at most {limits.PUBLIC_MAX} characters.")
    elif template.max_length > limits.PRIVATE_MAX:
        raise _invalid("private_too_long", field, f"A private message is at most {limits.PRIVATE_MAX} characters.")
    return template


def preview(kind, text):
    """The preview route's answer: always a dict, never an error."""
    try:
        template = check_template(kind, text)
    except ApiError as error:
        return {"valid": False, "error": {"code": error.code, "message": error.message}, "variant_count": 0, "samples": []}
    return {"valid": True, "error": None, "variant_count": template.variant_count, "samples": samples(template)}


def clean_keywords(value):
    if not isinstance(value, list):
        raise _invalid("invalid_keywords", "keywords")
    out, seen = [], set()
    for item in value:
        if not isinstance(item, str):
            raise _invalid("invalid_keywords", "keywords")
        word = " ".join(item.split())
        if not word:
            continue
        if len(word) > limits.KEYWORD_MAX_CHARS:
            raise _invalid("invalid_keywords", "keywords")
        if word.casefold() not in seen:
            seen.add(word.casefold())
            out.append(word)
    if len(out) > limits.MAX_KEYWORDS:
        raise _invalid("invalid_keywords", "keywords")
    return out


def clean_posts(site, value):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _invalid("unknown_post", "post_ids")
    ids = list(dict.fromkeys(value))
    if len(ids) > limits.MAX_POSTS:
        raise _invalid("invalid_posts", "post_ids")
    if not all(POST_ID_RE.match(v) for v in ids):
        raise _invalid("unknown_post", "post_ids")
    if ids and InquiryPost.objects.filter(site=site, external_id__in=ids).count() != len(ids):
        raise _invalid("unknown_post", "post_ids")
    return ids


def validate(site, merged):
    """The clean field dict of a rule from the merged request values, or a 422."""
    trigger, scope = merged["trigger"], merged["scope"]
    if trigger not in (InquiryCommentRule.ANY, InquiryCommentRule.KEYWORDS):
        raise _invalid("invalid_trigger", "trigger")
    if scope not in (InquiryCommentRule.ALL, InquiryCommentRule.SELECTED):
        raise _invalid("invalid_scope", "scope")
    check_template("public", merged["public_text"])
    private = merged["private_text"] or ""
    if private.strip():
        check_template("private", private)
    else:
        private = ""
    keywords, post_ids = [], []
    if trigger == InquiryCommentRule.KEYWORDS:
        keywords = clean_keywords(merged["keywords"])
        if not keywords:
            raise _invalid("keywords_required", "keywords")
    if scope == InquiryCommentRule.SELECTED:
        post_ids = clean_posts(site, merged["post_ids"])
        if not post_ids:
            raise _invalid("posts_required", "post_ids")
    return {
        "enabled": bool(merged["enabled"]), "trigger": trigger, "keywords": keywords, "scope": scope,
        "post_ids": post_ids, "public_text": merged["public_text"].strip(), "private_text": private.strip(),
    }


# -- matching and output ------------------------------------------------------------------------

def matches(rule, text, post_external_id):
    """True when `rule` answers a comment with this text on this post (the rule's enabled flag aside)."""
    if rule.scope == InquiryCommentRule.SELECTED and post_external_id not in rule.post_ids:
        return False
    if rule.trigger == InquiryCommentRule.KEYWORDS:
        return any(
            re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text or "", re.IGNORECASE) for word in rule.keywords
        )
    return True


def rule_out(rule, posts=None):
    """The contract's rule object. `posts` is a dict of this site's InquiryPost by external id."""
    if posts is None:
        posts = {p.external_id: p for p in InquiryPost.objects.filter(site=rule.site, external_id__in=rule.post_ids)}
    public = parse(rule.public_text)
    private = parse(rule.private_text) if rule.private_text else None
    return {
        "ref": rule.ref, "enabled": rule.enabled, "trigger": rule.trigger, "keywords": list(rule.keywords),
        "scope": rule.scope, "post_ids": list(rule.post_ids),
        "posts": [
            {"id": p.external_id, "is_ad": p.is_ad, "caption": p.caption, "permalink": p.permalink or None}
            for p in (posts.get(i) for i in rule.post_ids) if p is not None
        ],
        "public_text": rule.public_text, "private_text": rule.private_text,
        "public_variants": public.variant_count, "private_variants": private.variant_count if private else 0,
        "created_at": rule.created_at.isoformat(), "updated_at": rule.updated_at.isoformat(),
    }
