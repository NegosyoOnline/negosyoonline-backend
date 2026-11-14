"""Santé shop constants. Money is integer centavos. The wording is TWCinc's where TWCinc had it
(negosyoonline_storefront/constants.py), except the bank-transfer wording (bank transfer is the
only payment) and the support contact, which is each Subscriber's own."""
CURRENCY = "PHP"
MAX_AMOUNT_CENTAVOS = 9999999  # PHP 99,999.99, an advisory proof amount

# Cart limits (TWCinc constants.py:20-21), enforced here and mirrored by the page.
QTY_MIN, QTY_MAX = 1, 99
MAX_LINES = 20

# payment_status
PENDING_REVIEW, VERIFIED, CANCELLED = "pending_review", "verified", "cancelled"
PAYMENT_STATUSES = [
    (PENDING_REVIEW, "Under review"), (VERIFIED, "Paid"), (CANCELLED, "Cancelled"),
]

# buyer_kind and ship_mode. A signed-in member without benefits buys as a guest at SRP
# (buyer_kind "member" so the order shows in My Orders, ship_mode "guest").
GUEST, MEMBER = "guest", "member"
BUYER_KINDS = [(GUEST, "Guest"), (MEMBER, "Member")]
HOME, DIFFERENT = "home", "different"
SHIP_MODES = [(GUEST, "Guest"), (HOME, "Member's own address"), (DIFFERENT, "Another address")]

CHARGE, ZERO = "charge", "zero"
SHIPPING_POLICIES = [(CHARGE, "Charge"), (ZERO, "Zero")]

EVENT_PLACED, EVENT_VERIFIED, EVENT_CANCELLED, EVENT_SHIPPED, EVENT_DELIVERED = (
    "placed", "verified", "cancelled", "shipped", "delivered",
)
EVENT_KINDS = [
    (EVENT_PLACED, "Placed"), (EVENT_VERIFIED, "Payment confirmed"), (EVENT_CANCELLED, "Cancelled"),
    (EVENT_SHIPPED, "Shipped"), (EVENT_DELIVERED, "Delivered"),
]
ACTOR_TYPES = [("buyer", "Buyer"), ("subscriber", "Subscriber"), ("system", "System")]

NOTE_MAX = 500
COURIER_MAX, TRACKING_MAX = 60, 64
RECEIVER_NAME_MAX = 120
REFERENCE_MAX = 64
ORDER_NUMBER = "SO%05d"

# The proof file: same cap and types as the pack proof (Vercel body limit, 4 MB).
PROOF_MAX_BYTES = 4 * 1024 * 1024
PROOF_FOLDER = "sante/shop-proofs/site-{}"

# The cookie token of the placing buyer, answered once, kept by the storefronts server.
VIEW_TOKEN_BYTES = 32

# Wording
MSG_EMPTY_CART = "Your cart is empty."
MSG_UNAVAILABLE = "This product is no longer available. Remove it to continue."
MSG_SHIPPING_MISSING = "Shipping is not available for this product yet."
MSG_SHIPPING_FAILED = "We could not work out the shipping for this order."
MSG_PRICES_CHANGED = "Prices changed. Review your order."
MSG_BAD_QTY = "Enter a quantity from 1 to 99."
MSG_TOO_MANY_LINES = "A cart holds up to 20 different products."
MSG_BAD_LINE = "Check the products in your cart."
MSG_SHIP_MODE = "Choose how to ship this order."
MSG_SHIP_MODE_NOT_ALLOWED = "Only a member with active benefits can ship to a member address."
MSG_NO_HOME_ADDRESS = "Add your delivery address to your member profile first."
MSG_CHECK_FIELDS = "Check the highlighted fields."
MSG_PAYMENT_UNAVAILABLE = "Payment is not available right now."
MSG_NO_SUBMIT_KEY = "This checkout has expired. Please start again."
MSG_SESSION = "Not signed in."
MSG_WRONG_STATUS = "This order cannot be changed now."
MSG_STALE = "This order changed. Reload it and try again."
MSG_NOTE_REQUIRED = "Write a note of 1 to 500 characters."
MSG_COURIER = "Enter the courier."
MSG_BAD_DATE = "Enter a valid date that is not in the future."
MSG_SHIP_DATE_ORDER = "The delivery date cannot be before the shipping date."
MSG_STORE_UNAVAILABLE = "We could not save your proof. Please try again shortly."
MSG_STORE_PAYS_SHIPPING = "This store pays your shipping."

# Product reviews
RATING_MIN, RATING_MAX = 1, 5
REVIEW_BODY_MAX, REVIEW_NAME_MAX = 1000, 40
REVIEW_EDIT_DAYS = 7
REVIEW_PAGE_SIZE = 10
MSG_REVIEW_FIELDS = "Check the highlighted fields."
MSG_REVIEW_RATING = "Choose a rating from 1 to 5."
MSG_REVIEW_BODY = "Write up to 1000 characters."
MSG_REVIEW_NAME = "Use a name of 1 to 40 characters."
MSG_REVIEW_NOT_DELIVERED = "You can review a product once your order is delivered."
MSG_REVIEW_EXISTS = "You already reviewed this product."
MSG_REVIEW_LOCKED = "This review can no longer be changed."
MSG_REVIEW_NONE = "There is no review to change."

# Throttles: (scope, limit, window seconds).
QUOTE_IP_LIMIT = ("shop-quote-ip", 120, 3600)
PLACE_IP_LIMIT = ("shop-place-ip", 20, 3600)
VIEW_IP_LIMIT = ("shop-view-ip", 60, 3600)
MEMBER_LIST_LIMIT = ("shop-member-orders", 120, 3600)
OWNER_WRITE_LIMIT = ("shop-write-acct", 60, 600)
OWNER_LINK_LIMIT = ("shop-link-acct", 120, 600)
OWNER_LIST_MAX = 200
REVIEW_IP_LIMIT = ("shop-review-ip", 30, 3600)
