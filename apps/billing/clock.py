"""Trial and cycle dates. `cycle_end` is the only place the length of a cycle lives."""
import calendar
from datetime import datetime, time
from zoneinfo import ZoneInfo

from apps.billing import plan

MANILA = ZoneInfo("Asia/Manila")
CUTOFF = time(15, 0)  # renewal and cutoff time: 3:00 PM Asia/Manila


def trial_end(start_at):
    """Exactly 14 days after the trial starts, to the second."""
    return start_at + plan.TRIAL


def anchor_day(instant):
    """The Manila day of the month a cycle starting at this instant renews on."""
    return instant.astimezone(MANILA).day


def cycle_end(start_at, anchor):
    """3:00 PM Manila on the anchor day of the month after the one the cycle starts in.

    A month without that day ends the cycle on its last day. The shortening does not carry
    forward: anchor 31 gives 28 February, then 31 March.
    """
    start = start_at.astimezone(MANILA)
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    day = min(anchor, calendar.monthrange(year, month)[1])
    return datetime.combine(datetime(year, month, day), CUTOFF, tzinfo=MANILA)
