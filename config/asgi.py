"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from config.access_log import install_access_filter  # noqa: E402

# Under uvicorn the request line is logged by `uvicorn.access`, not gunicorn's logger_class:
# mask the Messenger verify token, code and state there too.
install_access_filter()

application = get_asgi_application()
