"""Subscribers. An Account is not a Django user; operators are (apps.operators)."""
import secrets

from django.db import models

from apps.common.ids import ALPHABET, new_public_id


def new_account_id():
    return new_public_id("acc")


def new_session_id():
    return new_public_id("ses")


def new_referral_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


class Account(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_account_id, editable=False)
    email = models.EmailField(unique=True)  # always stored lowercase; see services.normalise_email
    name = models.CharField(max_length=120, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    referral_code = models.CharField(
        max_length=12, unique=True, default=new_referral_code, editable=False
    )
    referred_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="referrals"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.email


class Session(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_session_id, editable=False)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="sessions")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField()
    device_label = models.CharField(max_length=80, blank=True)
    masked_ip = models.CharField(max_length=45, blank=True)

    def __str__(self):
        return f"{self.account} on {self.device_label or 'unknown device'}"


class PolicyAcceptance(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="policy_acceptances")
    policy_version = models.CharField(max_length=20)
    policies = models.JSONField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    accepted_at = models.DateTimeField(auto_now_add=True)


class AccountAuditEvent(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="audit_events")
    event = models.CharField(max_length=40)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class ExternalIdentity(models.Model):
    """A Google or Facebook user linked to an account. No provider token is ever stored."""

    class Provider(models.TextChoices):
        GOOGLE = "google"
        FACEBOOK = "facebook"

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="identities")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    provider_user_id = models.CharField(max_length=255)
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_user_id"], name="unique_provider_user"
            ),
            models.UniqueConstraint(
                fields=["account", "provider"], name="one_identity_per_provider"
            ),
        ]

    def __str__(self):
        return f"{self.provider} for {self.account}"


class AccountMobile(models.Model):
    """An account's verified mobile number. A row exists only for a verified number. Two
    accounts may hold the same number."""

    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="mobile")
    number = models.CharField(max_length=13)  # +639XXXXXXXXX
    verified_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "account mobile"

    def __str__(self):  # never the number
        return f"Mobile of account {self.account_id}"


class MobileOtpChallenge(models.Model):
    """One request to verify a number. The code is never stored: only a keyed hash of account,
    number and code. Deleted 24 hours after it expires."""

    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="mobile_challenges"
    )
    number = models.CharField(max_length=13)  # the number being verified
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    sms_public_id = models.CharField(max_length=40, blank=True)  # the send record's id
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "mobile verification challenge"

    def __str__(self):  # never the number
        return f"Mobile challenge {self.pk} of account {self.account_id}"
