# Loads the task queue with Django, so a task defined with shared_task uses this app.
from .celery import app as celery_app

__all__ = ("celery_app",)
