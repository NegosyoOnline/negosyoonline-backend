import ast
from pathlib import Path

from django.utils import timezone

from apps.operators import audit
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.base import TwoStepTestCase

APP = Path(__file__).resolve().parent.parent
# Other apps that write operator audit rows through audit.record().
CALLERS = (
    APP, APP.parent / "notifications", APP.parent / "common", APP.parent / "accounts",
    APP.parent / "billing", APP.parent / "sante",
)


class AuditTests(TwoStepTestCase):
    def test_record_writes_the_row(self):
        operator = self.staff()
        entry = audit.record(
            operator, "admin_identity_add", target_type="admin_identity", target_id=7,
            reason="first admin", detail={"surface": "hq"},
        )
        entry.refresh_from_db()
        self.assertEqual(entry.operator, operator)
        self.assertEqual((entry.target_type, entry.target_id), ("admin_identity", "7"))
        self.assertEqual(entry.reason, "first admin")
        self.assertEqual(entry.detail, {"surface": "hq"})
        self.assertEqual(entry.personal, {})
        self.assertIsNone(entry.personal_blanked_at)
        self.assertIsNotNone(entry.at)

    def test_a_system_row_has_no_operator(self):
        self.assertIsNone(audit.record(None, "admin_locked").operator)

    def test_an_undeclared_action_is_refused(self):
        with self.assertRaises(ValueError):
            audit.record(None, "something_new")
        self.assertEqual(OperatorAuditEntry.objects.count(), 0)

    def test_every_action_has_a_class(self):
        self.assertTrue(set(audit.ACTIONS.values()) <= {audit.PERSONAL, audit.KEPT})
        for action in audit.SIGN_IN_ACTIONS:
            self.assertEqual(audit.ACTIONS[action], audit.PERSONAL)

    def test_every_action_named_in_the_code_is_a_declared_literal(self):
        """A call to record() or _audit() or _record() names its action as a plain string."""
        positions = {"record": 1, "_audit": 1, "_record": 1}
        seen = set()
        for path in (path for folder in CALLERS for path in folder.rglob("*.py")):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", getattr(node.func, "id", None))
                if name not in positions or len(node.args) <= positions[name]:
                    continue
                action = node.args[positions[name]]
                if isinstance(action, ast.Name) and action.id == "action":
                    continue  # the pass-through inside a helper
                self.assertIsInstance(action, ast.Constant, f"{path.name}:{node.lineno}")
                self.assertIn(action.value, audit.ACTIONS, f"{path.name}:{node.lineno}")
                seen.add(action.value)
        self.assertEqual(seen, set(audit.ACTIONS))

    def test_an_entry_cannot_be_changed_or_deleted(self):
        entry = audit.record(None, "admin_locked")
        entry.reason = "edited"
        with self.assertRaises(RuntimeError):
            entry.save()
        with self.assertRaises(RuntimeError):
            entry.delete()

    def test_personal_values_can_be_blanked_while_the_row_stays(self):
        """What a retention job does after a year: one update, no schema change."""
        entry = audit.record(None, "admin_sign_in_ok", personal={"email": "ana@example.test"})
        OperatorAuditEntry.objects.filter(pk=entry.pk).update(
            personal={}, personal_blanked_at=timezone.now()
        )
        entry.refresh_from_db()
        self.assertEqual(entry.personal, {})
        self.assertIsNotNone(entry.personal_blanked_at)
        self.assertEqual(entry.action, "admin_sign_in_ok")

    def test_a_personal_value_on_a_kept_action_is_refused(self):
        """A kept row is never blanked, so it must never hold a personal value."""
        with self.assertRaises(ValueError):
            audit.record(None, "admin_identity_add", personal={"email": "ana@example.test"})
        self.assertEqual(OperatorAuditEntry.objects.count(), 0)
        audit.record(None, "admin_identity_add", personal={})  # an empty value is not one

    def test_recent_sign_in_events_are_the_newest_ten_of_one_identity(self):
        mine = self.enrolled()
        other = self.enrolled("other@example.test", self.staff("other"))
        for identity, count in ((mine, 12), (other, 3)):
            for _ in range(count):
                audit.record(
                    identity.user, "admin_sign_in_ok",
                    target_type="admin_identity", target_id=identity.pk,
                )
        audit.record(mine.user, "admin_identity_reset", target_type="admin_identity", target_id=mine.pk)
        events = audit.recent_sign_in_events(mine)
        self.assertEqual(len(events), 10)
        self.assertTrue(all(e.target_id == str(mine.pk) and e.action == "admin_sign_in_ok" for e in events))
        self.assertEqual([e.pk for e in events], sorted((e.pk for e in events), reverse=True))
