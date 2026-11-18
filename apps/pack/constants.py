"""Membership Pack constants, carried over from TWCinc's negosyoonline_sante_pack/constants.py.

Money is integer centavos. The wording is TWCinc's, character for character, except the
support contact, which is each Subscriber's own (Subscribers are separate businesses)."""
PRICE_CENTAVOS = 148800  # PHP 1,488.00, frozen on every order row
CURRENCY = "PHP"
PAYMENT_METHOD = "direct"  # bank or e-wallet transfer with proof; never COD
XENDIT = "xendit"  # a Xendit hosted invoice, the site's own key (Coach Ed, 2026-11-18)
METHODS = (PAYMENT_METHOD, XENDIT)
METHOD_LABELS = {PAYMENT_METHOD: "Bank or e-wallet transfer", XENDIT: "Pay online (Xendit)"}
MAX_AMOUNT_CENTAVOS = 9999999  # PHP 99,999.99

PENDING_REVIEW, REJECTED, VERIFIED = "pending_review", "rejected", "verified"
# A Xendit order is `awaiting_xendit` until Xendit confirms it (webhook or server-side read); a paid one
# is `verified` (payment_method "xendit", paid_at set), so shipping, registration and the member panel
# read it like any verified order. `xendit_mismatch`: Xendit reported another amount or currency.
AWAITING_XENDIT, XENDIT_EXPIRED, XENDIT_MISMATCH = "awaiting_xendit", "xendit_expired", "xendit_mismatch"
PAYMENT_STATUSES = [
    (PENDING_REVIEW, "Under review"), (REJECTED, "Needs attention"), (VERIFIED, "Verified"),
    (AWAITING_XENDIT, "Waiting for payment"), (XENDIT_EXPIRED, "Payment not completed"),
    (XENDIT_MISMATCH, "Under review"),
]
LIVE_STATUSES = (PENDING_REVIEW, REJECTED, VERIFIED, AWAITING_XENDIT, XENDIT_MISMATCH)
XENDIT_UNPAID = (AWAITING_XENDIT, XENDIT_EXPIRED, XENDIT_MISMATCH)  # a Xendit order whose onboarding stays closed
INVOICE_SECONDS = 24 * 3600
EXTERNAL_PREFIX = "np-"  # np-{site row id}-{24 hex}: our own reference at Xendit
RETURN_PATH = "/funnel/membership-pack/checkout/return/"
MSG_PAYMENT_PENDING = "Your payment is not confirmed yet."
MSG_INVOICE_BUSY = "Payment is being prepared. Please try again shortly."
INVOICE_RETRY_LIMIT = ("pack-invoice-retry", 5, 3600)

REJECTION_REASONS = [
    ("amount_mismatch", "Amount does not match ₱1,488."),
    ("payment_not_found", "Payment not found in the receiving account."),
    ("proof_unreadable", "Proof is unreadable or incomplete."),
    ("reference_mismatch", "Reference number is missing or does not match."),
    ("wrong_account", "Payment was sent to the wrong account."),
    ("other", "Other, with a mandatory written explanation."),
]
REASON_LABELS = dict(REJECTION_REASONS)
EXPLAIN_REASON = "Explain the reason."
MSG_PAYMENT_WRONG_STATUS = "This payment cannot be changed now."
MSG_ORDER_STALE = "This order changed. Reload it and try again."
MSG_CHOOSE_REASON = "Choose a reason."
MSG_NOT_REJECTED = "A new proof can be sent only after the store asks for one."

EVENT_PLACED = "placed"
EVENT_SHIPMENT_RECORDED, EVENT_SHIPMENT_CORRECTED, EVENT_DELIVERED = (
    "shipment_recorded", "shipment_corrected", "delivered",
)
EVENT_XENDIT_PAID, EVENT_XENDIT_EXPIRED, EVENT_XENDIT_MISMATCH, EVENT_XENDIT_SUPERSEDED = (
    "xendit_paid", "xendit_expired", "xendit_mismatch", "xendit_paid_superseded",
)
ORDER_EVENT_KINDS = [
    (EVENT_PLACED, "Placed"), (EVENT_SHIPMENT_RECORDED, "Shipment recorded"),
    (EVENT_SHIPMENT_CORRECTED, "Shipment corrected"), (EVENT_DELIVERED, "Delivered"),
    (EVENT_XENDIT_PAID, "Paid with Xendit"), (EVENT_XENDIT_EXPIRED, "Xendit payment expired"),
    (EVENT_XENDIT_MISMATCH, "Xendit amount mismatch"), (EVENT_XENDIT_SUPERSEDED, "Late Xendit payment"),
]

