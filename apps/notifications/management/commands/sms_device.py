"""Add, rotate, disable and list the phones that send texts. This is the only way to do so.

    manage.py sms_device add --name NAME --actor USERNAME
    manage.py sms_device rotate --name NAME --actor USERNAME
    manage.py sms_device disable --name NAME --actor USERNAME
    manage.py sms_device incoming on|off --name NAME --actor USERNAME
    manage.py sms_device list

`add` and `rotate` show the phone's token once. Only its SHA-256 is stored.
"""
import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.inbox.switch import inbox_on
from apps.notifications import devices, sms
from apps.notifications.models import SmsDevice
from apps.operators import audit

logger = logging.getLogger(__name__)

ACTIONS = ("add", "rotate", "disable", "incoming", "list")
TARGET = "sms_device"


class Command(BaseCommand):
    help = "Add, rotate, disable or list the phones of the SMS gateway."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=ACTIONS)
        parser.add_argument("switch", nargs="?", choices=("on", "off"), help="incoming: on or off")
        parser.add_argument("--name", help="the phone's name, for example system")
        parser.add_argument("--actor", help="the staff user running this command, for the audit record")

    def handle(self, *args, action, switch, name, actor, **options):
        if action == "list":
            return self._list()
        if (switch is not None) != (action == "incoming"):
            raise CommandError("Use `incoming on` or `incoming off`; no other action takes on or off.")
        actor = self._staff_user(actor)
        name = self._name(name)
        with transaction.atomic():
            getattr(self, f"_{action}")(name, actor, switch)

    # Checks

    def _staff_user(self, username):
        if not username:
            raise CommandError("--actor is required.")
        found = get_user_model().objects.filter(
            username=username, is_active=True, is_staff=True
        ).first()
        if found is None:
            raise CommandError("--actor must be an active staff user.")
        return found

    def _name(self, raw):
        try:
            return devices.clean_name(raw)
        except devices.DeviceError:
            raise CommandError("--name is required: lower-case letters, digits and hyphens.")

    def _locked(self, name):
        device = SmsDevice.objects.select_for_update().filter(name=name).first()
        if device is None:
            raise CommandError("No such phone. Use `add` first.")
        return device

    def _record(self, actor, action, device, detail=None):
        audit.record(actor, action, target_type=TARGET, target_id=device.pk, detail=detail)
        logger.error("sms-device-change: %s on phone %s by operator %s", action, device.pk, actor.pk)

    def _show_token(self, token):
        self.stdout.write(f"\nToken: {token}")
        self.stdout.write("Enter it in the phone app now. It is shown only this once and is not stored.")

    # Actions. add, rotate and disable are the functions the HQ screens call.

    def _add(self, name, actor, _switch):
        try:
            _device, token = devices.register(actor, name, via="cli", single=False)
        except devices.DeviceError as refusal:
            raise CommandError(refusal.message)
        self.stdout.write("Added. Incoming texts: off.")
        self._show_token(token)

    def _rotate(self, name, actor, _switch):
        self._locked(name)  # keeps the command's own "No such phone" message
        device, token = devices.rotate(actor, name, via="cli", platform_only=False)
        self.stdout.write("Rotated. The old token no longer works.")
        if not device.is_active:
            self.stdout.write("This phone is disabled, so the new token does not work either.")
        self._show_token(token)

    def _disable(self, name, actor, _switch):
        self._locked(name)
        _device, changed = devices.disable(actor, name, via="cli", platform_only=False)
        if not changed:
            self.stdout.write("Already disabled.")
            return
        self.stdout.write("Disabled. The phone is refused from now on.")

    def _incoming(self, name, actor, switch):
        if switch == "on" and not inbox_on():
            raise CommandError("SMS_INBOX_ENABLED is off. Turn it on before a phone forwards texts.")
        device = self._locked(name)
        if switch == "on" and device.site_id is not None:
            raise CommandError("A Site's phone cannot forward incoming texts.")
        device.forwards_incoming = switch == "on"
        device.save(update_fields=["forwards_incoming"])
        self._record(
            actor, "sms_device_incoming", device, {"forwards_incoming": device.forwards_incoming}
        )
        self.stdout.write(f"Incoming texts: {switch}. The phone learns it with its next poll.")
        if device.forwards_incoming:
            self.stdout.write("Texts this phone receives are now stored in the inbox, for 365 days.")

    def _list(self):
        devices = list(SmsDevice.objects.order_by("pk"))
        system = sms.pick_device()
        for device in devices:
            contact = device.last_contact_at.isoformat(timespec="seconds") if device.last_contact_at else "never"
            self.stdout.write(
                f"{device.name}\tactive={device.is_active}"
                f"\tincoming={'on' if device.forwards_incoming else 'off'}"
                f"\tsends={device == system}\tlast_contact={contact}"
                f"\tapp={device.app_version or '-'}"
            )
        if not devices:
            self.stdout.write("No phones.")
