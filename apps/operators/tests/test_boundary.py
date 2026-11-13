"""Nothing a browser can reach may create or change an identity, a recovery code or a secret."""
import ast
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase


APP = Path(__file__).resolve().parent.parent
# The only modules that may write an identity's secret or recovery codes.
ENROLMENT_WRITERS = {"two_step.py", "admin_identity.py"}
ENROLMENT_NAMES = {"store_enrolment", "clear_enrolment", "AdminRecoveryCode"}


class BoundaryTests(SimpleTestCase):
    def test_the_django_admin_is_not_installed(self):
        self.assertFalse(apps.is_installed("django.contrib.admin"))

    def test_only_the_command_and_the_shared_check_touch_enrolment(self):
        for path in APP.rglob("*.py"):
            if "tests" in path.parts or "migrations" in path.parts or path.name == "models.py":
                continue
            names = {
                getattr(node, "attr", getattr(node, "id", None))
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, (ast.Name, ast.Attribute))
            }
            if path.name not in ENROLMENT_WRITERS:
                self.assertFalse(names & ENROLMENT_NAMES, path.name)

    def test_the_app_imports_no_other_apps_models(self):
        for path in APP.rglob("*.py"):
            if "tests" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("apps.") and node.module.endswith(".models")
                        and not node.module.startswith("apps.operators"),
                        f"{path.name} imports {node.module}",
                    )
