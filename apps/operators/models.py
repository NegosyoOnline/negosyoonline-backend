"""Operators are Django staff users. Subscribers are Accounts, never Users.

The admin identity, its recovery codes, sign-in challenges and hq sessions carry the two-step
sign-in. They are written only by `two_step.py` and the `admin_identity` command, never by a
browser form. `OperatorAuditEntry` is the append-only record of what operators did.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    pass


class AdminIdentity(models.Model):
    """One admin's second step: an authenticator secret (encrypted) and recovery codes."""

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="admin_identities")
    email = models.EmailField(unique=True)  # stored lower-cased
    # Raised on lock, reset, disable and enrol. A session or flag made under an older value is dead.
    credential_version = models.PositiveIntegerField(default=1)
    totp_secret_ciphertext = models.TextField(blank=True)
    totp_last_step = models.BigIntegerField(default=0)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_active=True),
                name="one_active_identity_per_user",
            ),
        ]

    def __str__(self):
        return f"admin identity {self.pk}"


class AdminRecoveryCode(models.Model):
    identity = models.ForeignKey(
        AdminIdentity, on_delete=models.CASCADE, related_name="recovery_codes"
    )
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)


class AdminSignInChallenge(models.Model):
    """One emailed hq sign-in code. Only its HMAC is stored."""

    identity = models.ForeignKey(AdminIdentity, on_delete=models.CASCADE, related_name="challenges")
    code_hmac = models.CharField(max_length=64)
    nonce = models.CharField(max_length=64)  # matches the pre-sign-in cookie
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)


class AdminSession(models.Model):
    """An hq session. The cookie holds a random token; only its SHA-256 is stored."""

    class Stage(models.TextChoices):
        EMAIL_OK = "email_ok"
        VERIFIED = "verified"

    identity = models.ForeignKey(AdminIdentity, on_delete=models.CASCADE, related_name="sessions")
    token_hash = models.CharField(max_length=64, unique=True)
    credential_version = models.PositiveIntegerField()
    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.EMAIL_OK)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)


class OperatorAuditEntry(models.Model):
    """What an operator did. Rows are never changed or deleted.

    Write only through `apps.operators.audit.record`. A personal value may go only in
    `personal`, so that it can be blanked later while the row stays.
    """

    # Null means the system wrote the row.
    operator = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_entries"
    )
    action = models.CharField(max_length=60, db_index=True)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    reason = models.TextField(blank=True)
    detail = models.JSONField(default=dict, blank=True)
    personal = models.JSONField(default=dict, blank=True)
    personal_blanked_at = models.DateTimeField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["target_type", "target_id", "-at"], name="audit_target_recent"),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("An audit entry cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("An audit entry cannot be deleted.")

    def __str__(self):
        return f"{self.action} at {self.at:%Y-%m-%d %H:%M}"
