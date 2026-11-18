"""The comment auto-reply limits, in one place so they can be tuned (task z8uq9mg9pj; Coach Ed
2026-11-18 01:00 HKT, CM2: 30 an hour, 300 a day per Page, one per person per post per 24 hours).
Every other module reads these names at call time, so a test (or a later tuning) changes them here."""
from datetime import timedelta

HOUR_CAP = 30  # automatic replies per Page per rolling hour, public and private each counted
DAY_CAP = 300  # per Page per Manila calendar day
PER_PERSON_WINDOW = timedelta(hours=24)  # one automatic reply per person per post in this window
DELAY_MIN, DELAY_MAX = 30, 120  # seconds between queueing and sending, random
PAUSE = timedelta(hours=1)  # after a Meta rate-limit or spam error
STRIKES_PER_DAY = 3  # strikes in one Manila day switch automation off until the owner re-enables it
MAX_AGE = timedelta(days=7)  # an older comment is never auto-answered
MAX_RULES = 20
MAX_KEYWORDS, KEYWORD_MAX_CHARS = 20, 40
MAX_POSTS = 50
PUBLIC_MAX, PRIVATE_MAX = 500, 1000
NAME_MAX = 30  # the longest first name counted when checking the length of a template
MIN_PUBLIC_VARIANTS = 2
# Meta error codes that count as a strike when seen on a send (4, 9, 17, 32, 613 map to
# rate_limited in messenger.map_error; 368 is Meta's temporary "spam" block and keeps its mapping).
STRIKE_CODES = (4, 9, 17, 32, 613, 368)
