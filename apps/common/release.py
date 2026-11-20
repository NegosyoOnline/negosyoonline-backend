"""The running release's short commit, in one place (settings RELEASE_COMMIT and Sentry's release).

No Django imports: settings.py loads this before Django is configured.
"""


def release_commit(environ):
    """First 12 characters of APP_COMMIT_SHA (baked into the Bunny image at build time), else
    RAILWAY_GIT_COMMIT_SHA, else "unknown". Blank values are skipped. Railway never sets
    APP_COMMIT_SHA, so its behaviour is unchanged."""
    for name in ("APP_COMMIT_SHA", "RAILWAY_GIT_COMMIT_SHA"):
        value = (environ.get(name) or "").strip()
        if value:
            return value[:12]
    return "unknown"
