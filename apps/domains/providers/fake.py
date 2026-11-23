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
        path = path.removeprefix("/client/v4")
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
        if path == "/zones":
            found = [z for z in self.zones.values()
                     if query.get("name") in (None, z["name"]) and z["account"]["id"] == query.get("account.id", z["account"]["id"])]
            return 200, {"result": found}
        m = re.fullmatch(r"/zones/([0-9a-f]{32})/dns_records(?:/([0-9a-f]{32}))?", path)
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


class FakeResend(_Fake):
    """The Resend domains list. `template` makes the records an answer carries (a test sets its own
    values to prove nothing is hard-coded); `capacity_error` is the status a create answers with
    when the plan is full; `status_script` is the statuses a get answers with in turn."""

    def __init__(self):
        super().__init__()
        self.domains = {}  # id -> {"id", "name", "status", "region", "records"}
        self.verify_calls = 0
        self.capacity_error = None
        self.auto_status = "pending"  # what a verify leaves a domain at
        self.template = None  # callable(name) -> list of record dicts

    def default_records(self, name):
        token = uuid.uuid4().hex[:12]
        return [
            {"record": "DKIM", "name": "resend._domainkey", "type": "TXT", "ttl": "Auto",
             "status": "not_started", "value": f"p=FAKEDKIM{token}"},
            {"record": "SPF", "name": "send", "type": "MX", "ttl": "Auto", "status": "not_started",
             "value": f"feedback-smtp.{token}.example", "priority": 10},
            {"record": "SPF", "name": "send", "type": "TXT", "ttl": "Auto", "status": "not_started",
             "value": f"v=spf1 include:{token}.example ~all"},
        ]

    def set_status(self, domain_id, status):
        self.domains[domain_id]["status"] = status

    def __call__(self, method, url, headers, body):
        path, _ = self._split(url)
        self.calls.append((method, path))
        self.requests.append((method, path, body))
        if self._fail:
            return self._fail.pop(0), {}
        if path == "/domains" and method == "POST":
            if self.capacity_error:
                return self.capacity_error, {}
            if any(d["name"] == body["name"] for d in self.domains.values()):
                return 409, {}
            did = str(uuid.uuid4())
            records = (self.template or self.default_records)(body["name"])
            self.domains[did] = {"id": did, "name": body["name"], "status": "not_started",
                                 "region": body["region"], "records": records,
                                 "capabilities": body.get("capabilities")}
            return 201, self.domains[did]
        if path == "/domains" and method == "GET":
            return 200, {"data": [{"id": d["id"], "name": d["name"], "status": d["status"]} for d in self.domains.values()]}
        m = re.fullmatch(r"/domains/([A-Za-z0-9-]+)(/verify)?", path)
        if m:
            did, verify = m.groups()
            if did not in self.domains:
                return 404, {}
            if method == "DELETE":
                del self.domains[did]
                return 200, {"object": "domain", "id": did, "deleted": True}
            if verify and method == "POST":
                self.verify_calls += 1
                if self.domains[did]["status"] in ("not_started", "pending"):
                    self.domains[did]["status"] = self.auto_status
                return 200, {"object": "domain", "id": did}
            if method == "GET":
                return 200, self.domains[did]
        return 404, {}


class FakeRegistrarRead:
    """The one registrar read B1 uses (`registration read`): name -> {expires_at, auto_renew}."""

    def __init__(self):
        self.registrations = {}
        self.calls = []

    def get(self, name):
        self.calls.append(name)
        return self.registrations.get(name)


class FakeRegistrar(_Fake):
    """The Cloudflare Registrar API for one account: Check prices by extension, a register that
    answers 201, 202 or an error, a status per name, and the registrations held.

    `register_calls` counts every POST to `registrations` (a money test asserts it never exceeds
    one per site). `register_mode` is "201", "202", an error status as a string ("409"), or
    "timeout" (the transport raises, as a lost connection would).
    """

    def __init__(self, account_id="acct_fake"):
        super().__init__()
        self.account_id = account_id
        self.prices = {}  # tld -> (registration cents, renewal cents)
        self.default_price = (857, 857)
        self.tier = {}  # name or tld -> tier (default "standard")
        self.currency = "USD"
        self.taken = set()  # names that are not registrable
        self.registrations = {}  # name -> {"expires_at", "auto_renew", "account_id"}
        self.statuses = {}  # name -> list of statuses answered in turn (the last repeats)
        self.register_mode = "201"
        self.register_calls = 0
        self.register_bodies = []
        self.status_error = None  # an HTTP status the status call answers with
        self.get_error = None

    @staticmethod
    def _dollars(cents):
        return f"{cents // 100}.{cents % 100:02d}"

    def __call__(self, method, url, headers, body):
        path, _ = self._split(url)
        self.calls.append((method, path))
        self.requests.append((method, path, body))
        root = f"/accounts/{self.account_id}/registrar"
        if self._fail:
            return self._fail.pop(0), {}
        if method == "POST" and path == f"{root}/domain-check":
            rows = []
            for name in body["domains"]:
                tld = name.rsplit(".", 1)[-1]
                reg, ren = self.prices.get(tld, self.default_price)
                taken = name in self.taken or name in self.registrations
                rows.append({
                    "name": name, "registrable": not taken,
                    "tier": self.tier.get(name) or self.tier.get(tld) or "standard",
                    "pricing": {"currency": self.currency, "registration_cost": self._dollars(reg),
                                "renewal_cost": self._dollars(ren)},
                })
            return 200, {"result": {"domains": rows}}
        if method == "POST" and path == f"{root}/registrations":
            self.register_calls += 1
            self.register_bodies.append(body)
            name = body["domain_name"]
            mode = self.register_mode
            if mode == "timeout":
                from apps.domains.providers.base import ProviderError
                raise ProviderError("unavailable", retryable=True)
            if mode in ("201", "202"):
                if mode == "201":
                    self.registrations[name] = {"expires_at": "2027-11-22T00:00:00Z", "auto_renew": False,
                                                "account_id": self.account_id}
                else:
                    self.statuses.setdefault(name, ["in_progress"])
                return int(mode), {"result": {"domain_name": name}}
            return int(mode), {}
        m = re.fullmatch(rf"{re.escape(root)}/registrations/([a-z0-9.-]+)/registration-status", path)
        if method == "GET" and m:
            if self.status_error:
                return self.status_error, {}
            name = m.group(1)
            script = self.statuses.get(name)
            if script:
                state = script[0] if len(script) == 1 else script.pop(0)
            elif name in self.registrations:
                state = "succeeded"
            else:
                return 404, {}
            return 200, {"result": {"status": state}}
        m = re.fullmatch(rf"{re.escape(root)}/registrations/([a-z0-9.-]+)", path)
        if method == "GET" and m:
            if self.get_error:
                return self.get_error, {}
            found = self.registrations.get(m.group(1))
            if found is None:
                return 404, {}
            return 200, {"result": {"domain_name": m.group(1), **found}}
        return 404, {}
