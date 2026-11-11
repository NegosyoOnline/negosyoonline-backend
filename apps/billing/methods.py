"""Payment methods, as the lifecycle needs to ask about them.

B1 has no payment-method table, so the answer is always no. Billing Build B2 replaces the
body with a read of the payment-method history as of that instant.
"""


def active_at(site, instant):
    """Whether the site had an active payment method at that instant."""
    return False
