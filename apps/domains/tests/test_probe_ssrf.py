"""The probe's guard, with a fake resolver and a fake connection. No network."""
import json

from django.test import SimpleTestCase

from apps.domains import probe


class FakeResponse:
    def __init__(self, status=200, body=b""):
        self.status, self._body = status, body

    def read(self, n=None):
        return self._body if n is None else self._body[:n]


def connection(response=None, boom=None):
    made = []

    class Conn:
        def __init__(self, host, ip, **kwargs):
            made.append((host, ip, kwargs))

        def request(self, method, path, headers=None):
            made.append((method, path, headers))
            if boom:
                raise boom

        def getresponse(self):
            return response

        def close(self):
            pass

    Conn.made = made
    return Conn


class BlockedAddressTests(SimpleTestCase):
    def test_blocked(self):
        for ip in ("127.0.0.1", "10.0.0.1", "172.16.0.5", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                   "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
                   "2002:7f00:1::1", "198.18.0.1", "not-an-ip", "240.0.0.1"):
            with self.subTest(ip=ip):
                self.assertTrue(probe.blocked_address(ip))

    def test_a_public_address_is_allowed(self):
        for ip in ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"):
            with self.subTest(ip=ip):
                self.assertFalse(probe.blocked_address(ip))


class ProbeTests(SimpleTestCase):
    NONCE = "n1"

    def run_probe(self, response, resolver=None, **kwargs):
        conn = connection(response, **kwargs)
        result = probe.probe("www.brand.com", self.NONCE, resolver=resolver or (lambda h: ["93.184.216.34"]),
                             connection_class=conn)
        return result, conn

    def ok_body(self, **over):
        return json.dumps({"site": "site_abc", "n": self.NONCE} | over).encode()

    def test_success_pins_the_resolved_address_and_sends_no_cookie(self):
        result, conn = self.run_probe(FakeResponse(200, self.ok_body()))
        self.assertTrue(result.ok)
        self.assertEqual(result.site, "site_abc")
        self.assertEqual(conn.made[0][:2], ("www.brand.com", "93.184.216.34"))
        headers = conn.made[1][2]
        self.assertNotIn("Cookie", headers)
        self.assertEqual(conn.made[1][1], f"/.well-known/negosyoonline-probe?n={self.NONCE}")

    def test_any_blocked_resolution_refuses_before_connecting(self):
        for addresses in (["127.0.0.1"], ["93.184.216.34", "10.0.0.1"], ["::ffff:169.254.169.254"]):
            with self.subTest(addresses=addresses):
                result, conn = self.run_probe(FakeResponse(200, self.ok_body()), resolver=lambda h, a=addresses: a)
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, "blocked_address")
                self.assertEqual(conn.made, [])

    def test_a_redirect_is_a_failed_probe(self):
        for status in (301, 302, 307, 308):
            result, _ = self.run_probe(FakeResponse(status))
            self.assertFalse(result.ok)

    def test_a_wrong_nonce_or_shape_fails(self):
        for body in (self.ok_body(n="other"), b"not json", b"[]", json.dumps({"n": self.NONCE}).encode()):
            with self.subTest(body=body):
                result, _ = self.run_probe(FakeResponse(200, body))
                self.assertFalse(result.ok)

    def test_an_oversize_body_fails(self):
        result, _ = self.run_probe(FakeResponse(200, b"x" * (probe.MAX_BODY_BYTES + 10)))
        self.assertEqual(result.reason, "too_large")

    def test_a_resolution_failure_fails(self):
        def resolver(host):
            raise probe.ProbeRefused("dns")
        result, _ = self.run_probe(FakeResponse(200, self.ok_body()), resolver=resolver)
        self.assertEqual(result.reason, "dns")

    def test_a_rebound_peer_is_refused(self):
        result, _ = self.run_probe(None, boom=probe.ProbeRefused("peer"))
        self.assertEqual(result.reason, "peer")

    def test_network_errors_fail_the_probe_not_the_worker(self):
        result, _ = self.run_probe(None, boom=OSError("down"))
        self.assertFalse(result.ok)
