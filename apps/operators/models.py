"""Operators are Django staff users. Subscribers are Accounts (slice 2), never Users."""
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    pass
