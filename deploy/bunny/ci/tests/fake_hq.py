"""A fake HQ host for verify-hq.sh tests, local only. Usage: fake_hq.py <port-file>
GET /ok/     -> 303 Location /sign-in/?next=/secret   (query must never be printed)
GET /miss/   -> 404
GET /fine/   -> 200
GET /other/  -> 303 Location /healthz/
GET /flaky/  -> 303 /sign-in/ twice, then 404, repeating (the streak must reset)
"""
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

n = {"flaky": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        code, loc = 404, None
        if self.path == "/ok/":
            code, loc = 303, "/sign-in/?next=/secret"
        elif self.path == "/fine/":
            code = 200
        elif self.path == "/other/":
            code, loc = 303, "/healthz/"
        elif self.path == "/flaky/":
            n["flaky"] += 1
            if n["flaky"] % 3 != 0:
                code, loc = 303, "/sign-in/"
        self.send_response(code)
        if loc:
            self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        self.end_headers()


s = HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(s.server_address[1]))
s.serve_forever()