# Shipment by hand (PK-SHIP). The courier list is TWCinc's (negosyoonline_orders/models.py), a draft
# until Coach Ed confirms it; the words are TWCinc's fulfillment.py.
COURIER_OTHER = "other"
COURIERS = [
    ("lbc", "LBC"), ("jnt", "J&T Express"), ("flash", "Flash Express"), ("ninjavan", "Ninja Van"),
    ("2go", "2GO"), ("grab", "Grab"), ("lalamove", "Lalamove"), (COURIER_OTHER, "Other"),
]
COURIER_LABELS = dict(COURIERS)
NOT_SHIPPED, SHIPPED, DELIVERED = "not_shipped", "shipped", "delivered"
MSG_NOT_SHIPPABLE = "Shipping opens after the payment is verified."
MSG_ALREADY_SHIPPED = "The shipment is already recorded."
MSG_NOT_SHIPPED = "Record the shipment first."
MSG_DELIVERED_FINAL = "Delivery is confirmed; the shipment can no longer change."
MSG_ALREADY_DELIVERED = "Delivery is already confirmed."
MSG_CHECK_SHIPMENT = "Check the highlighted fields."
MSG_SHIP = {
    "courier": "Choose the courier.", "courier_other_name": "Enter the courier name.",
    "tracking_number": "Enter the tracking number.",
    "shipped_on": "Enter a shipped date that is not in the future.",
    "delivered_on": "Enter a delivery date on or after the shipped date and not in the future.",
}
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
# The Membership Pack page visitor (PackVisit): a new visit per IP, any event per IP, and per visit.
VISIT_IP_LIMIT = ("pack-visit-ip", 60, 3600)
VISIT_EVENT_IP_LIMIT = ("pack-visit-event-ip", 600, 3600)
VISIT_REF_LIMIT = ("pack-visit-ref", 200, 3600)
VISIT_CONTACT_LIMIT = ("pack-visit-contact", 60, 3600)

# The checkout token slides: every use pushes its expiry two hours on (TWCinc checkout.TTL).
TOKEN_TTL_SECONDS = 2 * 3600

# The proof file. TWCinc allows 10 MB. Here the proof travels browser -> storefronts route
# handler on Vercel -> Django, and a Vercel Function request body is capped at 4.5 MB (Vercel
# docs, "Vercel Functions limitations", checked 2026-11-12), so the cap is 4 MB.
PROOF_MAX_BYTES = 4 * 1024 * 1024
PROOF_FOLDER = "sante/proofs/site-{}"

# Santé registration (PK7): only the edges up to the member signing in are carried (T1, T3, T4;
# T8 arrives with the Subscriber's approval). The statuses are TWCinc's full list so a later slice
# adds edges without a migration.
REG_STATUSES = (
    "awaiting_buyer_details", "waiting_for_accept", "ready_to_register", "in_registration",
    "submitted_on_sante", "needs_buyer_action", "blocked", "retry_wait", "possible_existing_member",
    "registered", "cancelled",
)
REG_STATUS_CHOICES = [(s, s.replace("_", " ").capitalize()) for s in REG_STATUSES]
PAYMENT_OK = (VERIFIED,)

# The "Santé account created" email (PackRegisteredNotice): pending until the first try, failed
# while it is being retried, then sent or gave_up.
NOTICE_PENDING, NOTICE_SENT, NOTICE_FAILED, NOTICE_GAVE_UP = "pending", "sent", "failed", "gave_up"
NOTICE_STATUSES = (NOTICE_PENDING, NOTICE_SENT, NOTICE_FAILED, NOTICE_GAVE_UP)
NOTICE_STATUS_CHOICES = [(s, s.replace("_", " ").capitalize()) for s in NOTICE_STATUSES]

MSG_TERMS_NOT_ON_RECORD = (
    "We could not find your agreement to the Membership Terms for this order. Please contact the store."
)
# Throttles of the onboarding and access steps, TWCinc's numbers (onboarding.py, site_views.py,
# access.py): (scope, limit, window seconds).
SEND_IP_LIMIT = ("pack-otp-ip", 10, 3600)
TOKEN_SEND_LIMIT = ("pack-otp-token", 5, 3600)
ACCESS_IP_LIMIT = ("pack-access-ip", 20, 3600)
RENEW_LIMIT = ("pack-access-renew", 3, 3600)
