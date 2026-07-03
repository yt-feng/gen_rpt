"""
Concurrent report-generation pipeline for the DeepSeek API.

Usage:
    export DEEPSEEK_API_KEY="sk-..."
    python pipeline.py --jobs jobs.json --db reports.db --out reports/

jobs.json format:
[
  {"id": "report-0001", "company": "Acme Corp", "data": "...whatever per-report data..."},
  ...
]

Design notes:
- system_prompt is IDENTICAL across all jobs (your report template/instructions).
  Keeping it byte-for-byte identical is what lets DeepSeek's automatic context
  caching kick in -- cached input tokens are billed at a fraction of the normal
  rate, and it's on by default, no special API flag needed.
- Concurrency is controlled by AdaptiveLimiter (see limiter.py), not a fixed
  worker count, because DeepSeek's real ceiling moves with their server load.
- Every job's outcome is persisted to SQLite immediately (see store.py), so
  killing the process and restarting resumes cleanly.
"""

import argparse
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

from openai import AsyncOpenAI, APIStatusError, APITimeoutError, APIConnectionError

from limiter import AdaptiveLimiter
from store import JobStore

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

MAX_ATTEMPTS = 6
BASE_BACKOFF = 1.0      # seconds
MAX_BACKOFF = 90.0


def build_system_prompt() -> str:
    """
    Your report template / instructions go here. Keep this function returning
    an IDENTICAL string for every job -- that's the whole trick for hitting
    DeepSeek's context cache discount.
    """
    return (
        "You are a financial analyst producing structured company reports. "
        "Follow this exact structure: 1) Executive Summary, 2) Financial "
        "Overview, 3) Risk Factors, 4) Outlook. Be precise, cite figures "
        "given in the input, and do not speculate beyond the provided data."
        # ... your full, real template goes here ...
    )


def build_user_prompt(job_data: dict) -> str:
    """The part that actually varies per report -- keep the volatile,
    per-company data here, not in the system prompt."""
    return f"Generate the report for: {job_data.get('company')}\n\nData:\n{job_data.get('data')}"


async def call_deepseek(client: AsyncOpenAI, system_prompt: str, user_prompt: str) -> str:
    """Single API call with retry + exponential backoff + jitter on 429/503/timeouts."""
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = await client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=2000,   # tune to what a real report actually needs --
                                    # smaller max_tokens = shorter-held concurrency slot
                temperature=0.3,
            )
            return resp.choices[0].message.content, "ok"

        except APIStatusError as e:
            is_throttle = e.status_code in (429, 503)
            if attempt >= MAX_ATTEMPTS:
                raise
            delay = min(MAX_BACKOFF, BASE_BACKOFF * (2 ** (attempt - 1))) + random.uniform(0, 1)
            await asyncio.sleep(delay)
            # signal caller whether this was a "back off hard" event
            if is_throttle and attempt == MAX_ATTEMPTS - 1:
                pass
            continue

        except (APITimeoutError, APIConnectionError):
            if attempt >= MAX_ATTEMPTS:
                raise
            delay = min(MAX_BACKOFF, BASE_BACKOFF * (2 ** (attempt - 1))) + random.uniform(0, 1)
            await asyncio.sleep(delay)
            continue


async def worker(name: str, client: AsyncOpenAI, queue: asyncio.Queue,
                  limiter: AdaptiveLimiter, store: JobStore, stats: dict):
    while True:
        job = await queue.get()
        if job is None:
            queue.task_done()
            return

        await limiter.acquire()
        store.mark_running(job["id"])
        try:
            report, _ = await call_deepseek(client, job["system_prompt"], job["user_prompt"])
            store.mark_done(job["id"], report)
            stats["done"] += 1
            await limiter.report_success()
        except APIStatusError as e:
            hard = e.status_code in (429, 503)
            await limiter.report_failure(hard=hard)
            store.mark_failed(job["id"], f"HTTP {e.status_code}: {e}")
            stats["failed"] += 1
        except Exception as e:  # noqa: BLE001 -- last-resort catch so one bad job never kills the run
            await limiter.report_failure(hard=False)
            store.mark_failed(job["id"], str(e))
            stats["failed"] += 1
        finally:
            await limiter.release()
            queue.task_done()


