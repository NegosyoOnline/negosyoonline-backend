"""The Resend domains adapter: five calls, nothing else (design R4.5, R4.14.7, plan r4.3 item 1).

create (the name equals the site's own succeeded registration, a fixed region, sending only), get,
verify, delete by a recorded id, and list. There is no `/emails` call and no API-key call. Every
guard runs before any network call:

* the key, the region and `DOMAINS_PLATFORM_ZONES` must be set (an empty list refuses everything);
* a name must be the name of the site's own `succeeded` registration, never a platform zone;
* a delete takes only an id recorded on a claim, reads the domain first (GET) and refuses unless
  the answer's `name` equals the registration name of the claim's site.

An answer is reduced to whitelisted fields (id, name, status, region and the records' type, name,
value, priority, ttl, status), so no body, key or personal data travels on.
"""
import re

from django.conf import settings

from apps.domains.models import Domain, DomainRegistration, RegistrationState
from apps.domains.providers.base import HOSTNAME, Adapter, NotConfigured, Refused, allow

BASE = "https://api.resend.com"
RECORD_TYPES = ("TXT", "MX", "CNAME")
_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# Sending only: no inbound mail is ever enabled.
CAPABILITIES = {"sending": "enabled", "receiving": "disabled"}


class ResendDomainsAdapter(Adapter):
    BASE = BASE

    def __init__(self, token=None, region=None, transport=None):
        super().__init__(settings.RESEND_DOMAINS_API_KEY if token is None else token, transport)
        self.region = settings.RESEND_REGION if region is None else region
        did = r"[A-Za-z0-9-]{1,64}"
        self.ALLOWED = (
            allow("POST", r"/domains"),
            allow("GET", rf"/domains/{did}"),
            allow("POST", rf"/domains/{did}/verify"),
            allow("DELETE", rf"/domains/{did}"),
            allow("GET", r"/domains"),
        )

    def _preflight(self):
        if not settings.DOMAINS_PLATFORM_ZONES:
            raise Refused("platform_zones_unset")  # fail closed
        super()._preflight()

    # --- guards ---------------------------------------------------------------------------

    @staticmethod
    def _guard_name(site, name):
        if not isinstance(name, str) or not HOSTNAME.fullmatch(name):
            raise Refused("name_not_allowed")
        for zone in settings.DOMAINS_PLATFORM_ZONES:
            if name == zone or name.endswith("." + zone) or zone.endswith("." + name):
                raise Refused("name_not_allowed")
        owns = DomainRegistration.objects.filter(site=site, name=name, state=RegistrationState.SUCCEEDED).exists()
        if not owns:
            raise Refused("name_not_allowed")  # another site's name, or none succeeded

    @staticmethod
    def _id(domain_id):
        if not isinstance(domain_id, str) or not _ID.fullmatch(domain_id):
            raise Refused("bad_id")
        return domain_id

    @staticmethod
    def _reduce(data):
        records = []
        for item in data.get("records") or []:
            if not isinstance(item, dict):
                continue
            records.append({
                "record": str(item.get("record", ""))[:16],
                "name": str(item.get("name", ""))[:253],
                "type": str(item.get("type", ""))[:8],
                "value": str(item.get("value", ""))[:1000],
                "priority": item["priority"] if isinstance(item.get("priority"), int) else None,
                "ttl": str(item.get("ttl", ""))[:16],
                "status": str(item.get("status", ""))[:32],
            })
        return {
            "id": str(data.get("id", ""))[:64], "name": str(data.get("name", ""))[:253],
            "status": str(data.get("status", ""))[:32], "region": str(data.get("region", ""))[:32],
            "records": records,
        }

    # --- the five calls -------------------------------------------------------------------

    def create(self, site, name):
        self._preflight()
        if not self.region:
            raise NotConfigured("no_region")
        self._guard_name(site, name)
        body = {"name": name, "region": self.region, "capabilities": dict(CAPABILITIES)}
        return self._reduce(self._call("POST", "/domains", body))

    def get(self, domain_id):
        return self._reduce(self._call("GET", f"/domains/{self._id(domain_id)}"))

    def verify(self, domain_id):
        self._call("POST", f"/domains/{self._id(domain_id)}/verify")

    def delete(self, site, domain_id, *, name):
        """Only an id recorded on a claim of `site`; the domain is read first and its name must
        equal `name`, which must be the site's own registration name."""
        self._preflight()
        self._id(domain_id)
        self._guard_name(site, name)
        if not Domain.objects.filter(site=site, resend_domain_id=domain_id).exists():
            raise Refused("id_not_recorded")
        found = self.get(domain_id)
        if found["name"] != name:
            raise Refused("name_mismatch")
        self._call("DELETE", f"/domains/{domain_id}")

    def list(self):
        data = self._call("GET", "/domains")
        return [
            {"id": str(d.get("id", ""))[:64], "name": str(d.get("name", ""))[:253],
             "status": str(d.get("status", ""))[:32]}
            for d in data.get("data") or [] if isinstance(d, dict)
        ]
