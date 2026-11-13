"""The branded account emails: one HTML layout with a plain-text part built from the same
variables. Nothing here sends or stores anything; `render` returns the three strings.

Every variable is escaped by the template engine. Bodies and codes are never logged."""
from django.conf import settings
from django.template.loader import render_to_string

SUPPORT_EMAIL = "support@negosyoonline.com"
IGNORE_LINE = "If you did not ask for this, you can ignore this email."

CODE_NOTE = "This code works once and expires in 10 minutes."

# kind -> (subject, heading, message, ignore line). Sentences are the ones the plain-text
# emails always had; only the wrapper is new. "{label}", "{when}" and "{last4}" are filled in.
CODE_KINDS = {
    "signin": (
        "Your NegosyoOnline sign-in code",
        "Your sign-in code",
        "Your sign-in code is below. Enter it to finish signing in.",
        "If you did not ask for this code, you can ignore this email.",
    ),
    "facebook_confirm": (
        "Confirm your email for NegosyoOnline",
        "Confirm your email",
        "Your confirmation code is below. You are seeing this because someone is signing in to "
        "NegosyoOnline with Facebook using this email.",
        "If that was not you, you can ignore this email.",
    ),
    "email_change": (
        "Confirm your new NegosyoOnline email",
        "Confirm your new email",
        "Your confirmation code is below. Enter it to use this email for NegosyoOnline.",
        "If you did not ask to use this email for NegosyoOnline, you can ignore this email.",
    ),
    "email_change_current": (
        "Confirm it is you on NegosyoOnline",
        "Confirm it is you",
        "Your confirmation code is below. Enter it to go on with changing the email of your "
        "NegosyoOnline account.",
        "If you did not ask to change your email, you can ignore this email.",
    ),
}

NOTICE_KINDS = {
    "email_changed_notice": (
        "Your NegosyoOnline email was changed",
        "Your email was changed",
        [
            "The email address of your NegosyoOnline account was just changed, so this address "
            "can no longer be used to sign in. Every other device was signed out.",
            "If you did not make this change, contact {support} right away.",
        ],
    ),
    "connection_notice": (
        "{subject}",
        "{heading}",
        ["{change}", "If you did not make this change, contact {support} right away."],
    ),
    "mobile_notice": (
        "Your NegosyoOnline mobile number changed",
        "Your mobile number changed",
        [
            "The mobile number on your NegosyoOnline account was changed to the number ending "
            "in {last4}. If you did not make this change, sign in and update it."
        ],
    ),
}


def kinds():
    return sorted([*CODE_KINDS, *NOTICE_KINDS])


def _context(subject, heading, preheader, ignore_line=""):
    return {
        "subject": subject,
        "heading": heading,
        "preheader": preheader,
        "ignore_line": ignore_line,
        "support_email": SUPPORT_EMAIL,
        "terms_url": f"{settings.SITE_URL}/terms/",
        "privacy_url": f"{settings.SITE_URL}/privacy/",
    }


def render(kind, **variables):
    """(subject, text, html) for one kind. Code kinds take `code`; "connection_notice" takes
    `subject`, `heading` and `change`; "mobile_notice" takes `last4`."""
    if kind in CODE_KINDS:
        subject, heading, message, ignore_line = CODE_KINDS[kind]
        code = str(variables["code"])
        context = _context(subject, heading, f"Your code is {code}", ignore_line)
        context.update(message=message, code=code, expiry_note=CODE_NOTE)
        text = (
            f"{heading}\n\n{message}\n\nCode: {code}\n\n{CODE_NOTE}\n\n{ignore_line}\n\n"
            f"NegosyoOnline, {SUPPORT_EMAIL}\n"
        )
        return subject, text, render_to_string("notifications/email/code.html", context)
    if kind in NOTICE_KINDS:
        subject, heading, paragraphs = NOTICE_KINDS[kind]
        values = {"support": SUPPORT_EMAIL, "last4": "", "subject": "", "heading": "", "change": "", **variables}
        # str.replace, not format: a value may hold braces.
        def fill(template):
            for name, value in values.items():
                template = template.replace("{" + name + "}", str(value))
            return template
        subject, heading = fill(subject), fill(heading)
        paragraphs = [fill(p) for p in paragraphs]
        context = _context(subject, heading, paragraphs[0][:90])
        context["paragraphs"] = paragraphs
        text = f"{heading}\n\n" + "\n\n".join(paragraphs) + f"\n\nNegosyoOnline, {SUPPORT_EMAIL}\n"
        return subject, text, render_to_string("notifications/email/notice.html", context)
    raise ValueError(f"Unknown email kind {kind!r}.")


def account_mail(kind, legacy_body, **variables):
    """The `body`, `html_body` and `reply_to` arguments of `send_email` for one account email.
    With ACCOUNT_EMAILS_BRANDED off it is the plain text the email always had, with no HTML and
    no Reply-To."""
    if not settings.ACCOUNT_EMAILS_BRANDED:
        return {"body": legacy_body}
    _, text, html = render(kind, **variables)
    return {"body": text, "html_body": html, "reply_to": SUPPORT_EMAIL}
