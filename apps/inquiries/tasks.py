"""Inquiry background work: a Messenger customer's name and photo (apps/inquiries/profile.py)."""
from celery import shared_task

from apps.inquiries import comment_media, profile


@shared_task(name="inquiries.fetch_messenger_profile", ignore_result=True)
def fetch_messenger_profile(conversation_pk):
    """One Graph read for one conversation. The argument is a table id, never a customer id."""
    profile.fetch(conversation_pk)


@shared_task(name="inquiries.evaluate_comment_rules", ignore_result=True)
def evaluate_comment_rules(comment_pk):
    """Match one stored comment against the site's auto-reply rules and queue the replies. The
    argument is a table id, never a customer id or a text."""
    from apps.inquiries import comment_automation
    comment_automation.evaluate(comment_pk)


@shared_task(name="inquiries.send_comment_auto_reply", ignore_result=True)
def send_comment_auto_reply(action_pk):
    """Send one queued automatic reply (after its random delay). It re-checks the switches, the pause
    and the rule first. The argument is a table id."""
    from apps.inquiries import comment_automation
    comment_automation.send(action_pk)


@shared_task(name="inquiries.fetch_comment_avatar", ignore_result=True)
def fetch_comment_avatar(comment_pk):
    """One Graph read for one comment's author. The argument is a table id, never a customer id."""
    comment_media.fetch_avatar(comment_pk)


@shared_task(name="inquiries.fetch_post_text", ignore_result=True)
def fetch_post_text(post_pk):
    """One Graph read for one post's text and link."""
    comment_media.fetch_post(post_pk)
