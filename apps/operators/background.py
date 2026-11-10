"""Work that must not delay or shape a response, such as sending a sign-in code.

There is no task queue yet (the worker arrives with the billing slice), so the work runs in a
short thread of the web process. This function is the one place that knows; a queue replaces it
here. If the process restarts at that moment the work is lost, and the admin asks again.
"""
import logging
import threading

from django.db import connections

logger = logging.getLogger(__name__)


def run_later(fn, *args):
    def work():
        try:
            fn(*args)
        except Exception:
            logger.exception("Background step failed (%s)", getattr(fn, "__name__", "step"))
        finally:
            connections.close_all()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread
