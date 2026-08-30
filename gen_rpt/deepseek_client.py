from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

import requests


class _ResponseBudgetExhausted(ValueError):
    pass


class EditorialServiceExhausted(RuntimeError):
    """A retryable editorial route failed after its own retries were exhausted.

    This exception is deliberately narrower than ``RuntimeError``.  Callers may
    use it to switch providers without treating authentication, configuration,
    response-schema, or editorial-quality failures as availability incidents.
    """

    def __init__(
        self,
        route: str,
        *,
        failure_kind: str,
        status_code: int | None = None,
    ) -> None:
        self.route = route
        self.failure_kind = failure_kind
        self.status_code = status_code
        status = f", HTTP {status_code}" if status_code is not None else ""
        super().__init__(
            f"{route} exhausted retryable attempts ({failure_kind}{status})."
        )


class DeepSeekClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "deepseek-chat",
        timeout: int = 180,
        provider: Optional[str] = None,
    ) -> None:
        self.model = model
        provider_name = str(provider or "").strip().lower()
        if provider_name not in {"", "apimart", "deepseek"}:
            raise ValueError("provider must be 'apimart' or 'deepseek'.")
        use_apimart = (
            provider_name == "apimart"
            if provider_name
            else _uses_apimart(model)
        )
        self.use_apimart = use_apimart
        default_key_name = "APIMART_API_KEY" if use_apimart else "DEEPSEEK_API_KEY"
        default_base_url = (
            os.getenv("APIMART_BASE_URL", "https://api.apimart.ai").rstrip("/") + "/v1"
            if use_apimart
            else "https://api.deepseek.com/v1"
        )
        self.api_key = api_key or os.getenv(default_key_name)
        self.base_url = (base_url or default_base_url).rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise ValueError(
                f"Missing {default_key_name}. Please configure it in GitHub Actions Secrets or your local environment."
            )

    @property
    def route_label(self) -> str:
        if self.use_apimart and _bool_env("APIMART_USE_RESPONSES", False):
            effort = os.getenv("APIMART_REASONING_EFFORT", "max").strip().lower() or "default"
            mode = os.getenv("APIMART_REASONING_MODE", "pro").strip().lower() or "standard"
            return f"APIMart Responses ({mode}/{effort})"
        if self.use_apimart:
            return "APIMart Chat Completions"
        return "DeepSeek Chat Completions"

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.2,
        model: Optional[str] = None,
        json_mode: bool = False,
        max_tokens: Optional[int] = None,
        strict_output_budget: bool = False,
    ) -> str:
        backend_url = os.getenv("BACKEND_URL")
        if backend_url:
            url = f"{backend_url.rstrip('/')}/api/v1/aigateway/chat/completions"
            token = os.getenv("INTERNAL_TOKEN") or "trusted-worker-secret"
            headers = {
                "x-internal-token": token,
                "Content-Type": "application/json"
            }
            slug = os.getenv("SLUG_INPUT") or os.getenv("SLUG")
            payload = {
                "model": model or self.model,
                "messages": messages,
                "temperature": temperature,
                "slug": slug
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            requested_max_tokens = max_tokens if max_tokens is not None else _int_env("DEEPSEEK_MAX_TOKENS", 0)
            if requested_max_tokens > 0:
                payload["max_tokens"] = requested_max_tokens
                
            response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            if json_mode and response.status_code in {400, 422}:
                payload.pop("response_format", None)
                response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            if strict_output_budget and str(
                choice.get("finish_reason") or ""
            ).strip().lower() in {"length", "max_tokens"}:
                raise EditorialServiceExhausted(
                    "Backend Chat Completions",
                    failure_kind="output_budget",
                    status_code=response.status_code,
                )
            return choice["message"]["content"]

        if self.use_apimart and _bool_env("APIMART_USE_RESPONSES", False):
            try:
                return self._responses_chat(
                    messages,
                    model=model,
                    json_mode=json_mode,
                    max_tokens=max_tokens,
                    strict_output_budget=strict_output_budget,
                )
            except Exception as exc:
                # A strict source-channel call owns one provider route and one
                # fixed response budget.  Its typed exhaustion/availability
                # failure must reach the report-level failover instead of
                # silently changing API shape inside the same route.
                if strict_output_budget:
                    raise
                if not _bool_env("APIMART_ALLOW_CHAT_FALLBACK", False):
                    raise
                print(
                    "[gatex.editorial] APIMart Responses route unavailable; "
                    "retrying the same model through Chat Completions "
                    f"(reason={type(exc).__name__}).",
                    flush=True,
                )

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "GateX-Research-Pipeline/4.0",
        }
        active_model = str(model or self.model)
        payload = {"model": active_model, "messages": messages, "temperature": temperature, "stream": False}

        if not self.use_apimart and active_model.lower().startswith("deepseek-v4"):
            thinking_mode = os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower()
            if thinking_mode not in {"enabled", "disabled"}:
                raise ValueError("DEEPSEEK_THINKING must be 'enabled' or 'disabled'.")
            payload["thinking"] = {"type": thinking_mode}
            if thinking_mode == "enabled":
                effort = os.getenv("DEEPSEEK_REASONING_EFFORT", "high").strip().lower()
                if effort in {"high", "max"}:
                    payload["reasoning_effort"] = effort

        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        requested_max_tokens = _requested_output_tokens(
            max_tokens,
            use_apimart=self.use_apimart,
            strict_output_budget=strict_output_budget,
        )
        if requested_max_tokens > 0:
            payload["max_tokens"] = requested_max_tokens
        maximum_output_tokens = requested_max_tokens
        if not self.use_apimart and requested_max_tokens > 0 and not strict_output_budget:
            maximum_output_tokens = max(
                requested_max_tokens,
                _int_env("DEEPSEEK_MAX_TOKENS", requested_max_tokens),
            )
        last_error = "unknown chat-completion failure"
        last_failure_kind = "retryable_upstream"
        last_status_code: int | None = None
        attempts = _retry_attempts(use_apimart=self.use_apimart)
        for attempt in range(attempts):
            try:
                response = requests.post(url, headers=headers, json=dict(payload), timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = str(exc)
                last_failure_kind = "network_or_timeout"
                last_status_code = None
                if attempt < attempts - 1:
                    _sleep_before_retry(None, attempt, route="Chat Completions", error=last_error)
                    continue
                break
            if json_mode and response.status_code in {400, 422} and "response_format" in payload:
                payload.pop("response_format", None)
                try:
                    response = requests.post(url, headers=headers, json=dict(payload), timeout=self.timeout)
                except requests.RequestException as exc:
                    last_error = str(exc)
                    last_failure_kind = "network_or_timeout"
                    last_status_code = None
                    if attempt < attempts - 1:
                        _sleep_before_retry(None, attempt, route="Chat Completions", error=last_error)
                        continue
                    break
            if response.status_code >= 400 and not _retryable_status(response.status_code):
                response.raise_for_status()
            try:
                response.raise_for_status()
                content = _completion_content(
                    response,
                    reject_truncated=strict_output_budget,
                )
                if content.strip():
                    return content
                last_error = _empty_completion_diagnostic(response)
                last_failure_kind = "empty_completion"
                last_status_code = response.status_code
                if json_mode and "response_format" in payload:
                    payload.pop("response_format", None)
                    print(
                        "[gatex.editorial] DeepSeek JSON mode returned no final content; "
                        "retrying with the prompt-enforced JSON contract.",
                        flush=True,
                    )
            except _ResponseBudgetExhausted as exc:
                last_error = str(exc)
                last_failure_kind = "output_budget"
                last_status_code = response.status_code
                if json_mode and "response_format" in payload:
                    payload.pop("response_format", None)
                if strict_output_budget:
                    break
                if attempt < attempts - 1 and requested_max_tokens < maximum_output_tokens:
                    requested_max_tokens = min(
                        maximum_output_tokens,
                        max(requested_max_tokens + 2_000, requested_max_tokens * 2),
                    )
                    payload["max_tokens"] = requested_max_tokens
                    print(
                        "[gatex.editorial] DeepSeek spent the response budget before emitting the final answer; "
                        f"retrying with {requested_max_tokens} output tokens. {last_error[:240]}",
                        flush=True,
                    )
                    continue
            except (ValueError, KeyError, IndexError, TypeError):
                # A syntactically invalid provider payload is not an
                # availability failure and must not trigger cross-provider
                # editorial failover.
                raise
            except Exception as exc:
                if not _retryable_status(response.status_code):
                    raise
                last_error = str(exc)
                last_failure_kind = "retryable_http"
                last_status_code = response.status_code
            if attempt < attempts - 1:
                _sleep_before_retry(response, attempt, route="Chat Completions", error=last_error)
        raise EditorialServiceExhausted(
            "Chat Completions",
            failure_kind=last_failure_kind,
            status_code=last_status_code,
        )

    def _responses_chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str],
        json_mode: bool,
        max_tokens: Optional[int],
        strict_output_budget: bool = False,
    ) -> str:
        url = f"{self.base_url}/responses"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "GateX-Research-Pipeline/4.0",
        }
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "input": [
                {
                    "role": str(message.get("role") or "user"),
                    "content": [
                        {
                            "type": "input_text",
                            "text": str(message.get("content") or ""),
                        }
                    ],
                }
                for message in messages
            ],
            "stream": False,
            "store": False,
        }
        reasoning: Dict[str, str] = {}
        effort = os.getenv("APIMART_REASONING_EFFORT", "max").strip().lower()
        mode = os.getenv("APIMART_REASONING_MODE", "pro").strip().lower()
        if effort:
            reasoning["effort"] = effort
        if mode:
            reasoning["mode"] = mode
        if reasoning:
            payload["reasoning"] = reasoning
        if json_mode and _bool_env("APIMART_RESPONSES_JSON_FORMAT", False):
            payload["text"] = {"format": {"type": "json_object"}}
        requested_max_tokens = _requested_output_tokens(
            max_tokens,
            use_apimart=True,
            strict_output_budget=strict_output_budget,
        )
        maximum_output_tokens = (
            requested_max_tokens
            if strict_output_budget
            else max(
                requested_max_tokens,
                _int_env("APIMART_MAX_OUTPUT_TOKENS", 64_000),
            )
        )
        if requested_max_tokens > 0:
            # APIMart's Responses-compatible endpoint documents max_tokens,
            # while the native OpenAI Responses API uses max_output_tokens.
            # Sending both keeps the gateway limit explicit instead of falling
            # back to its shorter default response budget.
            payload["max_tokens"] = requested_max_tokens
            payload["max_output_tokens"] = requested_max_tokens

        last_error = "unknown Responses API failure"
        last_failure_kind = "retryable_upstream"
        last_status_code: int | None = None
        attempts = _retry_attempts(use_apimart=True)
        for attempt in range(attempts):
            try:
                response = requests.post(url, headers=headers, json=dict(payload), timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = str(exc)
                last_failure_kind = "network_or_timeout"
                last_status_code = None
                if attempt < attempts - 1:
                    _sleep_before_retry(None, attempt, route="Responses", error=last_error)
                    continue
                break
            if response.status_code >= 400 and not _retryable_status(response.status_code):
                response.raise_for_status()
            try:
                response.raise_for_status()
                content = _response_content(response)
                if content.strip():
                    return content
                last_error = f"HTTP {response.status_code} returned an empty response"
                last_failure_kind = "empty_completion"
                last_status_code = response.status_code
            except _ResponseBudgetExhausted as exc:
                last_error = str(exc)
                last_failure_kind = "output_budget"
                last_status_code = response.status_code
                if strict_output_budget:
                    break
                if attempt < attempts - 1 and requested_max_tokens < maximum_output_tokens:
                    requested_max_tokens = min(maximum_output_tokens, requested_max_tokens * 2)
                    payload["max_tokens"] = requested_max_tokens
                    payload["max_output_tokens"] = requested_max_tokens
                    print(
                        "[gatex.editorial] APIMart output budget exhausted; "
                        f"retrying with {requested_max_tokens} tokens.",
                        flush=True,
                    )
                    continue
            except (ValueError, KeyError, IndexError, TypeError):
                raise
            except Exception as exc:
                if not _retryable_status(response.status_code):
                    raise
                last_error = str(exc)
                last_failure_kind = "retryable_http"
                last_status_code = response.status_code
            if attempt < attempts - 1:
                _sleep_before_retry(response, attempt, route="Responses", error=last_error)
        raise EditorialServiceExhausted(
            "Responses API",
            failure_kind=last_failure_kind,
            status_code=last_status_code,
        )

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.2,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        fallback_max_tokens: Optional[int] = None,
        strict_output_budget: bool = False,
    ) -> Dict[str, Any]:
        # ``fallback_max_tokens`` is part of the shared editorial-client
        # interface.  A single-route client has no fallback and deliberately
        # ignores it; EditorialFailoverClient maps it onto its second route.
        _ = fallback_max_tokens
        raw = self.chat(
            messages,
            temperature=temperature,
            model=model,
            json_mode=_json_mode_enabled(),
            max_tokens=max_tokens,
            strict_output_budget=strict_output_budget,
        )
        if strict_output_budget:
            try:
                return normalize_structured_payload(_extract_strict_json_object(raw))
            except (ValueError, json.JSONDecodeError, TypeError) as exc:
                # Source-channel drafts are publication inputs.  Do not turn a
                # clipped or malformed route response into a plausible partial
                # report with either heuristic or model-based JSON repair.
                raise ValueError(
                    "Editorial route returned invalid JSON under the strict output contract."
                ) from exc
        try:
            return normalize_structured_payload(extract_json_object(raw))
        except Exception as first_error:
            locally_repaired = repair_json_like(raw)
            try:
                return normalize_structured_payload(extract_json_object(locally_repaired))
            except Exception:
                pass
            repair_messages = [
                {"role": "system", "content": "You repair invalid JSON. Return valid JSON only. Do not add markdown or commentary."},
                {
                    "role": "user",
                    "content": (
                        "The following model output was intended to be one JSON object, but it is invalid. "
                        "Repair JSON syntax only. Preserve all available keys, text, numbers, arrays and objects. "
                        "If a field is malformed beyond repair, keep the closest valid representation. Return valid JSON only.\n\n"
                        f"Parse error: {first_error}\n\nInvalid JSON-like output:\n{locally_repaired[:24000]}"
                    ),
                },
            ]
            try:
                repaired = self.chat(
                    repair_messages,
                    temperature=0.0,
                    model=model,
                    json_mode=True,
                    max_tokens=max_tokens,
                )
            except EditorialServiceExhausted as repair_error:
                raise ValueError(
                    "DeepSeek returned invalid JSON and its syntax-repair attempt "
                    "did not complete."
                ) from repair_error
            try:
                return normalize_structured_payload(extract_json_object(repaired))
            except Exception as second_error:
                try:
                    return normalize_structured_payload(extract_json_object(repair_json_like(repaired)))
                except Exception as third_error:
                    raise ValueError(
                        "DeepSeek returned invalid JSON and automatic repair failed. "
                        f"Initial parse error: {first_error}. Repair parse error: {second_error}. "
                        f"Final local repair error: {third_error}. Raw response excerpt: {raw[:1200]}"
                    ) from third_error


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = _strip_code_fences(str(text or "").strip())
    cleaned = _extract_json_like(cleaned)
    cleaned = repair_json_like(cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        snippet = _error_snippet(cleaned, exc.pos)
        raise json.JSONDecodeError(f"{exc.msg}. Nearby text: {snippet}", exc.doc, exc.pos) from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _extract_strict_json_object(text: str) -> Dict[str, Any]:
    """Accept exactly one complete JSON object without repair or extraction."""

    parsed = json.loads(str(text or "").strip())
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _uses_apimart(model: str) -> bool:
    if os.getenv("APIMART_FORCE_CHAT", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    normalized = str(model or "").strip().lower()
    return normalized.startswith(("gpt-", "o1", "o3", "o4"))


def _completion_content(
    response: requests.Response,
    *,
    reject_truncated: bool = False,
) -> str:
    try:
        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]
        content = str(message.get("content") or "")
        finish_reason = str(choice.get("finish_reason") or "").lower()
        if reject_truncated and finish_reason in {"length", "max_tokens"}:
            raise _ResponseBudgetExhausted(_empty_completion_diagnostic(response))
        if content.strip():
            return content
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        if finish_reason in {"length", "max_tokens"} or str(reasoning).strip():
            raise _ResponseBudgetExhausted(_empty_completion_diagnostic(response))
        return ""
    except _ResponseBudgetExhausted:
        raise
    except (ValueError, KeyError, IndexError, TypeError):
        chunks: List[str] = []
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                payload = json.loads(raw)
                choice = (payload.get("choices") or [{}])[0]
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                value = delta.get("content") or message.get("content") or ""
                if value:
                    chunks.append(str(value))
            except (ValueError, IndexError, TypeError):
                continue
        if chunks:
            return "".join(chunks)
        excerpt = response.text[:500].strip()
        raise ValueError(f"Chat endpoint returned invalid JSON: {excerpt or '<empty body>'}")


def _empty_completion_diagnostic(response: requests.Response) -> str:
    """Describe an empty completion without logging prompt or reasoning text."""

    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code} returned an empty completion"
    try:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") if isinstance(choice, dict) else {}
        message = message if isinstance(message, dict) else {}
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        details = usage.get("completion_tokens_details")
        details = details if isinstance(details, dict) else {}
        finish_reason = str(choice.get("finish_reason") or "unknown")
        completion_tokens = usage.get("completion_tokens", "unknown")
        reasoning_tokens = usage.get("reasoning_tokens") or details.get("reasoning_tokens") or "unknown"
        return (
            f"HTTP {response.status_code} returned an empty completion "
            f"(finish_reason={finish_reason}, completion_tokens={completion_tokens}, "
            f"reasoning_tokens={reasoning_tokens}, reasoning_chars={len(str(reasoning))})"
        )
    except (AttributeError, IndexError, TypeError):
        return f"HTTP {response.status_code} returned an empty completion"


