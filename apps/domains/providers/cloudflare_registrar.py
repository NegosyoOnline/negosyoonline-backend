"""The Cloudflare Registrar adapter: four calls, one account (design R4.4, plan r4.3 item 1).

* `check`: `POST .../registrar/domain-check` (read, no money), up to 20 names.
* `register`: `POST .../registrar/registrations`, the only call that spends money. The body is
  exactly `{"domain_name": <name>}`, plus `"auto_renew": true` only while
  `DOMAINS_REGISTER_AUTO_RENEW` is on (step 0 decides). It refuses unless the name is the one of a
  registration row that purchase step 1 already marked `sent` (so it cannot be called for any other
  name), refuses inside a web request, and needs `CLOUDFLARE_REGISTRAR_API_TOKEN`, which only the
  worker holds. It is called from `apps.domains.purchase` and nowhere else (a test pins that).
* `status`: `GET .../registrations/{name}/registration-status`.
* `get`: `GET .../registrations/{name}`.

`status` and `get` are for the names of a `DomainRegistration` row only. There is no renewal, no
transfer, no contact change and no list call. The account id is pinned from settings. Answers are
reduced to whitelisted fields: no raw body is ever returned or stored.
"""
import re
from datetime import datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from django.conf import settings
from django.utils.dateparse import parse_datetime

from apps.domains import context
from apps.domains.models import DomainRegistration, RegistrationState
from apps.domains.providers.base import HOSTNAME, Adapter, NotConfigured, ProviderError, Refused, allow

BASE = "https://api.cloudflare.com/client/v4"
CHECK_MAX_NAMES = 20
STATUSES = ("in_progress", "succeeded", "failed", "action_required", "blocked")
_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


def cents(value):
    """Dollars (a string or number) as whole cents, rounded up (the conservative side for a
    ceiling), or None when it is not a plain non-negative amount."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return int((amount * 100).to_integral_value(rounding=ROUND_CEILING))


def _result(data):
    result = data.get("result")
    return result if isinstance(result, dict) else data


class RegistrarAdapter(Adapter):
    BASE = BASE

    def __init__(self, token=None, account_id=None, transport=None):
        super().__init__(settings.CLOUDFLARE_REGISTRAR_API_TOKEN if token is None else token, transport)
        self.account_id = settings.CLOUDFLARE_ACCOUNT_ID if account_id is None else account_id
        acct = re.escape(self.account_id) if self.account_id else r"\0"
        host = r"[a-z0-9.-]{1,253}"
        self.ALLOWED = (
            allow("POST", rf"/accounts/{acct}/registrar/domain-check"),
            allow("POST", rf"/accounts/{acct}/registrar/registrations"),
            allow("GET", rf"/accounts/{acct}/registrar/registrations/{host}/registration-status"),
            allow("GET", rf"/accounts/{acct}/registrar/registrations/{host}"),
        )

    def _preflight(self):
        super()._preflight()
        if not self.account_id:
            raise NotConfigured("no_account")

    @property
    def _root(self):
        return f"/accounts/{self.account_id}/registrar"

    @staticmethod
    def _known_name(name):
        """Only the name of a registration row of ours may be read or registered."""
        if not isinstance(name, str) or not HOSTNAME.fullmatch(name):
            raise Refused("name_not_allowed")
        if not DomainRegistration.objects.filter(name=name).exists():
            raise Refused("name_not_allowed")
        return name

    # --- reads that move no money ----------------------------------------------------------

    def check(self, names):
        """One Check call. Returns [{"name", "registrable", "tier", "registration_cost_cents",
        "renewal_cost_cents", "currency"}] for the names the provider answered."""
        if not names or len(names) > CHECK_MAX_NAMES:
            raise Refused("name_not_allowed")
        for name in names:
            if not isinstance(name, str) or not HOSTNAME.fullmatch(name):
                raise Refused("name_not_allowed")
        data = self._call("POST", f"{self._root}/domain-check", {"domains": list(names)})
        result = _result(data)
        rows = result.get("domains") if isinstance(result.get("domains"), list) else data.get("result")
        out = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
            out.append({
                "name": str(item.get("name") or item.get("domain_name") or "").lower(),
                "registrable": item.get("registrable") is True,
                "tier": str(item.get("tier") or "")[:16].lower(),
                "registration_cost_cents": cents(pricing.get("registration_cost")),
                "renewal_cost_cents": cents(pricing.get("renewal_cost")),
                "currency": str(pricing.get("currency") or "")[:3].upper(),
            })
        return out

    def status(self, name):
        """{"state": one of STATUSES or "unknown", "code": whitelisted text or ""}."""
        self._known_name(name)
        result = _result(self._call("GET", f"{self._root}/registrations/{name}/registration-status"))
        state = str(result.get("status") or result.get("state") or "").lower()
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        code = str(result.get("reason") or error.get("code") or "").lower()
        return {"state": state if state in STATUSES else "unknown", "code": code if _CODE.fullmatch(code) else ""}

    def get(self, name):
        """The registration in our account by exact name, or None (404). Whitelisted fields:
        {"name", "expires_at", "auto_renew"}. An answer naming another account is refused."""
        self._known_name(name)
        try:
            data = self._call("GET", f"{self._root}/registrations/{name}")
        except ProviderError as error:
            if error.code == "not_found":
                return None
            raise
        result = _result(data)
        account = result.get("account_id")
        if not account and isinstance(result.get("account"), dict):
            account = result["account"].get("id")
        if account and account != self.account_id:
            raise Refused("other_account")
        expires = result.get("expires_at")
        expires = parse_datetime(expires) if isinstance(expires, str) else None
        if isinstance(expires, datetime) and expires.tzinfo is None:
            expires = None
        auto = result.get("auto_renew")
        return {
            "name": str(result.get("domain_name") or result.get("name") or "").lower(),
            "expires_at": expires,
            "auto_renew": auto if isinstance(auto, bool) else None,
        }

    # --- the one call that spends money -----------------------------------------------------

    def register(self, name):
        """POST once. Returns {"state": "succeeded" | "in_progress" | a failure status}. A refusal
        of our own guard raises `Refused` before any network call; a provider failure raises
        `ProviderError` (retryable: outcome unknown)."""
        if context.in_request():
            raise Refused("in_request")
        self._preflight()
        if not isinstance(name, str) or not HOSTNAME.fullmatch(name):
            raise Refused("name_not_allowed")
        sent = DomainRegistration.objects.filter(
            name=name, state=RegistrationState.SENT, send_count=1, register_sent_at__isnull=False
        ).exists()
        if not sent:
            raise Refused("not_a_sent_registration")
        body = {"domain_name": name}
        if settings.DOMAINS_REGISTER_AUTO_RENEW:
            body["auto_renew"] = True
        status, data = self._call_status("POST", f"{self._root}/registrations", body)
        state = str(_result(data).get("status") or "").lower()
        if state not in STATUSES:
            state = "succeeded" if status == 201 else "in_progress"
        return {"state": state}
