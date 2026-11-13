"""Error tracking, off until a DSN exists. Errors only: no traces, no personal data.

`init()` does nothing without `SENTRY_DSN`. It is called from settings, so the API and the
Celery worker (which loads the same settings) both report.
"""
import os
import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PH_MOBILE = re.compile(r"(?<!\w)(?:\+?63|0)9\d{9}(?!\d)")
MASK = "[masked]"


def mask_text(value):
    return _PH_MOBILE.sub(MASK, _EMAIL.sub(MASK, value))


def _mask_deep(value):
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, list):
        return [_mask_deep(item) for item in value]
    if isinstance(value, dict):
        return {key: _mask_deep(item) for key, item in value.items()}
    return value


def _drop_frame_vars(event):
    """Remove every stack frame's local variables: they can hold codes, tokens and names."""
    for container in (event.get("exception"), event.get("threads")):
        for entry in (container or {}).get("values", []):
            stack = entry.get("stacktrace") or {}
            for frame in stack.get("frames", []):
                frame.pop("vars", None)


def before_send(event, hint):
    """Strip the request's body, cookies, query string and headers, and the user; mask any
    email or Philippine mobile number left in messages, exception values and breadcrumbs."""
    event.pop("user", None)
    event.pop("server_name", None)
    request = event.get("request")
    if isinstance(request, dict):
        kept = {}
        if "method" in request:
            kept["method"] = request["method"]
        if request.get("url"):
            kept["url"] = mask_text(request["url"].split("?")[0].split("#")[0])
        event["request"] = kept
    _drop_frame_vars(event)
    job = (event.get("contexts") or {}).get("celery-job")
    if isinstance(job, dict):  # keep the task name only, never its arguments
        job.pop("args", None)
        job.pop("kwargs", None)
    for key in ("message", "logentry", "exception", "breadcrumbs", "extra", "contexts"):
        if key in event:
            event[key] = _mask_deep(event[key])
    return event


def init(dsn=None):
    """Start the SDK when a DSN is set. Returns whether it started."""
    dsn = (dsn if dsn is not None else os.environ.get("SENTRY_DSN", "")).strip()
    if not dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        include_local_variables=False,
        traces_sample_rate=0,
        before_send=before_send,
        release=os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown")[:12],
        environment=os.environ.get("SENTRY_ENVIRONMENT", "") or None,
        integrations=[DjangoIntegration(), CeleryIntegration()],
    )
    return True
