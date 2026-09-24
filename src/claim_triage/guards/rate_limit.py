"""The rate limit in front of the boundary: a burst per caller, and a verdict when it is spent.

A token bucket, because that is the shape of the thing being limited: a caller may arrive in a
burst — a likvidátor submitting a folder of claims, a retry storm after a blip — and what has to be
bounded is the sustained rate it can hold, not how fast it knocks. One token is spent per submission
and tokens refill at a configured rate, up to the burst.

Two properties are the reason this is written rather than configured into a proxy:

* **It is a value, testable without waiting.** The clock is a constructor argument, so a test
  spends a burst, reads the wait, advances a clock it owns, and asserts the token came back. Nothing
  here sleeps and nothing here is a thread.
* **It is the guard vocabulary.** The answer is a `Verdict` like every other check's, so the
  boundary maps it to a 429 the way it maps an oversized upload to a 413, and a run that was
  admitted carries the same shape of evidence.

The state is in-process (`Limiters` holds one bucket per caller). That is honest about what it is:
per replica, so a deployment with N replicas allows N times the burst in aggregate, and a restart
forgets the bursts it was counting. There is no shared store behind this — Redis is in the stack for
the queue and the checkpointer — and the limitation is stated in `docs/adr/0007` rather than
implied.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Final

from claim_triage.guards.verdict import Check, Verdict

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_KEYS: Final = 1024
"""How many callers can be counted at once before the least recently seen one is dropped."""

ONE_TOKEN: Final = 1.0
"""What one submission costs. A submission is one unit however much it carries."""


@dataclass(frozen=True, slots=True)
class RateLimit:
    """The limit itself: how large a burst is, and how fast the tokens come back."""

    burst: int
    refill_per_second: float


@dataclass(frozen=True, slots=True)
class Admission:
    """What the limiter answered: the verdict, and how long until asking again would be worth it."""

    verdict: Verdict
    wait_seconds: float


class Bucket:
    """One caller's tokens: what is left of the burst, and when it was last looked at."""

    def __init__(self, limit: RateLimit, *, clock: Callable[[], float] = monotonic) -> None:
        self._limit = limit
        self._clock = clock
        self._tokens = float(limit.burst)
        self._filled_at = clock()

    def take(self) -> Admission:
        """Spend one token, or answer how long the caller has to wait for the next one."""
        now = self._clock()
        elapsed = max(now - self._filled_at, 0.0)
        self._tokens = min(float(self._limit.burst), self._tokens + elapsed * self._refilled())
        self._filled_at = now

        if self._tokens >= ONE_TOKEN:
            self._tokens -= ONE_TOKEN
            return Admission(
                verdict=Verdict(
                    check=Check.RATE_LIMIT,
                    passed=True,
                    detail=(
                        f"{self._tokens:.0f} of {self._limit.burst} submissions left in this burst"
                    ),
                ),
                wait_seconds=0.0,
            )

        wait = (ONE_TOKEN - self._tokens) / self._refilled()
        return Admission(
            verdict=Verdict(
                check=Check.RATE_LIMIT,
                passed=False,
                detail=(
                    f"the burst of {self._limit.burst} submissions is spent, and the next token"
                    f" arrives in {wait:.2f}s"
                ),
            ),
            wait_seconds=wait,
        )

    def _refilled(self) -> float:
        """How many tokens one second returns. Configuration is validated to be positive."""
        return self._limit.refill_per_second


class Limiters:
    """One bucket per caller, over a table that cannot grow without bound.

    A caller is named by whatever the boundary can see it by — the address the request arrived
    from — so buckets are per caller rather than one for everybody: a flood from one source does not
    spend another source's burst. The table is bounded, and a full table drops the caller seen least
    recently rather than growing, which costs that caller its remaining tokens and starts it on a
    full burst if it comes back. So this is a burst ceiling per caller, and not a budget that
    survives an attacker cycling through addresses; the honest reading is in the module docstring.
    """

    def __init__(
        self,
        limit: RateLimit,
        *,
        keys: int = DEFAULT_KEYS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._limit = limit
        self._keys = keys
        self._clock = clock
        self._buckets: OrderedDict[str, Bucket] = OrderedDict()

    def take(self, caller: str) -> Admission:
        """Spend one of this caller's tokens, adding its bucket if it has none or lost it."""
        bucket = self._buckets.get(caller)
        if bucket is None:
            bucket = self._buckets[caller] = Bucket(self._limit, clock=self._clock)
            while len(self._buckets) > self._keys:
                self._buckets.popitem(last=False)
        else:
            self._buckets.move_to_end(caller)
        return bucket.take()
