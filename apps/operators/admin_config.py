"""Its own module: an app's apps.py may hold only one default AppConfig."""
from django.contrib.admin.apps import AdminConfig


class HqAdminConfig(AdminConfig):
    default_site = "apps.operators.admin_site.HqAdminSite"