async def progress_reporter(store: JobStore, limiter: AdaptiveLimiter, total: int, stop_event: asyncio.Event):
    start = time.time()
    while not stop_event.is_set():
        counts = store.counts()
        done = counts.get("done", 0)
        failed = counts.get("failed", 0)
        elapsed = time.time() - start
        rate = done / elapsed * 60 if elapsed > 0 else 0  # reports/min
        eta_min = (total - done - failed) / rate if rate > 0 else float("inf")
        print(f"[{elapsed/60:5.1f}m] done={done} failed={failed} "
              f"in_flight={limiter.in_flight} limit={limiter.current_limit} "
              f"rate={rate:.1f}/min eta={eta_min:.1f}min", flush=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass


async def run(jobs_path: str, db_path: str, out_dir: str, worker_pool_size: int,
              initial_concurrency: int, max_concurrency: int):
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: set DEEPSEEK_API_KEY", file=sys.stderr)
        sys.exit(1)

    store = JobStore(db_path)

    # Seed jobs (idempotent -- safe on resume, existing IDs are skipped)
    with open(jobs_path) as f:
        raw_jobs = json.load(f)
    system_prompt = build_system_prompt()
    seed_rows = [
        {
            "id": j["id"],
            "system_prompt": system_prompt,
            "user_prompt": build_user_prompt(j),
        }
        for j in raw_jobs
    ]
    store.seed(seed_rows)

    pending = store.get_pending()
    total = len(raw_jobs)
    print(f"Total jobs: {total} | Pending this run: {len(pending)} "
          f"(already done/failed from a prior run are skipped)")

    if not pending:
        print("Nothing to do.")
    else:
        client = AsyncOpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        limiter = AdaptiveLimiter(initial=initial_concurrency, max_limit=max_concurrency)

        queue: asyncio.Queue = asyncio.Queue()
        for job in pending:
            queue.put_nowait(job)
        for _ in range(worker_pool_size):
            queue.put_nowait(None)  # sentinel to stop each worker

        stats = {"done": 0, "failed": 0}
        stop_event = asyncio.Event()
        reporter_task = asyncio.create_task(progress_reporter(store, limiter, total, stop_event))

        workers = [
            asyncio.create_task(worker(f"w{i}", client, queue, limiter, store, stats))
            for i in range(worker_pool_size)
        ]

        await queue.join()
        stop_event.set()
        await reporter_task
        for w in workers:
            w.cancel()

    # Export finished reports to individual files
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for row in store.export_done():
        Path(out_dir, f"{row['id']}.md").write_text(row["report"] or "")

    counts = store.counts()
    print(f"Final: {counts}")
    if counts.get("failed"):
        print("Failed jobs are still marked 'failed' in the DB -- re-run the "
              "script to retry them (they'll be picked up as 'pending' if you "
              "flip their status, or extend this script to auto-requeue failures).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", required=True, help="Path to jobs.json")
    parser.add_argument("--db", default="reports.db")
    parser.add_argument("--out", default="reports/")
    parser.add_argument("--worker-pool-size", type=int, default=80,
                         help="Number of worker coroutines spawned. This is an "
                              "UPPER BOUND on parallelism, not a target -- the "
                              "AdaptiveLimiter decides how many run at once.")
    parser.add_argument("--initial-concurrency", type=int, default=10)
    parser.add_argument("--max-concurrency", type=int, default=80)
    args = parser.parse_args()

    asyncio.run(run(args.jobs, args.db, args.out, args.worker_pool_size,
                     args.initial_concurrency, args.max_concurrency))
