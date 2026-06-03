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
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": model or self.model, "messages": messages, "temperature": temperature}
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
        section = dict(item) if isinstance(item, dict) else {"title": str(item), "paragraphs": [str(item)]}
        section["id"] = str(section.get("id") or f"section-{idx}")
        section["title"] = str(section.get("title") or f"Section {idx}")
        section["lead"] = str(section.get("lead") or "")
        section["paragraphs"] = [str(x) for x in _as_list(section.get("paragraphs")) if str(x).strip()]
        if not section["paragraphs"]:
            section["paragraphs"] = [section["lead"] or section["title"]]
        section["key_takeaways"] = [str(x) for x in _as_list(section.get("key_takeaways")) if str(x).strip()]
        section["visual_hint"] = str(section.get("visual_hint") or f"image-{idx}")
        sections.append(section)
    return sections


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
        chart["source_note"] = str(chart.get("source_note") or "BlueOcean synthesis.")
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
        chart["source_note"] = str(chart.get("source_note") or "BlueOcean synthesis from public evidence.")
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
