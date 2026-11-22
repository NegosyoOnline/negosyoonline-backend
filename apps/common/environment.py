"""The environment name of the running deployment ("production", "staging", or empty), in one place.

No Django imports: settings.py loads this before Django is configured.
"""


def app_environment(environ):
    """APP_ENVIRONMENT, stripped and lower-cased. For one release, when it is empty the old
    RAILWAY_ENVIRONMENT_NAME variable is read instead (remove that fallback after the variable
    rename in docs/runbooks/bunny-staging.md is done). Empty means a local run."""
    for name in ("APP_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME"):
        value = (environ.get(name) or "").strip().lower()
        if value:
            return value
    return ""
