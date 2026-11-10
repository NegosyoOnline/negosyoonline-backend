from django.test import SimpleTestCase

from apps.operators import background


class RunLaterTests(SimpleTestCase):
    def test_the_work_runs_off_the_calling_thread(self):
        seen = []
        thread = background.run_later(seen.append, "done")
        thread.join(5)
        self.assertEqual(seen, ["done"])

    def test_a_failure_is_logged_and_never_raised(self):
        def broken():
            raise RuntimeError("smtp down")

        with self.assertLogs("apps.operators.background", "ERROR") as logs:
            background.run_later(broken).join(5)
        self.assertIn("Background step failed (broken)", logs.output[0])
