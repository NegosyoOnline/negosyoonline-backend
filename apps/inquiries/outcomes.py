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

# The email channel's own codes. They are NOT in CODES (that tuple is the choices of
# InquiryReply.error_code, and changing it needs a migration). The audit row keeps the code in
# EMAIL_AUDIT_CODE; the specific code is kept on InquiryMessage.error_code, a free field.
SENDER_NOT_READY = "sender_not_ready"
NO_RECIPIENT = "no_recipient"
EMAIL_FAILED = "email_failed"
EMAIL_CODES = (SENDER_NOT_READY, NO_RECIPIENT, EMAIL_FAILED)
EMAIL_AUDIT_CODE = {
    SENDER_NOT_READY: NOT_CONFIGURED, NO_RECIPIENT: NOT_FOUND, EMAIL_FAILED: PROVIDER_DOWN,
}
# What the owner is told, fixed text per code. Never the mail server's own words.
EMAIL_TEXTS = {
    SENDER_NOT_READY: "Email replies are not set up for this store.",
    NO_RECIPIENT: "This conversation has no email address to reply to.",
    EMAIL_FAILED: "The email could not be sent. Please try again shortly.",
    RATE_LIMITED: "Too many emails have been sent from this store. Please wait and try again.",
}
