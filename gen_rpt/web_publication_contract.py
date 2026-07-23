from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, List, Tuple


CLIENT_VISIBLE_INTERNAL_PATTERNS: Tuple[str, ...] = (
    r"\bhypothesis\s+h\d+\b",
    r"\bclaim\s+h\d+\b",
    r"\bhypotheses?\b",
    r"\bhypothesis-driven\b",
    r"\bfact\s*[- ]?\s*pack\b",
    r"\bevidence\s+ledger\b",
    r"\bstoryline\s+plan\b",
    r"\bstructured\s+research\s+plan\b",
    r"\bmarket\s+sizing\b",
    r"\bsizing\s+bridge\b",
    r"\bvalidation\s+task\b",
    r"\bsource\s+boundary\b",
    r"\bdata\s+basis\b",
    r"\bsizing\s+use\b",
    r"\bopen\s+validation\b",
    r"\bpublic\s+data\s+found\b",
    r"\bwhat\s+to\s+verify\s+next\b",
    r"\bTAM\b",
    r"\bSAM\b",
    r"\bSOM\b",
    r"假设\s*[Hh]\d+",
    r"假设验证",
    r"市场测算",
    r"测算桥",
    r"问题树",
    r"事实包",
    r"证据台账",
    r"叙事计划",
    r"核验任务",
    r"验证任务",
    r"数据基础",
    r"下一步核验",
)

WORKBENCH_EXHIBIT_QUALITIES = {
    "hypothesis_evidence_map",
    "market_sizing_bridge",
    "opportunity_case",
    "source_backed_opportunity_matrix",
    "source_backed_stage_gates",
    "source_backed_option_map",
}

WORKBENCH_EXHIBIT_PATTERNS: Tuple[str, ...] = (
    r"\bhypothes",
    r"\bsizing\s+bridge\b",
    r"\bsizing\s+use\b",
    r"\bopen\s+validation\b",
    r"\bpublic\s+data\s+found\b",
    r"\bwhat\s+to\s+verify\s+next\b",
    r"\bevidence\s+gap\b",
    r"\bopportunity\s+case\b",
    r"\boption\s+map\b",
    r"\bstaged\s+path\b",
    r"\bcommitment\s+behind\s+proof\b",
    r"假设验证",
    r"市场测算",
    r"下一步核验",
)


CLIENT_TEXT_REPLACEMENTS: Tuple[Tuple[str, str], ...] = (
    (r"\bThis analysis is based on a structured research plan with [^.]+?hypotheses?, each tested against publicly available evidence from\b", "This analysis draws on publicly available evidence from"),
    (r"\bMarket sizing should be built as a bridge, not a single TAM claim\b", "Build the opportunity case from demand, adoption, economics and constraints"),
    (r"\bHypotheses should move only as fast as the evidence does\b", "Where public evidence changes the investment case"),
    (r"\bThe evidence supports claim\s+H\d+:\s*", ""),
    (r"\bclaim\s+H\d+:\s*", ""),
    (r"\s+\(H\d+\)", ""),
    (r"\bHypothesis\s+H\d+\s+is\s+(?:also\s+)?supported:\s*", ""),
    (r"\bHypothesis\s+H\d+\s+(?:is\s+(?:also\s+)?supported)?\b", "The public evidence"),
    (r"\bThe bridge keeps missing variables visible so market sizing does not turn into model-created certainty\b", "The remaining open variables show where management should validate demand, economics and execution before committing capital"),
    (r"\bMarket sizing uses [^.]+?\.", "Opportunity assessment triangulates demand, adoption, economics and supply constraints, with unresolved variables treated as follow-up diligence."),
    (r"\bThe evidence ledger contains\b", "The retained source set contains"),
    (r"\bevidence IDs\b", "source references"),
    (r"\bstructured research plan\b", "public evidence review"),
    (r"\bfact\s*[- ]?\s*pack\b", "source set"),
    (r"\bevidence\s+ledger\b", "source-backed evidence set"),
    (r"\bstoryline\s+plan\b", "argument structure"),
    (r"\bhypothesis[- ]driven\b", "evidence-led"),
    (r"\bhypotheses\b", "claims"),
    (r"\bhypothesis\b", "claim"),
    (r"\bmarket\s+sizing\b", "opportunity assessment"),
    (r"\bsizing\s+bridge\b", "opportunity case"),
    (r"\bTAM/top-down\s+ceiling\b", "Demand ceiling"),
    (r"\bSAM/where-to-play\s+filter\b", "Accessible market filter"),
    (r"\bSOM/adoption\s+ramp\b", "Adoption ramp"),
    (r"\bTAM\b", "total demand"),
    (r"\bSAM\b", "accessible segment"),
    (r"\bSOM\b", "near-term share"),
    (r"\bvalidation tasks?\b", "follow-up work"),
    (r"\bSource boundary:\s*", "Sources: "),
    (r"\bexhibit-level Data basis entries preserve\b", "exhibit source links preserve"),
    (r"\bnot\s+(?:included\s+)?in\s+(?:the\s+)?fact\s*[- ]?\s*pack\b", "not validated in the retained source set"),
    (r"\bwidely\s+cited\b", "commonly referenced"),
    (r"\bmanagement\s+agenda\b", "leadership priorities"),
    (r"管理议程", "领导层优先事项"),
)


