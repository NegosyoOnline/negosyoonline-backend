from contextlib import contextmanager
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.operators import totp, two_step
from apps.operators.models import AdminIdentity

TEST_KEY = Fernet.generate_key().decode()
EMAIL = "admin@example.test"
PASSWORD = "a-long-test-only-passphrase"


def run_now(fn, *args):
    fn(*args)


@override_settings(ADMIN_SECRETS_KEY=TEST_KEY)
class TwoStepTestCase(TestCase):
    """Background steps run at once, so their effects can be asserted."""

    def setUp(self):
        cache.clear()
        patcher = mock.patch("apps.operators.background.run_later", side_effect=run_now)
        self.run_later = patcher.start()
        self.addCleanup(patcher.stop)

    def staff(self, username="operator", **extra):
        return get_user_model().objects.create_user(
            username=username, password=PASSWORD, is_staff=True, is_superuser=True, **extra
        )

    def enrolled(self, email=EMAIL, user=None):
        """An enrolled identity. Sets self.secret and self.recovery_codes."""
        identity = AdminIdentity.objects.create(user=user or self.staff(), email=email)
        self.secret = totp.new_secret()
        self.recovery_codes = two_step.new_recovery_codes()
        two_step.store_enrolment(identity, self.secret, self.recovery_codes, timezone.now())
        return identity

    def code(self, offset=0, secret=None):
        """The authenticator code for now, or `offset` steps away."""
        step = totp.current_step(timezone.now()) + offset
        return totp.code_at(secret or self.secret, step)

    def wrong_code(self):
        near = {self.code(o) for o in (-1, 0, 1)}
        return next(c for c in ("000000", "111111", "222222", "333333") if c not in near)

    @contextmanager
    def committed(self):
        with self.captureOnCommitCallbacks(execute=True):
            yield
