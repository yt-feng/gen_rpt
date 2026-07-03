# DeepSeek concurrent report pipeline

Generates ~1,000 reports against the DeepSeek API well inside a 10-hour window,
without assuming a fixed rate limit (DeepSeek doesn't publish one).

## Files

- `limiter.py` — Adaptive concurrency limiter (AIMD). Grows the in-flight
  budget slowly on sustained success, halves it instantly on a 429/503. This
  replaces guessing a fixed "N workers" number.
- `store.py` — SQLite job store. Every job's status (pending/running/done/failed)
  is persisted immediately. Kill the process anytime — re-running picks up
  exactly where it left off instead of re-generating (and re-paying for)
  finished reports.
- `pipeline.py` — The orchestrator: spawns a worker pool, calls the DeepSeek
  API with retry/backoff, writes finished reports to `reports/`.
- `smoke_test.py` — Runs the whole worker/limiter/retry loop against a fake
  API that simulates throttling, so you can trust the mechanics before
  spending real tokens. Run it any time with `python3 smoke_test.py`.
- `jobs.example.json` — Shape of the input file `pipeline.py` expects.

## Setup

```bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY="sk-..."
```

## Run

```bash
python3 pipeline.py --jobs jobs.json --db reports.db --out reports/
```

Useful flags:
- `--worker-pool-size` (default 80) — upper bound on parallel coroutines.
  This is a ceiling, not a target; the adaptive limiter decides real
  parallelism moment to moment.
- `--initial-concurrency` (default 10) — where the limiter starts. Conservative
  on purpose since DeepSeek's real ceiling is unknown until you test it.
- `--max-concurrency` (default 80) — hard ceiling the limiter won't exceed
  even if things are going well, so a lucky streak doesn't overload the API
  or your own machine.

Progress prints every 15s: jobs done/failed, current in-flight count, current
adaptive limit, throughput, and ETA — watch this during your real run.

## Before the real 1,000-report run

1. **Test at small scale first.** Run with ~20-30 jobs to confirm end-to-end
   correctness and see what concurrency DeepSeek actually tolerates today.
2. **Run a load test a day or two before the deadline**, not the day of.
   DeepSeek's capacity is described as fluctuating with their overall server
   load, so today's ceiling may not be tomorrow's.
3. **Tune `max_tokens` in `pipeline.py`** to the smallest value that reliably
   fits a real report. Every request holds a concurrency slot for its full
   duration — cutting a report from 20 minutes to 8 by trimming unnecessary
   output length is worth more than any amount of concurrency tuning.
4. **Keep `build_system_prompt()` byte-for-byte identical across all jobs.**
   That's what lets DeepSeek's automatic context caching apply — cached
   input tokens bill far cheaper, and there's no code required beyond
   keeping the prefix stable.
5. **Put a firm cutoff time in your plan.** If a chunk of jobs is still
   `pending`/`failed` with 2 hours left, that's your signal to either bump
   `--max-concurrency`, split traffic across API keys/providers, or manually
   intervene — not something to discover at hour 9.

## If DeepSeek's direct API can't sustain the throughput you need

Third-party hosts (Together AI, Fireworks, Novita, OpenRouter) serve the
open-weight DeepSeek-V3 model with their own documented rate limits and, in
some cases, an actual async Batch API — worth having as a fallback path if
load testing shows DeepSeek's direct API can't reliably hit your numbers.
