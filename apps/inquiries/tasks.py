"""Inquiry background work: a Messenger customer's name and photo (apps/inquiries/profile.py)."""
from celery import shared_task

from apps.inquiries import profile


@shared_task(name="inquiries.fetch_messenger_profile", ignore_result=True)
def fetch_messenger_profile(conversation_pk):
    """One Graph read for one conversation. The argument is a table id, never a customer id."""
    profile.fetch(conversation_pk)
