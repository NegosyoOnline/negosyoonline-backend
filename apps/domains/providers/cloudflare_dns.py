"""The Cloudflare DNS adapter (design R4.6, plan r4.3 item 7): zone read, record list, create and
delete, with the platform-zone guard. One account holds platform zones and customer zones, so
the guard is code and runs before any network call:

* with `DOMAINS_PLATFORM_ZONES` empty the adapter refuses everything (fail closed);
* a zone is read only for the name of the site's own `succeeded` registration, never a platform
  zone, another site's name or a name with no succeeded registration for the site;
* the zone the answer names must equal the registrable name and its account id must equal
  `CLOUDFLARE_ACCOUNT_ID`;
* a record is created only as A, CNAME, TXT or MX, named the domain or under it, DNS-only
  (`proxied: false`), with the content given by the caller (from Vercel or Resend);
* a record is deleted only by a recorded id in its recorded zone.
There is no zone creation and no registrar call here.
"""
from dataclasses import dataclass

from django.conf import settings

from apps.domains.models import DomainDnsRecord, DomainRegistration, RegistrationState
from apps.domains.providers.base import Adapter, NotConfigured, Refused, allow

BASE = "https://api.cloudflare.com/client/v4"
RECORD_TYPES = ("A", "CNAME", "TXT", "MX")


@dataclass(frozen=True)
class Zone:
    """A zone the guard has already approved. Only `CloudflareDnsAdapter.zone_for` makes one."""

    id: str
    name: str
    _approved: bool = False


class CloudflareDnsAdapter(Adapter):
    BASE = BASE

    def __init__(self, token=None, account_id=None, transport=None):
        super().__init__(settings.CLOUDFLARE_DNS_API_TOKEN if token is None else token, transport)
        self.account_id = settings.CLOUDFLARE_ACCOUNT_ID if account_id is None else account_id
        zid = r"[0-9a-f]{32}"
        self.ALLOWED = (
            allow("GET", r"/zones"),
            allow("GET", rf"/zones/{zid}/dns_records"),
            allow("POST", rf"/zones/{zid}/dns_records"),
            allow("DELETE", rf"/zones/{zid}/dns_records/[0-9a-f]{{32}}"),
        )

    def _preflight(self):
        if not settings.DOMAINS_PLATFORM_ZONES:
            raise Refused("platform_zones_unset")  # fail closed
        super()._preflight()
        if not self.account_id:
            raise NotConfigured("no_account")

    # --- guards ---------------------------------------------------------------------------

    @staticmethod
    def _guard_name(site, name):
        if not isinstance(name, str) or not name:
            raise Refused("zone_not_allowed")
        for zone in settings.DOMAINS_PLATFORM_ZONES:
            if name == zone or name.endswith("." + zone) or zone.endswith("." + name):
                raise Refused("zone_not_allowed")
        registered = DomainRegistration.objects.filter(
            site=site, name=name, state=RegistrationState.SUCCEEDED
        ).exists()
        if not registered:
            raise Refused("zone_not_allowed")  # another site's name, or none succeeded

    def zone_for(self, site, name):
        """The site's own zone by exact name, or None when the account has no such zone."""
        self._preflight()
        self._guard_name(site, name)
        data = self._call("GET", "/zones", query={"name": name, "account.id": self.account_id})
        for item in data.get("result") or []:
            if (
                isinstance(item, dict) and item.get("name") == name and item.get("id")
                and (item.get("account") or {}).get("id") == self.account_id
            ):
                return Zone(id=item["id"], name=name, _approved=True)
        return None

    def _approved(self, zone):
        if not isinstance(zone, Zone) or not zone._approved:
            raise Refused("zone_not_allowed")

    @staticmethod
    def _under(name, zone_name):
        return name == zone_name or name.endswith("." + zone_name)

    def list_records(self, zone):
        self._approved(zone)
        data = self._call("GET", f"/zones/{zone.id}/dns_records", query={"per_page": 100})
        return [
            {k: r.get(k) for k in ("id", "type", "name", "content")}
            for r in data.get("result") or [] if isinstance(r, dict)
        ]

    def create_record(self, zone, *, type, name, content, priority=None, ttl=1):
        self._approved(zone)
        if type not in RECORD_TYPES or not content or not self._under(name, zone.name):
            raise Refused("record_not_allowed")
        body = {"type": type, "name": name, "content": content, "ttl": ttl, "proxied": False}
        if priority is not None:
            body["priority"] = priority
        data = self._call("POST", f"/zones/{zone.id}/dns_records", body)
        result = data.get("result") or {}
        if not result.get("id"):
            raise Refused("no_record_id")
        return {"id": result["id"], "type": type, "name": name, "content": content}

    def delete_record(self, zone, record_id):
        self._approved(zone)
        recorded = DomainDnsRecord.objects.filter(
            zone_id=zone.id, record_id=record_id, removed_at__isnull=True
        ).exists()
        if not recorded:
            raise Refused("record_not_recorded")
        self._call("DELETE", f"/zones/{zone.id}/dns_records/{record_id}")

    def list_zone_names(self):
        """Every zone name the token can see (the daily audit). Paged; at most 20 pages."""
        self._preflight()
        names = []
        for page in range(1, 21):
            data = self._call("GET", "/zones", query={"account.id": self.account_id, "per_page": 50, "page": page})
            batch = [z.get("name", "") for z in data.get("result") or [] if isinstance(z, dict)]
            names.extend(batch)
            if len(batch) < 50:
                break
        return names