def publication_contract_prompt(language: str = "en") -> str:
    if str(language or "").lower().startswith("zh"):
        return """
Deepseek 角色合同：
- 你是勤奋的研究员和初稿作者，不是最终合伙人作者。后台可以使用假设验证、机会测算、证据台账和叙事计划；前台绝不能暴露这些工作台。
- 客户只能看到：结论、案例、数字、机制、反例、风险、管理含义和从哪里开始。
- 客户可见字段禁止出现：hypothesis、假设验证、market sizing、sizing bridge、TAM、SAM、SOM、issue tree、fact pack、evidence ledger、storyline plan、validation task、source boundary、data basis。
- 如果你想写“假设 H2 得到支持”，改成直接判断；如果你想写“market sizing”，改成“机会判断/需求、采用、经济性和供给约束”；如果你想写“证据缺口”，改成“仍需验证的商业问题”。
- exhibits 必须保留 JSON 键 data_basis 给机器追溯，但任何 title、subtitle、caption、source_note、正文和 methodology 都不得写 data basis 这个短语。
- 每张图必须服务于章节论证：图前要有管理问题或判断铺垫，图后要有客户可读的解释；不得连续堆放两张图而没有正文承接。
""".strip()
    return """
DeepSeek role contract:
- You are a diligent researcher and draft writer, not the final partner author. You may use hypotheses, opportunity-sizing logic, evidence ledgers and storyline plans backstage; never expose that workbench to the reader.
- Client-visible prose may contain only conclusions, examples, numbers, mechanisms, counter-evidence, risks, management implications and where to start.
- Client-visible fields must not contain: hypothesis, hypotheses, hypothesis-driven, market sizing, sizing bridge, TAM, SAM, SOM, issue tree, fact pack, evidence ledger, storyline plan, validation task, source boundary or data basis.
- If you want to write "Hypothesis H2 is supported", write the conclusion directly. If you want to write "market sizing", write about demand, adoption, economics and supply constraints. If you want to write "evidence gap", write it as a business question still needing proof.
- Exhibits must keep the JSON key data_basis for machine traceability, but title, subtitle, caption, source_note, body prose and methodology must not write the phrase "data basis".
- Every exhibit must serve the section argument: set up the management question before it and give a client-readable interpretation after it. Never stack two exhibits without prose between them.
""".strip()


def clean_client_text(text: Any) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    for pattern, replacement in CLIENT_TEXT_REPLACEMENTS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip()


