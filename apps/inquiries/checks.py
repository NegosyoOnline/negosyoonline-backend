"""The inquiries switch needs a valid credential encryption key. Website chat needs the
inquiries and storefront switches."""
from django.conf import settings
from django.core.checks import Error, register

from apps.inquiries import crypto


@register(deploy=True)
def check_inquiries_dependencies(app_configs, **kwargs):
    if not settings.INQUIRIES_ENABLED or crypto.key_ready():
        return []
    return [Error(
        "INQUIRIES_ENABLED is on but the credential encryption key is missing or invalid.",
        hint="Set PAYMENT_CREDENTIALS_ENCRYPTION_KEY to a Fernet key on the api service.",
        id="inquiries.E001",
    )]


@register(deploy=True)
def check_webchat_dependencies(app_configs, **kwargs):
    if not settings.INQUIRY_WEBCHAT_ENABLED:
        return []
    missing = [
        name for name in ("INQUIRIES_ENABLED", "STOREFRONT_ENABLED") if not getattr(settings, name)
    ]
    if not missing:
        return []
    return [Error(
        f"INQUIRY_WEBCHAT_ENABLED is on but {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} off.",
        hint="Website chat is reached through the storefront surface and stored by the inquiries app: turn both on, or turn chat off.",
        id="inquiries.E002",
    )]


@register(deploy=True)
def check_contact_inbox_dependencies(app_configs, **kwargs):
    if not settings.INQUIRY_CONTACT_INBOX_ENABLED:
        return []
    missing = [
        name for name in ("INQUIRIES_ENABLED", "STOREFRONT_ENABLED") if not getattr(settings, name)
    ]
    if not missing:
        return []
    return [Error(
        f"INQUIRY_CONTACT_INBOX_ENABLED is on but {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} off.",
        hint="The contact form is reached through the storefront surface and its copy is stored by the inquiries app: turn both on, or turn this off.",
        id="inquiries.E003",
    )]


@register(deploy=True)
def check_messenger_dependencies(app_configs, **kwargs):
    if not settings.INQUIRY_MESSENGER_ENABLED:
        return []
    found = []
    if not settings.INQUIRIES_ENABLED:
        found.append(Error(
            "INQUIRY_MESSENGER_ENABLED is on but INQUIRIES_ENABLED is off.",
            hint="The Messenger inbox is part of the inquiries app: turn both on, or turn Messenger off.",
            id="inquiries.E004",
        ))
    missing = [
        name for name in ("META_MESSENGER_APP_ID", "META_MESSENGER_APP_SECRET", "MESSENGER_VERIFY_TOKEN")
        if not getattr(settings, name)
    ]
    if missing:
        found.append(Error(
            f"INQUIRY_MESSENGER_ENABLED is on but {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set.",
            hint="Set the variable(s) on the api service; the app secret goes straight from the Meta dashboard into the server variables.",
            id="inquiries.E005",
        ))
    return found


@register(deploy=True)
def check_comments_dependencies(app_configs, **kwargs):
    if not settings.INQUIRY_COMMENTS_ENABLED:
        return []
    if settings.INQUIRY_MESSENGER_ENABLED and settings.INQUIRIES_ENABLED:
        return []
    return [Error(
        "INQUIRY_COMMENTS_ENABLED is on but the Messenger inbox is not (INQUIRY_MESSENGER_ENABLED and INQUIRIES_ENABLED).",
        hint="Comments arrive through the Messenger Page connection: turn the Messenger inbox on, or turn comments off.",
        id="inquiries.E006",
    )]


@register(deploy=True)
def check_comment_automation_dependencies(app_configs, **kwargs):
    if not settings.INQUIRY_COMMENT_AUTOMATION_ENABLED:
        return []
    if settings.INQUIRY_COMMENTS_ENABLED and settings.INQUIRY_MESSENGER_ENABLED and settings.INQUIRIES_ENABLED:
        return []
    return [Error(
        "INQUIRY_COMMENT_AUTOMATION_ENABLED is on but comments are not (INQUIRY_COMMENTS_ENABLED, INQUIRY_MESSENGER_ENABLED and INQUIRIES_ENABLED).",
        hint="Auto-replies answer stored comments: turn comments on, or turn auto-replies off.",
        id="inquiries.E007",
    )]
