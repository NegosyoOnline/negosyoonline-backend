"""Show a store's email without sending it: `preview_store_email <kind> [--site PUBLIC_ID]
[--minimal] [--text] [--out FILE]`, or `all --out-dir DIR` for every kind with a full and a
bare store. Sample data only unless --site names a store (read only). Sends nothing, stores
nothing, has no web route. Uses a fixed sample code."""
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from apps.members import messaging
from apps.notifications import store_email
from apps.pack import notices as pack_notices

CODE = "123456"
LINK = "https://live4more.negosyotest.com:3000/dashboard/#access=sample"
SAMPLE_SITE = SimpleNamespace(display_name="Live for More", public_id="site_sample")
FULL_BRAND = {
    "name": "Live for More", "color": "#2e7d32", "email": "hello@liveformore.com",
    "mobile": "+639181112222", "address": "12 Mabini Street, Quezon City, Metro Manila",
}
MINIMAL_BRAND = {"name": "Bare Store", "color": "", "email": "", "mobile": "", "address": ""}
WHEN = datetime(2026, 11, 13, 10, 30, tzinfo=dt_timezone.utc)


def _pack(texts, **extra):
    values = {"name": "Live for More", "first": "Maria", "no": "SMP00001", "url": LINK, **extra}
    subject, body = texts
    return subject.format(**values), body.format(**values), ""


def _code(purpose):
    subject, body = messaging.otp_email(SAMPLE_SITE, CODE, purpose)
    return subject, body, CODE


def _notice(**kwargs):
    _, _, subject, body = messaging.notice_texts(SAMPLE_SITE, **kwargs)
    return subject, body, ""


def _registered():
    from apps.pack import registered_notice

    subject, body, cta = registered_notice.texts(SimpleNamespace(full_name="Maria Santos"), SAMPLE_SITE)
    return subject, body, "", cta


KINDS = {  # kind -> (subject, body, code to highlight)
    "member_signin": lambda: _code("signin"),
    "member_email_verify": lambda: _code("email_verify"),
    "member_email_change": lambda: _code("current_proof"),
    "member_contact_changed": lambda: _notice(kind="change", field="email", changed_at=WHEN),
    "member_recovery_notice": lambda: _notice(kind="recovery", hold_end=WHEN + timedelta(days=3)),
    "pack_order_received": lambda: _pack(pack_notices.RECEIVED),
    "pack_proof_rejected": lambda: _pack(pack_notices.REJECTED),
    "pack_verified": lambda: _pack(pack_notices.VERIFIED),
    "pack_access_link": lambda: _pack(pack_notices.ACCESS_EMAIL),
    "pack_registered": _registered,  # the video link shows when PACK_ORDERING_VIDEO_URL is set
}


def render(kind, brand):
    subject, body, code, *cta = KINDS[kind]()
    text, html = store_email.render_store(brand, subject=subject, body=body, code=code, cta=cta[0] if cta else None)
    return subject, text, html


class Command(BaseCommand):
    help = "Render a store email to a file or the screen. Sends nothing."

    def add_arguments(self, parser):
        parser.add_argument("kind", help="One of: " + ", ".join([*KINDS, "all"]))
        parser.add_argument("--site", help="A real store's site id: its own brand (read only).")
        parser.add_argument("--minimal", action="store_true", help="A store with no colour or contact details.")
        parser.add_argument("--text", action="store_true", help="The plain-text part, not HTML.")
        parser.add_argument("--out", help="Write to this file instead of the screen.")
        parser.add_argument("--out-dir", help="With kind 'all': write <kind>.html/.txt (full store) and <kind>-minimal.html/.txt here.")

    def _brand(self, site, minimal):
        if site:
            from apps.sites.models import Site

            found = Site.objects.filter(public_id=site).first()
            if found is None:
                raise CommandError("No such site.")
            return store_email.brand_for(found)
        return MINIMAL_BRAND if minimal else FULL_BRAND

    def handle(self, *args, kind, site, minimal, text, out, out_dir, **options):
        if kind == "all":
            if not out_dir:
                raise CommandError("'all' needs --out-dir.")
            folder = Path(out_dir)
            folder.mkdir(parents=True, exist_ok=True)
            brands = [("", self._brand(site, False))] + ([] if site else [("-minimal", MINIMAL_BRAND)])
            for name in KINDS:
                for suffix, brand in brands:
                    subject, body, html = render(name, brand)
                    (folder / f"{name}{suffix}.html").write_text(html, encoding="utf-8")
                    (folder / f"{name}{suffix}.txt").write_text(f"Subject: {subject}\n\n{body}", encoding="utf-8")
            self.stdout.write(f"Wrote {len(KINDS) * len(brands)} emails to {folder}")
            return
        if kind not in KINDS:
            raise CommandError(f"Unknown kind. Use one of: {', '.join(KINDS)}, all.")
        subject, body, html = render(kind, self._brand(site, minimal))
        content = f"Subject: {subject}\n\n{body}" if text else html
        if out:
            Path(out).write_text(content, encoding="utf-8")
            self.stdout.write(f"Wrote {out}")
        else:
            self.stdout.write(content)
