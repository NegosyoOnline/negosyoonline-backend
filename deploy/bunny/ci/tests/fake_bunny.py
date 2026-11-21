"""A fake Bunny Magic Containers API plus fake app /healthz/ and /readyz/, for local tests only.

Run: python fake_bunny.py <port-file>. It binds 127.0.0.1 on a free port and writes the port to
<port-file>. State is in memory. Endpoints:

  GET   /mc/apps/<app>                           the app with five container templates
  PATCH /mc/apps/<app>/containers/<id>           merges imageTag/imageDigest/imageNamespace/imageName only
  GET   /healthz/                                {"status":"ok","commit":<first 12 of the api tag>}
  GET   /readyz/                                 200 all true, or 503 with beat false when broken
  POST  /_ctl/broken | /_ctl/fixed               toggle the broken readiness
  GET   /_ctl/log                                the API calls received (method path, no bodies)

The AccessKey header must equal "test-key" on /mc/ calls, otherwise 401.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

APP = "app-prod"
STATE = {"broken": False, "log": []}
CONTAINERS = [
    {"id": "c-api", "name": "api", "imageNamespace": "negosyoonline", "imageName": "pilot", "imageTag": "pilot-api", "imageDigest": "sha256:" + "0" * 64,
     "env": [{"name": "DJANGO_SECRET_KEY", "value": "SECRET-SHOULD-NEVER-PRINT"}], "volumes": []},
    {"id": "c-worker", "name": "worker", "imageNamespace": "negosyoonline", "imageName": "pilot", "imageTag": "pilot-worker", "imageDigest": "sha256:" + "0" * 64, "env": []},
    {"id": "c-beat", "name": "beat", "imageNamespace": "negosyoonline", "imageName": "pilot", "imageTag": "pilot-beat", "imageDigest": "sha256:" + "0" * 64, "env": []},
    {"id": "c-pg", "name": "postgres", "imageNamespace": "library", "imageName": "postgres", "imageTag": "18", "imageDigest": "", "env": [], "volumes": ["prod-pg18"]},
    {"id": "c-redis", "name": "redis", "imageNamespace": "library", "imageName": "redis", "imageTag": "7", "imageDigest": "", "env": [], "volumes": ["prod-redis"]},
]
ALLOWED = {"imageTag", "imageDigest", "imageNamespace", "imageName"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if self.headers.get("AccessKey") != "test-key":
            self._send(401, {"message": "bad key"})
            return False
        return True

    def do_GET(self):
        if self.path == f"/mc/apps/{APP}":
            if not self._auth():
                return
            STATE["log"].append("GET " + self.path)
            return self._send(200, {"id": APP, "containerTemplates": CONTAINERS})
        if self.path == "/healthz/":
            api = next(c for c in CONTAINERS if c["id"] == "c-api")
            return self._send(200, {"status": "ok", "commit": api["imageTag"][:12]})
        if self.path == "/readyz/":
            if STATE["broken"]:
                return self._send(503, {"db": True, "cache": True, "beat": False})
            return self._send(200, {"db": True, "cache": True, "beat": True})
        if self.path == "/_ctl/log":
            return self._send(200, STATE["log"])
        self._send(404, {"message": "not found"})

    def do_POST(self):
        if self.path == "/_ctl/broken":
            STATE["broken"] = True
        elif self.path == "/_ctl/fixed":
            STATE["broken"] = False
        else:
            return self._send(404, {"message": "not found"})
        self._send(200, {"ok": True})

    def do_PATCH(self):
        prefix = f"/mc/apps/{APP}/containers/"
        if not self.path.startswith(prefix):
            return self._send(404, {"message": "not found"})
        if not self._auth():
            return
        cid = self.path[len(prefix):]
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        c = next((x for x in CONTAINERS if x["id"] == cid), None)
        if c is None:
            return self._send(404, {"message": "no such container"})
        extra = set(body) - ALLOWED
        if extra:
            return self._send(400, {"message": "fields not allowed in this fake: " + ",".join(sorted(extra))})
        STATE["log"].append(f"PATCH {self.path} {sorted(body)}")
        c.update(body)
        self._send(200, c)


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", 0), H)
    with open(sys.argv[1], "w") as f:
        f.write(str(srv.server_address[1]))
    srv.serve_forever()
