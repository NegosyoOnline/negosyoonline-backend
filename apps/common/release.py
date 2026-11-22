"""The running release's short commit, in one place (settings RELEASE_COMMIT and Sentry's release).

No Django imports: settings.py loads this before Django is configured.
"""


def release_commit(environ):
    """First 12 characters of APP_COMMIT_SHA (baked into the Bunny image at build time), else
    "unknown". A blank value counts as unset."""
    value = (environ.get("APP_COMMIT_SHA") or "").strip()
    return value[:12] if value else "unknown"
