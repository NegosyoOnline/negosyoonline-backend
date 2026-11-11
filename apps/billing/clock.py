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


def cycle_end(start_at, anchor, months=1):
    """3:00 PM Manila on the anchor day of the month `months` after the one the cycle starts in.

    A month without that day ends the cycle on its last day. The shortening does not carry
    forward: anchor 31 gives 28 February, then 31 March. A cycle of six months ends on the
    calendar date six months on, never after 180 days.
    """
    start = start_at.astimezone(MANILA)
    year, month = divmod(start.year * 12 + start.month - 1 + months, 12)
    month += 1
    day = min(anchor, calendar.monthrange(year, month)[1])
    return datetime.combine(datetime(year, month, day), CUTOFF, tzinfo=MANILA)


def allowance_periods(term_start, anchor, months, periods):
    """The monthly allowance periods of a term, as [(start, end), ...].

    Period k ends at `cycle_end(term_start, anchor, k)`, not one month after period k-1, so
    the shortening of a short month never carries forward. Period 1 starts at `term_start`,
    and `periods` must equal `months` for the last to end with the term.
    """
    ends = [cycle_end(term_start, anchor, k) for k in range(1, periods + 1)]
    return list(zip([term_start, *ends[:-1]], ends))
