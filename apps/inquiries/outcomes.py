"""The fixed outcome codes every provider adapter answers with. No provider text is ever kept."""
OK = "ok"
INVALID_CREDENTIALS = "invalid_credentials"
EXPIRED_CREDENTIALS = "expired_credentials"
FORBIDDEN_PAGE = "forbidden_page"
NOT_FOUND = "not_found"
RATE_LIMITED = "rate_limited"
PROVIDER_DOWN = "provider_down"
BAD_RESPONSE = "bad_response"
WINDOW_CLOSED = "window_closed"
BLOCKED_SITE = "blocked_site"
NOT_CONFIGURED = "not_configured"

CODES = (
    OK, INVALID_CREDENTIALS, EXPIRED_CREDENTIALS, FORBIDDEN_PAGE, NOT_FOUND, RATE_LIMITED,
    PROVIDER_DOWN, BAD_RESPONSE, WINDOW_CLOSED, BLOCKED_SITE, NOT_CONFIGURED,
)
# The codes that mean the saved token no longer works: the connection is marked invalid.
CREDENTIAL_FAILURES = (INVALID_CREDENTIALS, EXPIRED_CREDENTIALS)
