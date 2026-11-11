"""Membership Pack constants, carried over from TWCinc's negosyoonline_sante_pack/constants.py.

Money is integer centavos. The wording is TWCinc's, character for character, except the
support contact, which is each Subscriber's own (Subscribers are separate businesses)."""
PRICE_CENTAVOS = 148800  # PHP 1,488.00, frozen on every order row
CURRENCY = "PHP"
PAYMENT_METHOD = "direct"  # bank or e-wallet transfer with proof; Xendit is not built, never COD
MAX_AMOUNT_CENTAVOS = 9999999  # PHP 99,999.99

PENDING_REVIEW, REJECTED, VERIFIED = "pending_review", "rejected", "verified"
PAYMENT_STATUSES = [
    (PENDING_REVIEW, "Under review"), (REJECTED, "Needs attention"), (VERIFIED, "Verified"),
]
LIVE_STATUSES = (PENDING_REVIEW, REJECTED, VERIFIED)

EVENT_PLACED = "placed"
ORDER_EVENT_KINDS = [(EVENT_PLACED, "Placed")]
ACTOR_TYPES = [
    ("member", "Member"), ("subscriber", "Subscriber"), ("operator", "Operator"), ("system", "System"),
]

# The agreement popup's words and the enrollment authorization, recorded with the order
# (Coach Ed 2026-10-31). The enrollment words are his exact wording.
ENROLLMENT_TEXT_VERSION = "2026-10-31"
ENROLLMENT_AUTHORIZATION = (
    "I understand that this package includes an application for Santé International membership, "
    "and I authorize the submission of my registration information to Santé International for "
    "enrollment, subject to the applicable privacy notice."
)
MSG_AGREEMENT_UNAVAILABLE = "Membership checkout is not available right now. Please check back soon."
MSG_AGREEMENT_REQUIRED = "Please review and agree to the Membership Terms to continue."
MSG_AGREEMENT_CHANGED = (
    "The Membership Terms were updated. Your details are saved. Please review and agree to continue."
)
MSG_ENROLLMENT_REQUIRED = "Please tick this box to continue."
MSG_NOT_AVAILABLE = "Membership Pack checkout is not available yet."
MSG_PAYMENT_UNAVAILABLE = "Payment is not available right now."
MSG_CHECKOUT_EXPIRED = "This checkout has expired. Please start again."
MSG_NO_CONTACT = "Enter your details first."
MSG_NAME = "Enter your full name."
MSG_VERIFY_FIRST = "Verify your mobile number first."
MSG_CHECK_FIELDS = "Check the highlighted fields."

# Contact and checkout limits, TWCinc's numbers (onboarding.py): (scope, limit, window seconds).
CONTACT_IP_LIMIT = ("pack-contact-ip", 20, 3600)
CONTACT_TOKEN_LIMIT = ("pack-contact-token", 10, 3600)
CHECK_IP_LIMIT = ("pack-check-ip", 60, 3600)

# The checkout token slides: every use pushes its expiry two hours on (TWCinc checkout.TTL).
TOKEN_TTL_SECONDS = 2 * 3600

# The proof file. TWCinc allows 10 MB. Here the proof travels browser -> storefronts route
# handler on Vercel -> Django, and a Vercel Function request body is capped at 4.5 MB (Vercel
# docs, "Vercel Functions limitations", checked 2026-11-12), so the cap is 4 MB.
PROOF_MAX_BYTES = 4 * 1024 * 1024
PROOF_FOLDER = "sante/proofs/site-{}"
