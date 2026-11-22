"""RELEASE_COMMIT: APP_COMMIT_SHA (baked into the Bunny image), else "unknown"."""
from django.test import SimpleTestCase

from apps.common.release import release_commit

FULL = "0123456789abcdef0123456789abcdef01234567"


class ReleaseCommitTests(SimpleTestCase):
    def test_app_commit_sha_is_cut_to_twelve(self):
        self.assertEqual(release_commit({"APP_COMMIT_SHA": f" {FULL} "}), FULL[:12])

    def test_the_old_platform_variable_is_ignored(self):
        self.assertEqual(release_commit({"RAILWAY_GIT_COMMIT_SHA": FULL}), "unknown")
        env = {"APP_COMMIT_SHA": "  ", "RAILWAY_GIT_COMMIT_SHA": FULL}
        self.assertEqual(release_commit(env), "unknown")

    def test_nothing_set_is_unknown(self):
        self.assertEqual(release_commit({}), "unknown")
        self.assertEqual(release_commit({"APP_COMMIT_SHA": ""}), "unknown")
