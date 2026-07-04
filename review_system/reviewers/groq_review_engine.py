"""
review_system/reviewers/groq_review_engine.py

Groq API client with retry/backoff and model fallback.
All Groq HTTP calls go through this class — nothing else touches the API directly.
"""
import json
import time
from typing import Any, Dict, List, Optional

import requests

from review_system.config.review_config import (
    GROQ_API_URL,
    GROQ_DEFAULT_MODEL,
    GROQ_FALLBACK_MODEL,
    GROQ_REQUEST_TIMEOUT,
    GROQ_MAX_RETRIES,
    GROQ_RATE_LIMIT_BASE_WAIT,
)
from review_system.utils.logging_utils import get_run_logger, get_error_logger

log = get_run_logger()
err_log = get_error_logger()


class GroqReviewEngine:
    """
    Thin Groq REST client with:
    - Automatic JSON mode (response_format: json_object)
    - Exponential backoff on 429 rate-limit errors
    - Automatic fallback to GROQ_FALLBACK_MODEL on model 400 errors
    - Retry on transient network/parse errors
    """

    def __init__(self, api_key: str, model: str = GROQ_DEFAULT_MODEL):
        self.api_key = api_key
        self.model = model
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_retries: int = GROQ_MAX_RETRIES,
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
                    GROQ_API_URL,
                    headers=self._headers,
                    json=payload,
                    timeout=GROQ_REQUEST_TIMEOUT,
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
                            log.info("Groq Retry-After header specifies wait: %.1fs (attempt %d)", wait - 2.0, attempt + 1)
                        except ValueError:
                            wait = GROQ_RATE_LIMIT_BASE_WAIT * (2 ** attempt)
                    else:
                        wait = GROQ_RATE_LIMIT_BASE_WAIT * (2 ** attempt)
                    
                    import random
                    jitter = random.uniform(1.0, 12.0)
                    wait += jitter
                    log.warning(
                        "Rate-limited (429). Waiting %.1fs (includes %.1fs jitter) (attempt %d)",
                        wait, jitter, attempt + 1
                    )
                    time.sleep(wait)
                    last_exc = e

                elif status == 400 and "model" in str(e).lower():
                    log.warning(
                        "Model %r unavailable, falling back to %r",
                        self.model, GROQ_FALLBACK_MODEL,
                    )
                    self.model = GROQ_FALLBACK_MODEL
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
            f"Groq API failed after {max_retries} attempts. Last: {last_exc}"
        )
