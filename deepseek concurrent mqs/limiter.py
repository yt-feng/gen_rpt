"""
Adaptive concurrency limiter (AIMD: Additive Increase, Multiplicative Decrease).

DeepSeek does not publish a fixed RPM/concurrency tier -- their own docs describe
the limit as dynamic, based on server load. So instead of hardcoding "40 workers",
we spawn a generous pool of worker coroutines and let this limiter throttle how
many are actually allowed to be in-flight at once. It grows the budget slowly on
sustained success and cuts it hard the moment DeepSeek pushes back (429/503),
which is the standard, well-behaved way to talk to an API with an undocumented cap.
"""

import asyncio
import time


class AdaptiveLimiter:
    def __init__(self, initial: int = 10, min_limit: int = 2, max_limit: int = 80,
                 grow_every_n_successes: int = 5):
        self._limit = initial
        self._min = min_limit
        self._max = max_limit
        self._in_flight = 0
        self._consecutive_successes = 0
        self._grow_every_n = grow_every_n_successes
        self._cond = asyncio.Condition()
        self.last_change_reason = "init"
        self.last_change_time = time.time()

    async def acquire(self):
        async with self._cond:
            while self._in_flight >= self._limit:
                await self._cond.wait()
            self._in_flight += 1

    async def release(self):
        async with self._cond:
            self._in_flight -= 1
            self._cond.notify_all()

    async def report_success(self):
        async with self._cond:
            self._consecutive_successes += 1
            if self._consecutive_successes >= self._grow_every_n and self._limit < self._max:
                self._limit += 1
                self._consecutive_successes = 0
                self.last_change_reason = f"grew to {self._limit}"
                self.last_change_time = time.time()
            self._cond.notify_all()

    async def report_failure(self, hard: bool = True):
        """hard=True for 429/503 (back off aggressively). hard=False for generic
        errors we don't want to over-react to (e.g. a single timeout)."""
        async with self._cond:
            self._consecutive_successes = 0
            if hard:
                new_limit = max(self._min, self._limit // 2)
                if new_limit != self._limit:
                    self._limit = new_limit
                    self.last_change_reason = f"backed off to {self._limit}"
                    self.last_change_time = time.time()
            self._cond.notify_all()

    @property
    def current_limit(self) -> int:
        return self._limit

    @property
    def in_flight(self) -> int:
        return self._in_flight
