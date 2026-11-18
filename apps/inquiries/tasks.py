"""Inquiry background work: a Messenger customer's name and photo (apps/inquiries/profile.py)."""
from celery import shared_task

from apps.inquiries import comment_media, profile


@shared_task(name="inquiries.fetch_messenger_profile", ignore_result=True)
def fetch_messenger_profile(conversation_pk):
    """One Graph read for one conversation. The argument is a table id, never a customer id."""
    profile.fetch(conversation_pk)


@shared_task(name="inquiries.fetch_comment_avatar", ignore_result=True)
def fetch_comment_avatar(comment_pk):
    """One Graph read for one comment's author. The argument is a table id, never a customer id."""
    comment_media.fetch_avatar(comment_pk)


@shared_task(name="inquiries.fetch_post_text", ignore_result=True)
def fetch_post_text(post_pk):
    """One Graph read for one post's text and link."""
    comment_media.fetch_post(post_pk)
