"""Whether the current thread is inside a web request (design R4.14.9).

The register call is never made from a web request: `request_started` sets a thread flag and
`request_finished` clears it. A Celery task that runs eagerly inside a request (no `REDIS_URL`)
therefore still counts as inside the request. The signals are connected by `DomainsConfig.ready`.
"""
import threading

from django.core.signals import request_finished, request_started

_state = threading.local()


def in_request():
    return getattr(_state, "active", False)


def _started(sender, **kwargs):
    _state.active = True


def _finished(sender, **kwargs):
    _state.active = False


def connect():
    request_started.connect(_started, dispatch_uid="domains-request-started")
    request_finished.connect(_finished, dispatch_uid="domains-request-finished")
