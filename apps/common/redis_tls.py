"""TLS options for a `rediss://` REDIS_URL, shared by the cache (django-redis) and Celery.

The Redis of the Bunny data app presents a self-signed certificate made on its first start, so a
client cannot check it against a public authority. Two modes, chosen by the environment, never
silently:

* `REDIS_TLS_CA_FILE` (a path) or `REDIS_TLS_CA_PEM` (the certificate text, written to a private
  file): that one certificate is the only trusted anchor, `ssl_cert_reqs=CERT_REQUIRED`. The
  server is authenticated. Hostname checking is off because the anchor is the pinned certificate
  itself and the data app is reached by an IP address.
* neither set: `ssl_cert_reqs=CERT_NONE`. The traffic is encrypted and the password is required, but
  the server is not authenticated, so an active man-in-the-middle is not excluded. This is a
  deliberate, documented choice for the first cutover (docs/runbooks/bunny-staging.md, "Redis
  certificate"), to be replaced by pinning.

A URL that carries its own `ssl_cert_reqs` query parameter is left alone. A plain `redis://` URL
gets no options.
"""
import hashlib
import os
import ssl
import tempfile
from urllib.parse import parse_qs, urlsplit


def _ca_file_from_pem(pem):
    """Write the certificate text to a private file named by its hash; return the path."""
    path = os.path.join(tempfile.gettempdir(), f"redis-ca-{hashlib.sha256(pem.encode()).hexdigest()[:16]}.pem")
    if not os.path.exists(path):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(pem)
    return path


def redis_tls_options(url, *, ca_file="", ca_pem=""):
    """Keyword options for redis-py / Celery's `broker_use_ssl`; `{}` unless `url` is rediss://."""
    parts = urlsplit(url or "")
    if parts.scheme != "rediss" or "ssl_cert_reqs" in parse_qs(parts.query):
        return {}
    ca_file = (ca_file or "").strip()
    if not ca_file and (ca_pem or "").strip():
        ca_file = _ca_file_from_pem(ca_pem.strip() + "\n")
    if ca_file:
        return {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_ca_certs": ca_file, "ssl_check_hostname": False}
    return {"ssl_cert_reqs": ssl.CERT_NONE}
