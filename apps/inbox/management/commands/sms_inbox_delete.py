"""Delete the SMS inbox conversations of one number, the send records of the staff texts to
it, and the contact held under it: how a person's request to delete their texts and their
name is carried out. Works with SMS_INBOX_ENABLED on or off, wherever the tables exist.

    manage.py sms_inbox_delete --actor USERNAME             count only
    manage.py sms_inbox_delete --actor USERNAME --confirm   delete

The number is typed at the prompt. It is never an argument, and it must not be put into the
command line through echo or a here-string either: all of those leave it in the shell's
history. Nothing this command prints, logs or records holds it.
"""
import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.phones import normalise_ph_mobile
from apps.inbox.models import Contact, Conversation, ConversationMessage
from apps.inbox.reading import NUMBER_DIGITS, is_a_number
from apps.inbox.switch import SMS, contacts_tables_exist, tables_exist
from apps.notifications import services
from apps.operators import audit

TARGET = "conversation"
MAX_CONTACT = 32


def _digits(value):
    return "".join(character for character in value if character.isdigit())


def _count(number, one):
    return f"{number} {one}" if number == 1 else f"{number} {one}s"


class Command(BaseCommand):
    help = "Delete the SMS inbox conversations of one number. The number is read from standard input."

    def add_arguments(self, parser):
        parser.add_argument("--actor", help="the staff user running this command, for the audit record")
        parser.add_argument("--confirm", action="store_true", help="delete; without it the command only counts")

    def handle(self, *args, actor, confirm, **options):
        if not tables_exist():
            raise CommandError("The inbox tables do not exist here, so no text is stored.")
        actor = get_user_model().objects.filter(username=actor or "", is_active=True, is_staff=True).first()
        if actor is None:
            raise CommandError("--actor must be an active staff user.")
        self.stderr.write("Type the full number, then Enter:")
        number, digits = self._typed(sys.stdin.readline())
        with transaction.atomic():
            found = self._matching(number, digits)
            messages = ConversationMessage.objects.filter(conversation__in=found).count()
            records = self._staff_records(number)
            contacts = self._contacts(number)
            held = None if contacts is None else contacts.count()
            if confirm:
                Conversation.objects.filter(pk__in=found).delete()  # its messages go with it
                for record in records:
                    record.delete()
                if contacts is not None:
                    contacts.delete()  # its group links go with it; the groups stay
            detail = {
                "platform": SMS, "change": "delete" if confirm else "count",
                "conversations": len(found), "messages": messages, "send_records": len(records),
            }
            if held is not None:
                detail["contacts"] = held
            audit.record(actor, "sms_inbox_delete", target_type=TARGET, detail=detail)
        if confirm:
            for record in records:  # after the commit: a text still waiting is never sent
                services.forget_sms(record)
        if not (found or records or held):
            self.stdout.write("Nothing is stored for that number.")
            return
        counts = (
            f"{_count(len(found), 'conversation')}, {_count(messages, 'message')}, "
            f"{_count(len(records), 'send record')}"
        )
        if held is not None:
            counts += f", {_count(held, 'contact')}"
        if confirm:
            self.stdout.write(f"Deleted: {counts}.")
        else:
            self.stdout.write(f"Found: {counts}. Nothing was deleted. Run again with --confirm to delete them.")

    def _contacts(self, number):
        """The HQ contacts held under this exact Philippine mobile, locked, or None when
        contacts cannot exist here (inbox.0002 not applied). A number that is no Philippine
        mobile holds none."""
        if not contacts_tables_exist():
            return None
        if number is None:
            return Contact.objects.none()
        return Contact.objects.select_for_update().filter(owner_kind="hq", mobile=number)

    def _staff_records(self, number):
        """The send records of staff texts to a Philippine mobile, locked. None exist for any
        other number: a staff text can go to no other. Refuses while one is with the phone,
        before anything is deleted or recorded: the phone's report on it must still find it."""
        if number is None:
            return []
        records = list(services.staff_send_records(number).select_for_update())
        if any(record.status == "dispatched" for record in records):
            raise CommandError("A text to that number is with the phone right now. Run again in 10 minutes.")
        return records

    def _typed(self, typed):
        """What was typed, as a Philippine mobile or else as its digits. The error never
        repeats it."""
        typed = typed.replace("\x00", "").strip()
        try:
            return normalise_ph_mobile(typed), None
        except ValueError:
            pass
        digits = _digits(typed)
        if not NUMBER_DIGITS <= len(digits) <= MAX_CONTACT:
            raise CommandError("Type one full number.")
        return None, digits

    def _matching(self, number, digits):
        """The ids of this number's conversations, locked. A Philippine mobile is stored in
        one form. Any other number is stored as it was received and staff only see it masked,
        so it is matched by its digits, whatever spaces and punctuation either side has."""
        mine = Conversation.objects.select_for_update().filter(platform=SMS, owner_kind=Conversation.Owner.HQ)
        if number is not None:
            return list(mine.filter(contact=number).values_list("pk", flat=True))
        others = mine.filter(can_reply=False).values_list("pk", "contact")
        return [pk for pk, contact in others if is_a_number(contact) and _digits(contact) == digits]
