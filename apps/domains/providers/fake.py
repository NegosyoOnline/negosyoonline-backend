"""Fakes for the provider APIs: in-memory transports that answer like the REST APIs, so the real
adapters (with every guard) run on top of them. No network, no token value that means anything.

`calls` lists every (method, path) the adapter sent, so a test can assert what was and was not
called. Failures are scripted with `fail_next(status)`.
"""
import re
import uuid


class _Fake:
    def __init__(self):
        self.calls = []
        self._fail = []
        self.requests = []  # (method, path, body)

    def fail_next(self, status, times=1):
        self._fail.extend([status] * times)

    def _split(self, url):
        path = url.split("://", 1)[1].split("/", 1)[1]
        path, _, query = ("/" + path).partition("?")
        from urllib.parse import parse_qs
        return path, {k: v[0] for k, v in parse_qs(query).items()}


class FakeVercel(_Fake):
    """The storefronts project's domain list."""

    CNAME = "fake-cname.vercel-dns.example"
    IPV4 = "203.0.113.10"

    def __init__(self, project_id="prj_fake"):
        super().__init__()
        self.project_id = project_id
        self.domains = {}
        self.auto_verify = True
        self.refuse_config = False

    def __call__(self, method, url, headers, body):
        path, _ = self._split(url)
        self.calls.append((method, path))
        self.requests.append((method, path, body))
        if self._fail:
            return self._fail.pop(0), {}
        m = re.fullmatch(rf"/v10/projects/{self.project_id}/domains", path)
        if m and method == "POST":
            name = body["name"]
            if name in self.domains:
                return 400, {}
            self.domains[name] = {"name": name, "verified": self.auto_verify, "verification": [],
                                  "redirect": body.get("redirect", "")}
            return 200, self.domains[name]
        if re.fullmatch(rf"/v9/projects/{self.project_id}/domains", path) and method == "GET":
            return 200, {"domains": list(self.domains.values())}
        m = re.fullmatch(rf"/v9/projects/{self.project_id}/domains/([a-z0-9.-]+)(/verify)?", path)
        if m:
            name, verify = m.group(1), m.group(2)
            if name not in self.domains:
                return 404, {}
            if method == "DELETE":
                del self.domains[name]
                return 200, {}
            if verify and method == "POST":
                self.domains[name]["verified"] = True
            return 200, self.domains[name]
        m = re.fullmatch(r"/v6/domains/([a-z0-9.-]+)/config", path)
        if m and method == "GET":
            if self.refuse_config:
                return 403, {}
            name = m.group(1)
            return 200, {
                "misconfigured": False,
                "recommendedCNAME": [{"rank": 1, "value": self.CNAME + "."}],
                "recommendedIPv4": [{"rank": 1, "value": [self.IPV4]}],
            }
        return 404, {}


class FakeCloudflare(_Fake):
    """One account with zones and their records."""

    def __init__(self, account_id="acct_fake"):
        super().__init__()
        self.account_id = account_id
        self.zones = {}  # name -> {"id", "account"}
        self.records = {}  # zone id -> {record id -> record}
        self.records_exist_error = False

    def add_zone(self, name, account_id=None):
        zid = uuid.uuid4().hex
        self.zones[name] = {"id": zid, "name": name, "account": {"id": account_id or self.account_id}}
        self.records[zid] = {}
        return zid

    def __call__(self, method, url, headers, body):
        path, query = self._split(url)
        self.calls.append((method, path))
        self.requests.append((method, path, body))
        if self._fail:
            return self._fail.pop(0), {}
        if path == "/client/v4/zones" or path == "/zones":
            found = [z for z in self.zones.values()
                     if query.get("name") in (None, z["name"]) and z["account"]["id"] == query.get("account.id", z["account"]["id"])]
            return 200, {"result": found}
        m = re.fullmatch(r"(?:/client/v4)?/zones/([0-9a-f]{32})/dns_records(?:/([0-9a-f]{32}))?", path)
        if m:
            zid, rid = m.groups()
            store = self.records.get(zid)
            if store is None:
                return 404, {}
            if method == "GET":
                return 200, {"result": list(store.values())}
            if method == "POST":
                if self.records_exist_error:
                    return 409, {}
                rid = uuid.uuid4().hex
                store[rid] = {"id": rid, **{k: body[k] for k in ("type", "name", "content")}, "proxied": body.get("proxied")}
                return 200, {"result": store[rid]}
            if method == "DELETE":
                if rid not in store:
                    return 404, {}
                del store[rid]
                return 200, {"result": {"id": rid}}
        return 404, {}


class FakeRegistrarRead:
    """The one registrar read B1 uses (`registration read`): name -> {expires_at, auto_renew}."""

    def __init__(self):
        self.registrations = {}
        self.calls = []

    def get(self, name):
        self.calls.append(name)
        return self.registrations.get(name)
