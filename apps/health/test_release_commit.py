"""RELEASE_COMMIT fallback chain: APP_COMMIT_SHA (Bunny image), RAILWAY_GIT_COMMIT_SHA, "unknown"."""
from django.test import SimpleTestCase

from apps.common.release import release_commit

FULL = "0123456789abcdef0123456789abcdef01234567"


class ReleaseCommitTests(SimpleTestCase):
    def test_railway_only_is_unchanged(self):
        self.assertEqual(release_commit({"RAILWAY_GIT_COMMIT_SHA": FULL}), FULL[:12])

    def test_app_commit_sha_wins_over_railway(self):
        env = {"APP_COMMIT_SHA": FULL, "RAILWAY_GIT_COMMIT_SHA": "f" * 40}
        self.assertEqual(release_commit(env), FULL[:12])

    def test_blank_app_commit_falls_back_to_railway(self):
        env = {"APP_COMMIT_SHA": "  ", "RAILWAY_GIT_COMMIT_SHA": FULL}
        self.assertEqual(release_commit(env), FULL[:12])

    def test_nothing_set_is_unknown(self):
        self.assertEqual(release_commit({}), "unknown")
        self.assertEqual(release_commit({"APP_COMMIT_SHA": ""}), "unknown")
