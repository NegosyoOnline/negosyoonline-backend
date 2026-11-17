"""The outbound rate control per connection, shared by every web worker through the cache.

Time is cut into slots of 1/RATE seconds and each slot admits one call, so a connection sends
at most RATE calls a second and never more than RATE + 1 in any one second (RATE is 4). A caller
whose slot is taken waits for a later one, but never longer than MAX_WAIT seconds: after that it is
refused and the owner is told to retry. The key carries the connection's primary key, so one site
never uses another's permits."""
import time

from django.core.cache import cache

RATE = 4  # calls per second per connection
MAX_WAIT = 1.0  # seconds a web worker may wait for a permit


def acquire(connection_pk, *, now=time.time, sleep=time.sleep):
    """True once a permit is held, False if none became free within MAX_WAIT."""
    width = 1.0 / RATE
    started = now()
    slot = int(started / width)
    while True:
        key = f"inq:rl:{connection_pk}:{slot}"
        cache.add(key, 0, 3)
        try:
            taken = cache.incr(key)
        except ValueError:  # the key expired between add and incr
            cache.set(key, 1, 3)
            taken = 1
        if taken == 1:
            wait = (slot * width) - now()
            if wait > 0:
                sleep(wait)
            return True
        slot += 1
        if (slot * width) - started > MAX_WAIT:
            return False
