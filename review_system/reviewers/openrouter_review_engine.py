"""
review_system/reviewers/openrouter_review_engine.py

OpenRouter API client with retry/backoff and model fallback.
All OpenRouter HTTP calls go through this class — nothing else touches the API directly.
"""
import json
import time
from typing import Any, Dict, List, Optional

import requests

from review_system.config.review_config import (
    OPENROUTER_API_URL,
    OPENROUTER_DEFAULT_MODEL,
    OPENROUTER_FALLBACK_MODEL,
    OPENROUTER_REQUEST_TIMEOUT,
    OPENROUTER_MAX_RETRIES,
    OPENROUTER_RATE_LIMIT_BASE_WAIT,
)
from review_system.utils.logging_utils import get_run_logger, get_error_logger

log = get_run_logger()
err_log = get_error_logger()


class OpenRouterReviewEngine:
    """
    Thin OpenRouter REST client with:
    - Automatic JSON mode (response_format: json_object)
    - Exponential backoff on 429 rate-limit errors
    - Automatic fallback to OPENROUTER_FALLBACK_MODEL on model 400 errors
    - Retry on transient network/parse errors
    """

    def __init__(self, api_key: str, model: str = OPENROUTER_DEFAULT_MODEL):
        self.api_key = api_key
        self.model = model
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer": "https://github.com/yt-feng/gen_rpt",
            "X-Title": "Gen RPT Auditor",
        }

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_retries: int = OPENROUTER_MAX_RETRIES,
    ) -> Dict[str, Any]:
        """
        Send a chat completion request and parse the JSON response.
        Retries on rate-limit and transient errors.
        Raises RuntimeError after all retries exhausted.
        """
        payload = {
            "model":           self.model,
            "messages":        messages,
            "temperature":     temperature,
            "response_format": {"type": "json_object"},
        }

        last_exc: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    OPENROUTER_API_URL,
                    headers=self._headers,
                    json=payload,
                    timeout=OPENROUTER_REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return json.loads(content)

            except requests.exceptions.HTTPError as e:
                status = getattr(resp, "status_code", 0)

                if status == 429 and attempt < max_retries - 1:
                    retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                    if retry_after:
                        try:
                            wait = float(retry_after) + 2.0
                            log.info("OpenRouter Retry-After header specifies wait: %.1fs (attempt %d)", wait - 2.0, attempt + 1)
                        except ValueError:
                            wait = OPENROUTER_RATE_LIMIT_BASE_WAIT * (2 ** attempt)
                    else:
                        wait = OPENROUTER_RATE_LIMIT_BASE_WAIT * (2 ** attempt)
                    
                    import random
                    jitter = random.uniform(1.0, 12.0)
                    wait += jitter

                    if wait > 60.0:
                        log.error("Rate-limit wait (%.1fs) exceeds 60s cap. Aborting AI review to prevent CI hang.", wait)
                        raise RuntimeError(f"OpenRouter API rate limit penalty ({wait:.1f}s) exceeded 60s cap.")

                    log.warning(
                        "Rate-limited (429). Waiting %.1fs (includes %.1fs jitter) (attempt %d)",
                        wait, jitter, attempt + 1
                    )
                    time.sleep(wait)
                    last_exc = e

                elif status == 400 and "model" in str(e).lower():
                    log.warning(
                        "Model %r unavailable, falling back to %r",
                        self.model, OPENROUTER_FALLBACK_MODEL,
                    )
                    self.model = OPENROUTER_FALLBACK_MODEL
                    payload["model"] = self.model
                    last_exc = e

                else:
                    err_log.error("HTTP error %d: %s", status, e)
                    raise

            except (json.JSONDecodeError, KeyError) as e:
                if attempt < max_retries - 1:
                    wait = 5 * (attempt + 1)
                    log.warning("Parse error: %s. Retrying in %ds", e, wait)
                    time.sleep(wait)
                    last_exc = e
                else:
                    err_log.error("Parse error after retries: %s", e)
                    raise

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait = 5 * (attempt + 1)
                    log.warning("Network error: %s. Retrying in %ds", e, wait)
                    time.sleep(wait)
                    last_exc = e
                else:
                    err_log.error("Network error after retries: %s", e)
                    raise

        raise RuntimeError(
            f"OpenRouter API failed after {max_retries} attempts. Last: {last_exc}"
        )
