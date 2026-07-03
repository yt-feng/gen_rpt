"""
Standalone smoke test -- exercises limiter.py + store.py + the worker loop
logic with a fake API (no network, no real DeepSeek key needed) so you can
trust the concurrency/retry/resume mechanics before pointing it at the real
API and burning tokens.
"""
import asyncio
import os
import random
import tempfile
import time

from limiter import AdaptiveLimiter
from store import JobStore


class FakeThrottlingAPI:
    """Simulates an API that throttles once concurrency gets too high,
    to prove the limiter backs off and recovers."""
    def __init__(self, throttle_above=15):
        self.throttle_above = throttle_above
        self.in_flight = 0
        self.max_seen = 0

    async def call(self):
        self.in_flight += 1
        self.max_seen = max(self.max_seen, self.in_flight)
        try:
            if self.in_flight > self.throttle_above:
                await asyncio.sleep(0.05)
                raise RuntimeError("429")
            await asyncio.sleep(random.uniform(0.05, 0.15))  # fake "generation time"
            return "fake report content"
        finally:
            self.in_flight -= 1


async def worker(queue, limiter, store, api, stats):
    while True:
        job = await queue.get()
        await limiter.acquire()
        store.mark_running(job["id"])
        try:
            report = await api.call()
            store.mark_done(job["id"], report)
            stats["done"] += 1
            await limiter.report_success()
        except RuntimeError:
            await limiter.report_failure(hard=True)
            store.mark_pending(job["id"])  # requeue instead of failing permanently
            await queue.put(job)
            stats["retried"] += 1
        finally:
            await limiter.release()
            queue.task_done()


async def main():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.remove(db_path)  # let JobStore create it fresh
    store = JobStore(db_path)
    n_jobs = 200
    jobs = [{"id": f"job-{i}", "system_prompt": "sys", "user_prompt": f"user {i}"} for i in range(n_jobs)]
    store.seed(jobs)

    api = FakeThrottlingAPI(throttle_above=15)
    limiter = AdaptiveLimiter(initial=5, min_limit=2, max_limit=40, grow_every_n_successes=3)
    queue = asyncio.Queue()
    for j in store.get_pending():
        queue.put_nowait(j)

    n_workers = 40
    stats = {"done": 0, "retried": 0}
    start = time.time()
    workers = [asyncio.create_task(worker(queue, limiter, store, api, stats)) for _ in range(n_workers)]
    await queue.join()
    for w in workers:
        w.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

    elapsed = time.time() - start
    print(f"Completed {stats['done']} jobs ({stats['retried']} retries after throttling) in {elapsed:.2f}s")
    print(f"Limiter ended at concurrency={limiter.current_limit}, API's real max concurrent seen={api.max_seen}")
    print(f"Store counts: {store.counts()}")
    assert store.counts().get("done") == n_jobs, "not all jobs completed!"
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())