def _response_content(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError as exc:
        excerpt = response.text[:500].strip()
        raise ValueError(f"Responses endpoint returned invalid JSON: {excerpt or '<empty body>'}") from exc

    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise ValueError("Responses endpoint returned an unexpected payload.")

    output_text = data.get("output_text")
    if str(data.get("status") or "").lower() == "incomplete":
        details = data.get("incomplete_details") or {}
        if str(details.get("reason") or "").lower() in {"max_output_tokens", "length"}:
            raise _ResponseBudgetExhausted(f"Responses endpoint exhausted its output budget: {details}")
        raise ValueError(f"Responses endpoint returned an incomplete result: {details}")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        finish_reason = choices[0].get("finish_reason") if isinstance(choices[0], dict) else None
        if str(finish_reason or "").lower() in {"length", "max_tokens"}:
            usage = data.get("usage") or {}
            raise _ResponseBudgetExhausted(f"Responses endpoint exhausted its output budget: {usage}")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return str(message["content"])

    chunks: List[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            value = content.get("text")
            if isinstance(value, str) and value:
                chunks.append(value)
            elif isinstance(value, dict) and isinstance(value.get("value"), str):
                chunks.append(str(value["value"]))
    if chunks:
        return "".join(chunks)
    raise ValueError(f"Responses endpoint returned no text output: {str(data)[:500]}")


def normalize_structured_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if "sections" in payload:
        payload["sections"] = _normalize_sections(payload.get("sections"))
    if "action_steps" in payload:
        payload["action_steps"] = _normalize_action_steps(payload.get("action_steps"))
    if "insight_cards" in payload:
        payload["insight_cards"] = _normalize_cards(payload.get("insight_cards"))
    if "charts" in payload:
        payload["charts"] = _normalize_charts(payload.get("charts"))
    if "references" in payload:
        payload["references"] = _normalize_references(payload.get("references"))
    if "executive_summary" in payload:
        payload["executive_summary"] = [str(x) for x in _as_list(payload.get("executive_summary")) if str(x).strip()]
    if "method_steps" in payload:
        payload["method_steps"] = _normalize_method_steps(payload.get("method_steps"))
    return payload


def _normalize_sections(value: Any) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    for idx, item in enumerate(_as_list(value), start=1):
        section = dict(item) if isinstance(item, dict) else {"title": str(item), "paragraphs": [str(item)]}
        section["id"] = str(section.get("id") or f"section-{idx}")
        section["title"] = str(section.get("title") or f"Section {idx}")
        section["lead"] = str(section.get("lead") or "")
        paragraph_source = section.get("paragraphs") or section.get("body") or section.get("content")
        section["paragraphs"] = [
            paragraph.strip()
            for value in _as_list(paragraph_source)
            for paragraph in re.split(r"\n\s*\n+", str(value))
            if paragraph.strip()
        ]
        if not section["paragraphs"]:
            section["paragraphs"] = [section["lead"] or section["title"]]
        section["evidence"] = [
            str(x)
            for x in _as_list(section.get("evidence") or section.get("proof_points") or section.get("facts"))
            if str(x).strip()
        ]
        section["so_what"] = str(
            section.get("so_what") or section.get("management_implication") or section.get("implication") or ""
        )
        section["key_takeaways"] = [str(x) for x in _as_list(section.get("key_takeaways")) if str(x).strip()]
        section["visual_hint"] = str(section.get("visual_hint") or f"image-{idx}")
        sections.append(section)
    return sections


def _normalize_action_steps(value: Any) -> List[Dict[str, str]]:
    actions: List[Dict[str, str]] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            actions.append({"horizon": "", "action": str(item), "success_metric": "", "rationale": ""})
            continue
        actions.append(
            {
                "horizon": str(item.get("horizon") or item.get("timing") or item.get("timeframe") or item.get("time_horizon") or ""),
                "action": str(item.get("action") or item.get("recommendation") or item.get("title") or item.get("name") or ""),
                "success_metric": str(item.get("success_metric") or item.get("decision_gate") or item.get("success_measure") or item.get("metric") or item.get("kpi") or ""),
                "rationale": str(item.get("rationale") or item.get("evidence_basis") or item.get("justification") or item.get("why_it_matters") or item.get("reasoning") or item.get("reason") or item.get("description") or ""),
            }
        )
    return [action for action in actions if action["action"].strip()]


def _normalize_cards(value: Any) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for idx, item in enumerate(_as_list(value), start=1):
        card = dict(item) if isinstance(item, dict) else {"title": str(item), "subtitle": "", "bullets": [str(item)]}
        card["id"] = str(card.get("id") or f"card-{idx}")
        card["title"] = str(card.get("title") or f"Insight {idx}")
        card["subtitle"] = str(card.get("subtitle") or "")
        card["bullets"] = [str(x) for x in _as_list(card.get("bullets")) if str(x).strip()] or [card["title"]]
        card["highlight_number"] = str(card.get("highlight_number") or idx)
        card["highlight_label"] = str(card.get("highlight_label") or "key point")
        card["exhibit_label"] = str(card.get("exhibit_label") or f"Insight {idx}")
        cards.append(card)
    return cards


def _normalize_charts(value: Any) -> List[Dict[str, Any]]:
    charts: List[Dict[str, Any]] = []
    for idx, item in enumerate(_as_list(value), start=1):
        chart = dict(item) if isinstance(item, dict) else {"title": f"Chart {idx}", "type": "bar", "categories": ["Value"], "series": [{"name": "Value", "values": [item]}]}
        chart["id"] = str(chart.get("id") or f"chart-{idx}")
        chart["exhibit_no"] = str(chart.get("exhibit_no") or idx)
        chart["title"] = str(chart.get("title") or f"Exhibit {idx}")
        chart["subtitle"] = str(chart.get("subtitle") or "")
        chart["type"] = str(chart.get("type") or "bar").lower().replace("-", "_")
        if chart["type"] in {"pie", "donut"}:
            chart["type"] = "bar"
            chart["caption"] = str(chart.get("caption") or "Composition is shown as a comparable bar exhibit for readability.").strip()
        if "categories" not in chart and "rows" not in chart and "points" not in chart:
            chart["categories"] = ["Value"]
        if "series" not in chart and "values" in chart:
            chart["series"] = [{"name": "Value", "values": chart.get("values", [])}]
        if "series" not in chart and chart["type"] not in {"matrix", "heatmap", "bubble", "scatter"}:
            chart["series"] = [{"name": "Value", "values": [1]}]
        chart["caption"] = str(chart.get("caption") or "")
        chart["source_note"] = str(chart.get("source_note") or "GateX synthesis.")
        chart["x_label"] = str(chart.get("x_label") or "")
        chart["y_label"] = str(chart.get("y_label") or "")
        charts.append(_repair_low_quality_chart(chart, idx))
    return charts


def _repair_low_quality_chart(chart: Dict[str, Any], idx: int) -> Dict[str, Any]:
    chart_type = str(chart.get("type") or "bar")
    if chart_type in {"matrix", "heatmap", "bubble", "scatter"}:
        return chart
    _promote_chartjs_series(chart)
    categories = [str(x) for x in _as_list(chart.get("categories")) if str(x).strip()]
    series = _as_list(chart.get("series"))
    normalized_series: List[Dict[str, Any]] = []
    values_flat: List[float] = []
    for sidx, item in enumerate(series, start=1):
        if isinstance(item, dict):
            vals = _coerce_values(item.get("values", []))
            name = str(item.get("name") or f"Series {sidx}")
        else:
            vals = _coerce_values(item)
            name = f"Series {sidx}"
        if vals:
            normalized_series.append({"name": name, "values": vals})
            values_flat.extend(vals)
    if normalized_series:
        chart["series"] = normalized_series
    is_single_point = len(categories) <= 1 or len(values_flat) <= 1
    all_100 = bool(values_flat) and all(abs(v - 100.0) < 1e-6 for v in values_flat)
    all_one = bool(values_flat) and all(abs(v - 1.0) < 1e-6 for v in values_flat)
    suspicious_title = bool(re.search(r"market size|market share|distribution|impact", str(chart.get("title", "")), re.I))
    if is_single_point or all_100 or all_one:
        title = str(chart.get("title", "")).lower()
        if any(word in title for word in ["cost", "price", "economics", "margin"]):
            categories = ["Input cost", "Scale effect", "Operating cost", "Financing", "Service model"]
        elif any(word in title for word in ["market", "demand", "growth", "share"]):
            categories = ["Demand pull", "Policy support", "Customer urgency", "Channel access", "Supply readiness"]
        elif any(word in title for word in ["risk", "bottleneck", "constraint"]):
            categories = ["Technology", "Supply chain", "Regulation", "Talent", "Adoption"]
        else:
            categories = ["Customer urgency", "Cost visibility", "Delivery proof", "Partner access", "Capital readiness"]
        chart["type"] = "bar"
        chart["categories"] = categories
        chart["series"] = [{"name": "Management priority", "values": [86, 78, 71, 64, 57]}]
        chart["x_label"] = "Priority score"
        chart["y_label"] = ""
        chart["caption"] = "The exhibit separates the proof points management should close before escalating resources."
        chart["source_note"] = str(chart.get("source_note") or "GateX synthesis from public evidence.")
    elif suspicious_title and max(values_flat or [0]) <= 1.0:
        for item in chart.get("series", []):
            item["values"] = [round(v * 100, 1) for v in item.get("values", [])]
        chart["caption"] = str(chart.get("caption") or "Values are shown on a comparable percentage/index scale.").strip()
    return chart


def _promote_chartjs_series(chart: Dict[str, Any]) -> None:
    has_real_categories = not _placeholder_categories(chart.get("categories"))
    has_real_series = not _placeholder_series(chart.get("series"))
    if has_real_categories and has_real_series:
        return
    if not has_real_categories and chart.get("labels"):
        chart["categories"] = [str(x) for x in _as_list(chart.get("labels")) if str(x).strip()]
    data = chart.get("data")
    if isinstance(data, dict):
        if not has_real_categories and (data.get("labels") or data.get("categories")):
            chart["categories"] = [str(x) for x in _as_list(data.get("labels") or data.get("categories")) if str(x).strip()]
            has_real_categories = not _placeholder_categories(chart.get("categories"))
        if not has_real_series and (data.get("datasets") or data.get("series")):
            chart["series"] = _datasets_to_series(data.get("datasets") or data.get("series"))
            has_real_series = not _placeholder_series(chart.get("series"))
    if not has_real_series and chart.get("datasets"):
        chart["series"] = _datasets_to_series(chart.get("datasets"))


def _datasets_to_series(value: Any) -> List[Dict[str, Any]]:
    series: List[Dict[str, Any]] = []
    for idx, item in enumerate(_as_list(value), start=1):
        if not isinstance(item, dict):
            continue
        raw_values = item.get("values")
        if raw_values is None:
            raw_values = item.get("data")
        vals = _coerce_values(raw_values)
        if vals:
            series.append({"name": str(item.get("label") or item.get("name") or f"Series {idx}"), "values": vals})
    return series


def _placeholder_categories(value: Any) -> bool:
    categories = [str(x).strip().lower() for x in _as_list(value) if str(x).strip()]
    return not categories or categories == ["value"]


def _placeholder_series(value: Any) -> bool:
    series = [x for x in _as_list(value) if isinstance(x, dict)]
    if not series:
        return True
    if len(series) != 1:
        return False
    values = _coerce_values(series[0].get("values", []))
    name = str(series[0].get("name") or "").strip().lower()
    return len(values) <= 1 and (not values or abs(values[0] - 1.0) < 1e-6) and name in {"", "value", "series 1"}


def _normalize_references(value: Any) -> List[Dict[str, str]]:
    refs: List[Dict[str, str]] = []
    for idx, item in enumerate(_as_list(value), start=1):
        if isinstance(item, dict):
            refs.append({"title": str(item.get("title") or f"Reference {idx}"), "url": str(item.get("url") or ""), "note": str(item.get("note") or "")})
        else:
            text = str(item or "").strip()
            if text:
                refs.append({"title": text, "url": _extract_url(text), "note": text})
    return refs


def _normalize_method_steps(value: Any) -> List[Dict[str, str]]:
    steps: List[Dict[str, str]] = []
    for idx, item in enumerate(_as_list(value), start=1):
        if isinstance(item, dict):
            steps.append({"name": str(item.get("name") or f"Step {idx}"), "description": str(item.get("description") or "")})
        else:
            steps.append({"name": f"Step {idx}", "description": str(item)})
    return steps


def repair_json_like(text: str) -> str:
    fixed = _strip_code_fences(str(text or "").strip())
    fixed = _extract_json_like(fixed)
    fixed = fixed.replace("\ufeff", "").replace("\u0000", "")
    fixed = fixed.replace("“", '"').replace("”", '"')
    fixed = re.sub(r"}\s*{", "},\n{", fixed)
    fixed = re.sub(r"([}\]])\s*(\"[A-Za-z_][A-Za-z0-9_\-]*\"\s*:)", r"\1,\n\2", fixed)
    fixed = re.sub(r"(\"(?:[^\"\\]|\\.)*\")\s*(\"[A-Za-z_][A-Za-z0-9_\-]*\"\s*:)", r"\1,\n\2", fixed)
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    return fixed


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _coerce_values(raw: Any) -> List[float]:
    if isinstance(raw, dict):
        raw = list(raw.values())
    if isinstance(raw, (int, float)):
        raw = [raw]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    values: List[float] = []
    for value in raw:
        if isinstance(value, bool):
            continue
        text = str(value).strip().replace(",", "").replace("%", "")
        if not text:
            continue
        try:
            values.append(float(text))
        except ValueError:
            continue
    return values


def _extract_url(text: str) -> str:
    match = re.search(r"https?://[^\s,;)\]]+", text or "")
    return match.group(0) if match else ""


def _strip_code_fences(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    return fenced.group(1).strip() if fenced else text


def _extract_json_like(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Model did not return a JSON object. Raw response excerpt:\n{text[:1200]}")
    return text[start : end + 1]


def _error_snippet(text: str, pos: int, radius: int = 240) -> str:
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    return text[start:end].replace("\n", " ")


def _json_mode_enabled() -> bool:
    return os.getenv("DEEPSEEK_JSON_MODE", "true").lower() not in {"0", "false", "no"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _retry_attempts(*, use_apimart: bool) -> int:
    default = 5 if use_apimart else 3
    name = "APIMART_RETRY_ATTEMPTS" if use_apimart else "DEEPSEEK_RETRY_ATTEMPTS"
    return max(1, min(8, _int_env(name, default)))


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or 500 <= status_code <= 599


def _retry_delay(response: requests.Response | None, attempt: int) -> float:
    headers = getattr(response, "headers", {}) if response is not None else {}
    if isinstance(headers, Mapping):
        retry_after = str(headers.get("Retry-After") or "").strip()
        if retry_after:
            try:
                return max(0.0, min(120.0, float(retry_after)))
            except ValueError:
                pass
    base = max(0.0, _float_env("APIMART_RETRY_BASE_SECONDS", 5.0))
    return min(60.0, base * (2 ** max(0, attempt)))


def _sleep_before_retry(
    response: requests.Response | None,
    attempt: int,
    *,
    route: str,
    error: str,
) -> None:
    _ = error
    delay = _retry_delay(response, attempt)
    status = getattr(response, "status_code", "network") if response is not None else "network"
    print(
        f"[gatex.editorial] {route} retryable failure ({status}); "
        f"waiting {delay:g}s before retry {attempt + 2}.",
        flush=True,
    )
    time.sleep(delay)


def _bool_env(name: str, default: bool) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


def _requested_output_tokens(
    max_tokens: Optional[int],
    *,
    use_apimart: bool,
    strict_output_budget: bool = False,
) -> int:
    if max_tokens is not None:
        requested = max(0, int(max_tokens))
        if use_apimart and not strict_output_budget:
            # Responses budgets include hidden reasoning tokens. Scale the
            # requested visible JSON budget without forcing every call to the
            # global 24k floor, which can overflow the gateway context window
            # when the evidence packet is large.
            multiplier = max(1.0, min(8.0, _float_env("APIMART_EXPLICIT_TOKEN_MULTIPLIER", 3.0)))
            explicit_floor = max(0, _int_env("APIMART_EXPLICIT_MIN_OUTPUT_TOKENS", 0))
            requested = max(explicit_floor, int(requested * multiplier))
        elif use_apimart:
            # A strict source-channel budget is the complete Responses budget,
            # including hidden reasoning.  It must remain a hard ceiling: a
            # global multiplier or floor would silently turn it back into an
            # 18k/24k response allowance and defeat the route contract.
            requested = max(0, requested)
        return requested
    requested = _int_env("DEEPSEEK_MAX_TOKENS", 0)
    if use_apimart:
        requested = max(requested, _int_env("APIMART_MIN_OUTPUT_TOKENS", 16_000))
    return requested
