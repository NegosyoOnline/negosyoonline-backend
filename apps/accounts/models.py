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
