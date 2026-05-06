from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import requests


class DeepSeekClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout: int = 180,
    ) -> None:
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        if not self.api_key:
            raise ValueError(
                "Missing DEEPSEEK_API_KEY. Please configure it in GitHub Actions Secrets or your local environment."
            )

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.2,
        model: Optional[str] = None,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.2,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        raw = self.chat(messages, temperature=temperature, model=model)
        try:
            return extract_json_object(raw)
        except Exception as first_error:
            repair_messages = [
                {
                    "role": "system",
                    "content": "You repair invalid JSON. Return valid JSON only. Do not add markdown or commentary.",
                },
                {
                    "role": "user",
                    "content": (
                        "The following model output was intended to be one JSON object, but it is invalid. "
                        "Repair JSON syntax only. Preserve all available keys, text, numbers, arrays and objects. "
                        "If a field is malformed beyond repair, keep the closest valid representation. Return valid JSON only.\n\n"
                        f"Parse error: {first_error}\n\n"
                        f"Invalid JSON-like output:\n{raw}"
                    ),
                },
            ]
            repaired = self.chat(repair_messages, temperature=0.0, model=model)
            try:
                return extract_json_object(repaired)
            except Exception as second_error:
                raise ValueError(
                    "DeepSeek returned invalid JSON and automatic repair failed. "
                    f"Initial parse error: {first_error}. Repair parse error: {second_error}. "
                    f"Raw response excerpt: {raw[:1200]}"
                ) from second_error


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = _strip_code_fences(str(text or "").strip())
    cleaned = _extract_json_like(cleaned)
    cleaned = _remove_trailing_commas(cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        snippet = _error_snippet(cleaned, exc.pos)
        raise json.JSONDecodeError(f"{exc.msg}. Nearby text: {snippet}", exc.doc, exc.pos) from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _strip_code_fences(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    return fenced.group(1).strip() if fenced else text


def _extract_json_like(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Model did not return a JSON object. Raw response excerpt:\n{text[:1200]}")
    return text[start : end + 1]


def _remove_trailing_commas(text: str) -> str:
    # Safe cleanup for common model mistakes: {"a": 1,} or [1,2,]
    return re.sub(r",\s*([}\]])", r"\1", text)


def _error_snippet(text: str, pos: int, radius: int = 240) -> str:
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    return text[start:end].replace("\n", " ")
