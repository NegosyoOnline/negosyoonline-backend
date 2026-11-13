"""Show a branded account email without sending it: `preview_account_email <kind> [--text]
[--out FILE]`, or `all --out-dir DIR` for every kind. Uses a fixed sample code."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.notifications import email_render

SAMPLES = {
    "code": "123456",
    "last4": "4567",
    "subject": "Google was connected to your NegosyoOnline account",
    "heading": "Google was connected",
    "change": "A Google account was connected to your NegosyoOnline account on November 13, 2026 "
    "at 10:30 AM PST. It can now be used to sign in.",
}


def sample(kind):
    return email_render.render(kind, **SAMPLES)


class Command(BaseCommand):
    help = "Render an account email to a file or the screen. Sends nothing."

    def add_arguments(self, parser):
        parser.add_argument("kind", help="One of: " + ", ".join([*email_render.kinds(), "all"]))
        parser.add_argument("--text", action="store_true", help="The plain-text part, not HTML.")
        parser.add_argument("--out", help="Write to this file instead of the screen.")
        parser.add_argument("--out-dir", help="With kind 'all': write <kind>.html and <kind>.txt here.")

    def handle(self, *args, kind, text, out, out_dir, **options):
        if kind == "all":
            if not out_dir:
                raise CommandError("'all' needs --out-dir.")
            folder = Path(out_dir)
            folder.mkdir(parents=True, exist_ok=True)
            for name in email_render.kinds():
                subject, body, html = sample(name)
                (folder / f"{name}.html").write_text(html, encoding="utf-8")
                (folder / f"{name}.txt").write_text(f"Subject: {subject}\n\n{body}", encoding="utf-8")
            self.stdout.write(f"Wrote {len(email_render.kinds())} emails to {folder}")
            return
        if kind not in email_render.kinds():
            raise CommandError(f"Unknown kind. Use one of: {', '.join(email_render.kinds())}, all.")
        subject, body, html = sample(kind)
        content = f"Subject: {subject}\n\n{body}" if text else html
        if out:
            Path(out).write_text(content, encoding="utf-8")
            self.stdout.write(f"Wrote {out}")
        else:
            self.stdout.write(content)
