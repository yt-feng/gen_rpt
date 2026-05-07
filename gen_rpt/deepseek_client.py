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
            return normalize_structured_payload(extract_json_object(raw))
        except Exception as first_error:
            locally_repaired = repair_json_like(raw)
            try:
                return normalize_structured_payload(extract_json_object(locally_repaired))
            except Exception:
                pass

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
                        f"Invalid JSON-like output:\n{locally_repaired[:24000]}"
                    ),
                },
            ]
            repaired = self.chat(repair_messages, temperature=0.0, model=model)
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


def normalize_structured_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Make model-generated report JSON safe for the downstream pipeline.

    DeepSeek sometimes returns arrays with mixed dict/string items or omits ids.
    Normalizing once at the JSON boundary prevents brittle KeyError/AttributeError
    failures later in asset generation, rendering, and backups.
    """
    if not isinstance(payload, dict):
        return {}

    if "sections" in payload:
        payload["sections"] = _normalize_sections(payload.get("sections"))
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
        if isinstance(item, dict):
            section = dict(item)
        else:
            section = {"title": str(item), "paragraphs": [str(item)]}
        section["id"] = str(section.get("id") or f"section-{idx}")
        section["title"] = str(section.get("title") or f"Section {idx}")
        section["lead"] = str(section.get("lead") or "")
        section["paragraphs"] = [str(x) for x in _as_list(section.get("paragraphs")) if str(x).strip()]
        if not section["paragraphs"]:
            section["paragraphs"] = [section["lead"] or section["title"]]
        section["key_takeaways"] = [str(x) for x in _as_list(section.get("key_takeaways")) if str(x).strip()]
        section["visual_hint"] = str(section.get("visual_hint") or f"chart-{((idx - 1) % 4) + 1}")
        sections.append(section)
    return sections


def _normalize_cards(value: Any) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for idx, item in enumerate(_as_list(value), start=1):
        if isinstance(item, dict):
            card = dict(item)
        else:
            card = {"title": str(item), "subtitle": "", "bullets": [str(item)]}
        card["id"] = str(card.get("id") or f"card-{idx}")
        card["title"] = str(card.get("title") or f"Insight {idx}")
        card["subtitle"] = str(card.get("subtitle") or "")
        card["bullets"] = [str(x) for x in _as_list(card.get("bullets")) if str(x).strip()]
        if not card["bullets"]:
            card["bullets"] = [card["title"]]
        card["highlight_number"] = str(card.get("highlight_number") or idx)
        card["highlight_label"] = str(card.get("highlight_label") or "key point")
        card["exhibit_label"] = str(card.get("exhibit_label") or f"Insight {idx}")
        cards.append(card)
    return cards


def _normalize_charts(value: Any) -> List[Dict[str, Any]]:
    charts: List[Dict[str, Any]] = []
    for idx, item in enumerate(_as_list(value), start=1):
        if isinstance(item, dict):
            chart = dict(item)
        else:
            chart = {"title": f"Chart {idx}", "type": "bar", "categories": ["Value"], "series": [{"name": "Value", "values": [item]}]}
        chart["id"] = str(chart.get("id") or f"chart-{idx}")
        chart["exhibit_no"] = str(chart.get("exhibit_no") or idx)
        chart["title"] = str(chart.get("title") or f"Exhibit {idx}")
        chart["subtitle"] = str(chart.get("subtitle") or "")
        chart["type"] = str(chart.get("type") or "bar").lower().replace("-", "_")
        if "categories" not in chart and "rows" not in chart and "points" not in chart:
            chart["categories"] = ["Value"]
        if "series" not in chart and "values" in chart:
            chart["series"] = [{"name": "Value", "values": chart.get("values", [])}]
        if "series" not in chart and chart["type"] not in {"matrix", "heatmap", "bubble", "scatter"}:
            chart["series"] = [{"name": "Value", "values": [1]}]
        chart["caption"] = str(chart.get("caption") or "")
        chart["source_note"] = str(chart.get("source_note") or "BlueOcean synthesis.")
        chart["x_label"] = str(chart.get("x_label") or "")
        chart["y_label"] = str(chart.get("y_label") or "")
        charts.append(chart)
    return charts


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
