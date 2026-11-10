"""Local development only: plays the phone. It polls the local server like the phone app and
prints each text in this terminal instead of sending it, then reports it sent.

    manage.py sms_fake_phone --token TOKEN [--url http://127.0.0.1:8000] [--once]

The token comes from `manage.py sms_device add`. Refuses to run unless DEBUG is on.
"""
import json
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Local development only: polls like the SMS phone and prints each text."

    def add_arguments(self, parser):
        parser.add_argument("--token", required=True, help="the token `sms_device add` printed")
        parser.add_argument("--url", default="http://127.0.0.1:8000", help="the local server")
        parser.add_argument("--once", action="store_true", help="poll once and stop")

    def handle(self, *args, token, url, once, **options):
        if not settings.DEBUG:
            raise CommandError("sms_fake_phone runs only in local development (DEBUG on).")
        self.url, self.token = url.rstrip("/"), token
        self.stdout.write(f"Fake phone polling {self.url}. Nothing is sent. Ctrl-C stops it.")
        while True:
            wait = self._turn()
            if once:
                return
            time.sleep(wait)

    def _turn(self):
        """One poll. Returns the seconds to wait before the next."""
        try:
            answer = self._post("poll", {"app_version": "fake", "battery": 100, "charging": True})
        except urllib.error.HTTPError as error:
            self.stderr.write(f"The server answered {error.code}. Is the gateway on and the token right?")
            return 5
        except OSError as error:
            self.stderr.write(f"Could not reach the server: {error}")
            return 5
        messages = answer.get("messages", [])
        for message in messages:
            self.stdout.write(f"\nTo {message['to']}  (gap {message['gap']}s)\n  {message['text']}")
        if messages:
            self._post("report", {"reports": [{"id": m["id"], "status": "sent"} for m in messages]})
        return answer.get("wait", 5)

    def _post(self, call, body):
        request = urllib.request.Request(
            f"{self.url}/gateway/v1/{call}",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}
