"""The Vercel adapter: six calls on one project, the project id pinned (design R4.6).

add, get, verify, config read, remove and list. The project id comes from settings and is never
a parameter. Every `name` must be one of the hostnames the caller says belong to the claim
(`allowed`), which the caller builds from the site's own succeeded registration. `redirect` is
accepted only for the apex to `www.<apex>` with 308. The token is a project-scoped token; if
Vercel refuses the config read the records come from the project-domain answer.
"""
from django.conf import settings

from apps.domains.providers.base import HOSTNAME, Adapter, NotConfigured, Refused, allow

BASE = "https://api.vercel.com"


class VercelAdapter(Adapter):
    BASE = BASE

    def __init__(self, token=None, project_id=None, transport=None):
        super().__init__(settings.VERCEL_API_TOKEN if token is None else token, transport)
        self.project_id = settings.VERCEL_STOREFRONTS_PROJECT_ID if project_id is None else project_id
        pid = r"[A-Za-z0-9_]{1,64}"
        host = r"[a-z0-9.-]{1,253}"
        self.ALLOWED = (
            allow("POST", rf"/v10/projects/{pid}/domains"),
            allow("GET", rf"/v9/projects/{pid}/domains/{host}"),
            allow("POST", rf"/v9/projects/{pid}/domains/{host}/verify"),
            allow("GET", rf"/v6/domains/{host}/config"),
            allow("DELETE", rf"/v9/projects/{pid}/domains/{host}"),
            allow("GET", rf"/v9/projects/{pid}/domains"),
        )

    def _preflight(self):
        super()._preflight()
        if not self.project_id:
            raise NotConfigured("no_project")

    def _pinned(self, name, allowed):
        if not isinstance(name, str) or not HOSTNAME.fullmatch(name) or name not in allowed:
            raise Refused("name_not_allowed")
        return name

    @staticmethod
    def _domain(data):
        return {
            "name": data.get("name", ""),
            "verified": bool(data.get("verified")),
            "verification": [
                {k: item.get(k, "") for k in ("type", "domain", "value")}
                for item in data.get("verification") or [] if isinstance(item, dict)
            ],
            "redirect": data.get("redirect") or "",
        }

    def add(self, name, *, allowed, redirect_to=None):
        self._preflight()
        self._pinned(name, allowed)
        body = {"name": name}
        if redirect_to is not None:
            if name.startswith("www.") or redirect_to != f"www.{name}":
                raise Refused("redirect_not_allowed")
            body.update(redirect=redirect_to, redirectStatusCode=308)
        return self._domain(self._call("POST", f"/v10/projects/{self.project_id}/domains", body))

    def get(self, name, *, allowed):
        self._pinned(name, allowed)
        return self._domain(self._call("GET", f"/v9/projects/{self.project_id}/domains/{name}"))

    def verify(self, name, *, allowed):
        self._pinned(name, allowed)
        return self._domain(self._call("POST", f"/v9/projects/{self.project_id}/domains/{name}/verify"))

    def config(self, name, *, allowed):
        """The records Vercel wants for `name`, read from its answer, never hard-coded:
        {"misconfigured": bool, "records": [{"type", "content"}]}."""
        self._pinned(name, allowed)
        data = self._call("GET", f"/v6/domains/{name}/config")
        records = []
        for entry in data.get("recommendedCNAME") or []:
            if isinstance(entry, dict) and entry.get("rank") in (1, None) and entry.get("value"):
                records.append({"type": "CNAME", "content": str(entry["value"]).rstrip(".")})
                break
        for entry in data.get("recommendedIPv4") or []:
            if isinstance(entry, dict) and entry.get("rank") in (1, None):
                values = entry.get("value") or []
                if values:
                    records.append({"type": "A", "content": str(values[0])})
                    break
        return {"misconfigured": bool(data.get("misconfigured")), "records": records}

    def remove(self, name, *, allowed):
        self._pinned(name, allowed)
        self._call("DELETE", f"/v9/projects/{self.project_id}/domains/{name}")

    def list(self):
        data = self._call("GET", f"/v9/projects/{self.project_id}/domains")
        return [d.get("name", "") for d in data.get("domains") or [] if isinstance(d, dict)]