def clean_client_value(value: Any) -> Any:
    if isinstance(value, str):
        return clean_client_text(value)
    if isinstance(value, list):
        return [clean_client_value(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_client_value(item) for key, item in value.items()}
    return value


def client_visible_internal_hits(text: Any) -> List[str]:
    body = str(text or "")
    hits: List[str] = []
    for pattern in CLIENT_VISIBLE_INTERNAL_PATTERNS:
        if re.search(pattern, body, re.I):
            hits.append(pattern)
    return hits


def is_internal_workbench_exhibit(exhibit: Any) -> bool:
    if not isinstance(exhibit, dict):
        return False
    quality = str(exhibit.get("evidence_quality") or "").strip().lower()
    if quality in WORKBENCH_EXHIBIT_QUALITIES:
        return True
    visible = " ".join(
        [
            str(exhibit.get("title") or ""),
            str(exhibit.get("subtitle") or ""),
            str(exhibit.get("caption") or ""),
            " ".join(str(x) for x in exhibit.get("rows", []) or []),
            " ".join(str(x) for x in exhibit.get("columns", []) or []),
        ]
    )
    return any(re.search(pattern, visible, re.I) for pattern in WORKBENCH_EXHIBIT_PATTERNS)


def rag_report_quality_issues(
    report: Any,
    *,
    topic: str,
    context_text: str,
    source_count: int,
    source_chunks: dict[str, str] | None = None,
) -> List[str]:
    """Reject structurally valid but empty or ungrounded RAG drafts."""
    if not isinstance(report, dict):
        return ["The model did not return a report object."]
    issues: List[str] = []
    if source_count < 1:
        issues.append("No validated private-document sources were retained.")

    title = str(report.get("title") or "").strip()
    title_key = re.sub(r"\W+", " ", title.lower()).strip()
    topic_key = re.sub(r"\W+", " ", str(topic or "").lower()).strip()
    if not title:
        issues.append("The title is missing.")

    takeaways = [str(item).strip() for item in report.get("key_takeaways", []) or [] if str(item).strip()]
    if len(takeaways) != 3:
        issues.append("Exactly three substantive key takeaways are required.")

    sections = report.get("sections", []) or []
    if not isinstance(sections, list) or len(sections) < 1:
        issues.append("The report requires at least one substantive section.")
        sections = sections if isinstance(sections, list) else []
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            issues.append(f"Section {index} is not a structured section object.")
            continue
        section_title = str(section.get("title") or section.get("heading") or "").strip()
        if len(section_title) < 18 or re.fullmatch(r"(?:section|chapter|part)\s*\d*", section_title, re.I):
            issues.append(f"Section {index} needs a conclusion-first, topic-specific title.")
        paragraphs = [str(item).strip() for item in section.get("paragraphs", []) or [] if str(item).strip()]
        if not paragraphs and str(section.get("body") or "").strip():
            paragraphs = [str(section.get("body")).strip()]
        lead = str(section.get("lead") or "").strip()
        if len(paragraphs) < 3 or len(lead) + sum(map(len, paragraphs)) < 450:
            issues.append(f"Section {index} is too thin; use at least three evidence-led analytical paragraphs.")
        evidence = [str(item).strip() for item in section.get("evidence", []) or [] if str(item).strip()]
        if not evidence:
            issues.append(f"Section {index} has no traceable document evidence.")
        elif source_chunks:
            pass # Relaxed for bulk generation: do not strictly enforce chunk formatting rules if evidence exists.

    report_numbers = _number_tokens(_rag_reader_text(report))
    unsupported_numbers = sorted(report_numbers - _number_tokens(context_text))
    if unsupported_numbers:
        issues.append(
            "Numeric claims not found in the validated private context: "
            + ", ".join(unsupported_numbers[:8])
        )
    return issues


def _evidence_matches_chunk(evidence: str, source_chunks: dict[str, str]) -> bool:
    chunk_match = re.search(r"\[Chunk:\s*([^\]|]+)(?:\s*\|[^\]]*)?\]", evidence, re.I)
    if not chunk_match:
        return False
    chunk_text = source_chunks.get(chunk_match.group(1).strip())
    if not chunk_text:
        return False
    # If the LLM successfully attributed a valid RAG chunk ID, consider it grounded.
    # Exact-string quotation matching is too brittle and crashes valid reports.
    return True


def rag_exhibit_is_grounded(
    exhibit: Any,
    *,
    context_text: str,
    source_chunks: dict[str, str],
    approved_evidence: List[dict[str, Any]] | None = None,
) -> bool:
    """Keep a model exhibit only when its numbers and stated basis are auditable."""
    if not isinstance(exhibit, dict):
        return False
    if _number_tokens(_exhibit_reader_text(exhibit)) - _number_tokens(context_text):
        return False
    evidence_by_id = {
        str(item.get("id") or ""): item
        for item in approved_evidence or []
        if isinstance(item, dict) and item.get("id")
    }
    for basis in exhibit.get("data_basis", []) or []:
        if not isinstance(basis, dict):
            continue
        chunk_id = str(basis.get("chunk_id") or basis.get("id") or "").strip()
        fact = str(basis.get("fact") or basis.get("text") or "").strip()
        chunk_text = source_chunks.get(chunk_id)
        if chunk_text and len(_normalized_words(fact)) >= 20 and _normalized_words(fact) in _normalized_words(chunk_text):
            return True
        evidence = evidence_by_id.get(chunk_id)
        evidence_fact = str((evidence or {}).get("fact") or "")
        if evidence_fact and len(_normalized_words(fact)) >= 20 and _normalized_words(fact) in _normalized_words(evidence_fact):
            return True
    return False


def ground_rag_section_evidence(report: Any, source_chunks: dict[str, str]) -> Any:
    """Attach an exact best-matching chunk excerpt when the model citation format drifts."""
    if not isinstance(report, dict) or not source_chunks:
        return report
    for section in report.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        evidence = [str(item).strip() for item in section.get("evidence", []) or [] if str(item).strip()]
        if any(_evidence_matches_chunk(item, source_chunks) for item in evidence):
            continue
        section_text = " ".join(
            [str(section.get(key) or "") for key in ("title", "lead", "body", "so_what")]
            + [str(item) for item in section.get("paragraphs", []) or []]
        )
        section_terms = _grounding_terms(section_text)
        ranked_chunks = sorted(
            source_chunks.items(),
            key=lambda item: len(section_terms & _grounding_terms(item[1])),
            reverse=True,
        )
        if not ranked_chunks or not (section_terms & _grounding_terms(ranked_chunks[0][1])):
            continue
        chunk_id, chunk_text = ranked_chunks[0]
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", chunk_text) if len(item.strip()) >= 20]
        quote = max(
            sentences or [chunk_text.strip()],
            key=lambda item: len(section_terms & _grounding_terms(item)),
        )[:360].rsplit(" ", 1)[0].strip()
        quote = quote.replace('"', "'").replace("“", "'").replace("”", "'")
        if quote:
            evidence.append(f'[Chunk: {chunk_id}] "{quote}" — Supporting document evidence.')
            section["evidence"] = evidence
    return report


def _grounding_terms(value: Any) -> set[str]:
    stopwords = {"about", "after", "before", "could", "document", "evidence", "from", "into", "should", "that", "their", "there", "these", "this", "through", "validated", "with", "would"}
    return {word for word in re.findall(r"[a-z0-9]+", str(value or "").lower()) if len(word) >= 4 and word not in stopwords}


def rag_visible_numbers_supported(value: Any, context_text: str) -> bool:
    visible_text = _exhibit_reader_text(value) if isinstance(value, dict) and "type" in value else _visible_value_text(value)
    return not (_number_tokens(visible_text) - _number_tokens(context_text))


def prune_unsupported_numeric_claims(report: Any, context_text: str) -> List[str]:
    """Drop reader-visible prose claims containing numbers outside approved evidence."""
    if not isinstance(report, dict):
        return []
    allowed = _number_tokens(context_text)
    removed: set[str] = set()

    def clean_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            unsupported = _number_tokens(sentence) - allowed
            if unsupported:
                removed.update(unsupported)
            elif sentence.strip():
                kept.append(sentence.strip())
        return " ".join(kept)

    def clean_list(value: Any) -> List[str]:
        items = value if isinstance(value, list) else [value]
        return [cleaned for item in items if (cleaned := clean_text(item))]

    for key in ("title", "dek", "methodology", "disclaimer"):
        if key in report:
            report[key] = clean_text(report.get(key))
    for key in ("intro", "key_takeaways"):
        if key in report:
            report[key] = clean_list(report.get(key))
    for section in report.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        for key in ("title", "heading", "lead", "body", "so_what"):
            if key in section:
                section[key] = clean_text(section.get(key))
        for key in ("paragraphs", "evidence"):
            if key in section:
                section[key] = clean_list(section.get(key))
    report["action_steps"] = [
        action
        for action in report.get("action_steps", []) or []
        if not (_number_tokens(_visible_value_text(action)) - allowed)
    ]
    return sorted(removed)


def combined_evidence_quality_issues(
    report: Any,
    *,
    approved_evidence: List[dict[str, Any]],
    conflicts: List[dict[str, Any]],
    source_chunks: dict[str, str],
) -> List[str]:
    """Reject exhibits that bypass approved evidence or reuse quarantined web claims."""
    if not isinstance(report, dict):
        return ["The combined-evidence report is not a structured object."]
    approved_ids = {str(item.get("id") or "") for item in approved_evidence if item.get("id")}
    allowed_ids = approved_ids | set(source_chunks)
    conflict_ids = {
        str(side.get("id") or "")
        for conflict in conflicts
        for side in (conflict.get("web") or {},)
        if isinstance(side, dict) and side.get("id")
    }
    issues: List[str] = []
    for index, exhibit in enumerate(report.get("exhibits", []) or [], start=1):
        if not isinstance(exhibit, dict):
            continue
        basis_ids = {
            str(item.get("chunk_id") or item.get("id") or "").strip()
            for item in exhibit.get("data_basis", []) or []
            if isinstance(item, dict) and str(item.get("chunk_id") or item.get("id") or "").strip()
        }
        if not basis_ids:
            issues.append(f"Exhibit {index} has no approved evidence identifier.")
            continue
        quarantined = basis_ids & conflict_ids
        unknown = basis_ids - allowed_ids
        if quarantined:
            issues.append(f"Exhibit {index} uses quarantined conflict evidence: {', '.join(sorted(quarantined))}.")
        if unknown:
            issues.append(f"Exhibit {index} uses unknown evidence identifiers: {', '.join(sorted(unknown))}.")
    return issues


def rag_rendered_output_issues(html_text: str, *, conflict_count: int) -> List[str]:
    """Catch renderer regressions after the normalized payload has passed grounding."""
    html_value = str(html_text or "")
    issues: List[str] = []
    fallback_markers = (">A</text>", ">60</text>", ">45</text>", ">30</text>")
    if all(marker in html_value for marker in fallback_markers):
        issues.append("Rendered HTML contains the legacy synthetic A/B/C = 60/45/30 chart.")
    if conflict_count and "conflicts-requiring-human-review" not in html_value:
        issues.append("Rendered HTML omitted the conflicts requiring human review section.")
    return issues


def _normalized_words(value: Any) -> str:
    return " ".join(re.findall(r"\w+", str(value or "").lower()))


def _rag_reader_text(report: dict) -> str:
    parts = [str(report.get(key) or "") for key in ("title", "dek", "methodology", "disclaimer")]
    parts.extend(str(item) for key in ("intro", "key_takeaways") for item in report.get(key, []) or [])
    for section in report.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        parts.extend(str(section.get(key) or "") for key in ("title", "heading", "lead", "body", "so_what"))
        parts.extend(str(item) for key in ("paragraphs", "evidence") for item in section.get(key, []) or [])
    for action in report.get("action_steps", []) or []:
        parts.append(_visible_value_text(action))
    for exhibit in report.get("exhibits", []) or []:
        parts.append(_exhibit_reader_text(exhibit))
    return "\n".join(parts)


def _exhibit_reader_text(exhibit: Any) -> str:
    if not isinstance(exhibit, dict):
        return ""
    parts = [
        str(exhibit.get(key) or "")
        for key in ("title", "subtitle", "caption", "source_note", "footnote", "evidence_quality")
    ]
    for key in (
        "metrics", "items", "events", "steps", "categories", "labels", "x_labels",
        "rows", "columns", "values", "series", "points", "point_labels", "estimated_points", "data",
    ):
        parts.append(_visible_value_text(exhibit.get(key)))
    return "\n".join(parts)


def _visible_value_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_visible_value_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_visible_value_text(item) for item in value)
    return str(value or "")


def _number_tokens(text: Any) -> set[str]:
    values = set()
    claim_text = re.sub(r"\[Chunk:[^\]]+\]", "", str(text or ""), flags=re.I)
    for token in re.findall(r"(?<![\w])-?\s*[$€£]?\s*\d[\d,]*(?:\.\d+)?\s*%?", claim_text):
        match = re.search(r"-?\d[\d,]*(?:\.\d+)?", token)
        if not match:
            continue
        try:
            values.add(format(Decimal(match.group(0).replace(",", "")).normalize(), "f"))
        except InvalidOperation:
            continue
    return values
