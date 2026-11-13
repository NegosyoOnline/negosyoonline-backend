import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

SCRIPTS = Path(settings.BASE_DIR) / "scripts"
NAMES = ("start_worker.sh", "start_beat.sh")


class CeleryStartScriptTests(SimpleTestCase):
    def read(self, name):
        return (SCRIPTS / name).read_text()

    def test_scripts_exist_and_are_executable(self):
        for name in NAMES:
            path = SCRIPTS / name
            self.assertTrue(path.is_file(), name)
            self.assertTrue(os.access(path, os.X_OK), name)

    def test_scripts_pass_sh_syntax_check(self):
        for name in NAMES:
            result = subprocess.run(
                ["sh", "-n", str(SCRIPTS / name)], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_scripts_drop_to_nobody_with_a_fallback_without_it(self):
        for name in NAMES:
            text = self.read(name)
            exec_lines = [line for line in text.splitlines() if "exec uv run" in line]
            self.assertEqual(len(exec_lines), 2, name)
            with_drop = [line for line in exec_lines if "--uid=nobody --gid=nogroup" in line]
            without_drop = [line for line in exec_lines if "--uid" not in line]
            self.assertEqual(len(with_drop), 1, name)
            self.assertEqual(len(without_drop), 1, name)

    def test_beat_keeps_its_schedule_in_tmp(self):
        for line in self.read("start_beat.sh").splitlines():
            if "exec uv run" in line:
                self.assertIn("--schedule=/tmp/celerybeat-schedule", line)
