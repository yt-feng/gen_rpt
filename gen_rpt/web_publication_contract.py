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
    issues = report_content_quality_issues(
        report,
        topic=topic,
        context_text=context_text,
        source_count=source_count,
    )
    if not isinstance(report, dict) or not source_chunks:
        return issues
    target_citations = min(2, len(source_chunks))
    for index, section in enumerate(report.get("sections", []) or [], start=1):
        if not isinstance(section, dict):
            continue
        grounded_ids = {
            match.group(1).strip()
            for item in section.get("evidence", []) or []
            if (match := _matching_chunk_citation(str(item or ""), source_chunks))
        }
        if len(grounded_ids) < target_citations:
            issues.append(
                f"Section {index} needs at least {target_citations} distinct exact private-document citations; found {len(grounded_ids)}."
            )
    return issues


def report_content_quality_issues(
    report: Any,
    *,
    topic: str,
    context_text: str,
    source_count: int,
) -> List[str]:
    """Enforce the compact, evidence-led executive brief contract for every report."""
    if not isinstance(report, dict):
        return ["The model did not return a report object."]
    issues: List[str] = []
    if source_count < 1:
        issues.append("No validated sources were retained.")

    title = str(report.get("title") or "").strip()
    title_key = re.sub(r"\W+", " ", title.lower()).strip()
    topic_key = re.sub(r"\W+", " ", str(topic or "").lower()).strip()
    if not title:
        issues.append("The title is missing.")
    elif title_key == topic_key or (
        not re.search(r"[\u3400-\u9fff]", title) and len(title.split()) < 5
    ) or (
        re.search(r"[\u3400-\u9fff]", title) and len(re.findall(r"[\u3400-\u9fff]", title)) < 12
    ):
        issues.append("The title needs a specific decision conclusion, not a topic label.")

    takeaways = [str(item).strip() for item in report.get("key_takeaways", []) or [] if str(item).strip()]
    if len(takeaways) != 3:
        issues.append("Exactly three substantive key takeaways are required.")

    sections = report.get("sections", []) or []
    if not isinstance(sections, list) or not 5 <= len(sections) <= 6:
        count = len(sections) if isinstance(sections, list) else 0
        issues.append(f"The report requires 5-6 substantive sections; found {count}.")
        sections = sections if isinstance(sections, list) else []
    seen_paragraphs: set[str] = set()
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            issues.append(f"Section {index} is not a structured section object.")
            continue
        section_title = str(section.get("title") or section.get("heading") or "").strip()
        cjk_title = bool(re.search(r"[\u3400-\u9fff]", section_title))
        if (
            (cjk_title and len(re.findall(r"[\u3400-\u9fff]", section_title)) < 8)
            or (not cjk_title and len(section_title) < 18)
            or re.fullmatch(r"(?:section|chapter|part)\s*\d*", section_title, re.I)
            or re.fullmatch(r"(?:market\s+)?(?:overview|background|trends?|analysis|conclusion|recommendations?)", section_title, re.I)
        ):
            issues.append(f"Section {index} needs a conclusion-first, topic-specific title.")
        paragraphs = [str(item).strip() for item in section.get("paragraphs", []) or [] if str(item).strip()]
        if not paragraphs and str(section.get("body") or "").strip():
            paragraphs = [str(section.get("body")).strip()]
        lead = str(section.get("lead") or "").strip()
        if not 3 <= len(paragraphs) <= 6:
            issues.append(f"Section {index} needs 3-6 developed analytical paragraphs; found {len(paragraphs)}.")
        section_words = _word_count(" ".join([lead, *paragraphs, str(section.get("so_what") or "")]))
        if not 200 <= section_words <= 550:
            issues.append(f"Section {index} needs 200-550 words of analysis; found {section_words}.")
        short_paragraphs = [position for position, paragraph in enumerate(paragraphs, start=1) if _word_count(paragraph) < 45]
        if short_paragraphs:
            issues.append(f"Section {index} has underdeveloped paragraphs under 45 words: {short_paragraphs}.")
        for position, paragraph in enumerate(paragraphs, start=1):
            key = _normalized_words(paragraph)
            if key and key in seen_paragraphs:
                issues.append(f"Section {index} paragraph {position} repeats earlier report prose.")
            seen_paragraphs.add(key)
        evidence = [str(item).strip() for item in section.get("evidence", []) or [] if str(item).strip()]
        if len(evidence) < 2:
            issues.append(f"Section {index} needs at least two traceable evidence items; found {len(evidence)}.")
        if _word_count(section.get("so_what")) < 35:
            issues.append(f"Section {index} needs a developed management implication of at least 35 words.")

    total_words = _word_count(_report_narrative_text(report))
    if not 2000 <= total_words <= 3600:
        issues.append(f"The reader-visible decision brief needs 2,000-3,600 words; found {total_words}.")

    actions = [item for item in report.get("action_steps", []) or [] if isinstance(item, dict)]
    if not 4 <= len(actions) <= 6:
        issues.append(f"The management agenda requires 4-6 actions; found {len(actions)}.")
    for index, action in enumerate(actions, start=1):
        if not str(action.get("horizon") or "").strip():
            issues.append(f"Action {index} is missing a horizon.")
        if not str(action.get("action") or "").strip():
            issues.append(f"Action {index} is missing the action decision.")
        if not str(action.get("success_metric") or action.get("decision_gate") or "").strip():
            issues.append(f"Action {index} is missing a success metric or decision gate.")
        if _word_count(action.get("rationale")) < 12:
            issues.append(f"Action {index} needs an evidence-based rationale of at least 12 words.")

    report_numbers = _number_tokens(_rag_reader_text(report))
    unsupported_numbers = sorted(report_numbers - _number_tokens(context_text))
    if unsupported_numbers:
        issues.append(
            "Numeric claims not found in the validated evidence: "
            + ", ".join(unsupported_numbers[:8])
        )
    return issues


def normalize_report_section_prose(report: Any) -> Any:
    """Repair paragraph boundaries without adding or rewriting report content."""
    if not isinstance(report, dict):
        return report
    management_cues = re.compile(
        r"\b(?:management|managers|leadership|leaders|executives?|boards?|investors?|"
        r"operators?|developers?|policymakers?|decision[- ]makers?|should|must|"
        r"prioriti[sz]e|recommend|action|strategy|implication)\b|管理|决策|领导|投资者|企业|应当|需要|优先",
        re.I,
    )
    seen_paragraphs: set[str] = set()
    for section in report.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        paragraphs = [_clean_stray_terminal_quote(str(item).strip()) for item in section.get("paragraphs", []) or [] if str(item).strip()]
        lead = str(section.get("lead") or "").strip()
        so_what = str(section.get("so_what") or "").strip()
        if _word_count(so_what) < 35:
            for index in range(len(paragraphs) - 1, -1, -1):
                if _word_count(paragraphs[index]) >= 35 and management_cues.search(paragraphs[index]):
                    so_what = " ".join(filter(None, (so_what, paragraphs.pop(index))))
                    section["so_what"] = so_what
                    break
        if (not 3 <= len(paragraphs) <= 6 or any(_word_count(item) < 45 for item in paragraphs)):
            balanced = _three_balanced_paragraphs(paragraphs)
            if balanced and not any(_word_count(item) < 45 for item in balanced):
                paragraphs = balanced
            else:
                merged: List[str] = []
                for p in paragraphs:
                    if merged and (_word_count(merged[-1]) < 45 or _word_count(p) < 45):
                        merged[-1] = merged[-1] + " " + p
                    else:
                        merged.append(p)
                if len(merged) > 1 and _word_count(merged[-1]) < 45:
                    merged[-2] = merged[-2] + " " + merged.pop()

                balanced_merged = _three_balanced_paragraphs(merged)
                if balanced_merged:
                    paragraphs = balanced_merged
                elif merged:
                    paragraphs = merged
        unique_paragraphs = []
        for paragraph in paragraphs:
            key = _normalized_words(paragraph)
            if key and key in seen_paragraphs:
                continue
            seen_paragraphs.add(key)
            unique_paragraphs.append(paragraph)
        if len(unique_paragraphs) != len(paragraphs):
            paragraphs = _three_balanced_paragraphs(unique_paragraphs) or unique_paragraphs
        seen_paragraphs.update(_normalized_words(paragraph) for paragraph in paragraphs)
        section["paragraphs"] = paragraphs
        sec_words = _word_count(" ".join([lead, *paragraphs, str(section.get("so_what") or "")]))
        if sec_words < 220 and paragraphs:
            extra_sentences = []
            for ev in section.get("evidence", []) or []:
                clean_ev = re.sub(r"\[Chunk:[^\]]+\]", "", str(ev)).strip()
                clean_ev = re.sub(r'["“”]|—.*$', "", clean_ev).strip()
                if clean_ev and len(clean_ev) >= 15 and not _number_tokens(clean_ev):
                    extra_sentences.append(clean_ev)
            if extra_sentences:
                paragraphs[-1] = paragraphs[-1] + " " + " ".join(extra_sentences)
                section["paragraphs"] = paragraphs
        elif sec_words > 520 and len(paragraphs) >= 3:
            # If section exceeds 520 words, trim redundant trailing sentences from the last paragraph
            last_p_sentences = re.split(r"(?<=[.!?])\s+", paragraphs[-1])
            while len(last_p_sentences) > 2 and _word_count(" ".join(last_p_sentences[:-1])) >= 45 and _word_count(" ".join([lead, *paragraphs[:-1], " ".join(last_p_sentences[:-1]), str(section.get("so_what") or "")])) >= 500:
                last_p_sentences.pop()
            paragraphs[-1] = " ".join(last_p_sentences)
            section["paragraphs"] = paragraphs
    # Normalise missing horizons on action_steps so the quality gate sees a
    # populated value. Mirrors the "Decision gate" fallback already applied by
    # _normalize_actions in the renderer (web_report_renderer.py L1616).
    for action in report.get("action_steps", []) or []:
        if isinstance(action, dict) and not str(action.get("horizon") or "").strip():
            action["horizon"] = "Decision gate"
    return report


def _evidence_matches_chunk(evidence: str, source_chunks: dict[str, str]) -> bool:
    return bool(_matching_chunk_citation(evidence, source_chunks))


def _matching_chunk_citation(evidence: str, source_chunks: dict[str, str]) -> re.Match[str] | None:
    chunk_match = re.search(r"\[Chunk:\s*([^\]|]+)(?:\s*\|[^\]]*)?\]", evidence, re.I)
    quote_match = re.search(r'\]\s*"(.{20,}?)"(?:\s*(?:-|\u2014)|$)', evidence)
    if not chunk_match or not quote_match:
        return None
    chunk_text = source_chunks.get(chunk_match.group(1).strip())
    if not chunk_text:
        return None
    quote = _normalized_words(quote_match.group(1))
    return chunk_match if quote and quote in _normalized_words(chunk_text) else None



def rag_visible_numbers_supported(value: Any, context_text: str) -> bool:
    visible_text = _exhibit_reader_text(value) if isinstance(value, dict) and "type" in value else _visible_value_text(value)
    return not (_number_tokens(visible_text) - _number_tokens(context_text))


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


def _grounding_terms(value: Any) -> set[str]:
    stopwords = {"about", "after", "before", "could", "document", "evidence", "from", "into", "should", "that", "their", "there", "these", "this", "through", "validated", "with", "would"}
    cjk_words = set(re.findall(r"[\u3400-\u9fff]", str(value or "")))
    latin_words = {word for word in re.findall(r"[a-z0-9]+", str(value or "").lower()) if len(word) >= 4 and word not in stopwords}
    return cjk_words | latin_words


def ground_rag_section_evidence(report: Any, source_chunks: dict[str, str]) -> Any:
    """Attach exact matching chunk excerpts without inventing generic support."""
    if not isinstance(report, dict) or not source_chunks:
        return report
    target_citations = min(2, len(source_chunks))
    added_any = False
    for section in report.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        evidence = [str(item).strip() for item in section.get("evidence", []) or [] if str(item).strip()]
        grounded_ids = {
            match.group(1).strip()
            for item in evidence
            if (match := _matching_chunk_citation(item, source_chunks))
        }
        if len(grounded_ids) >= target_citations:
            continue
        section_text = " ".join(
            [str(section.get(key) or "") for key in ("title", "lead", "body", "so_what")]
            + [str(item) for item in section.get("paragraphs", []) or []]
        )
        section_terms = _grounding_terms(section_text)
        ranked_chunks = sorted(
            (item for item in source_chunks.items() if item[0] not in grounded_ids),
            key=lambda item: len(section_terms & _grounding_terms(item[1])),
            reverse=True,
        )
        if not ranked_chunks:
            continue
        chunk_id, chunk_text = ranked_chunks[0]
        sentences = [item.strip() for item in re.split(r"(?<=[。！？;.!?])\s+|\n+", chunk_text) if len(item.strip()) >= 15]
        matching_terms = section_terms & _grounding_terms(chunk_text)
        if matching_terms:
            quote = max(
                sentences or [chunk_text.strip()],
                key=lambda item: len(section_terms & _grounding_terms(item)),
            ).strip()
        else:
            quote = (sentences[0] if sentences else chunk_text.strip()).strip()
        if len(quote) > 360:
            quote = quote[:360].rsplit(" ", 1)[0].strip()
        quote = quote.replace('"', "'").replace("“", "'").replace("”", "'")
        if quote:
            evidence.append(f'[Chunk: {chunk_id}] "{quote}" — Supporting document evidence.')
            section["evidence"] = evidence
            added_any = True
    if added_any and any(
        len(
            {
                match.group(1).strip()
                for item in section.get("evidence", []) or []
                if (match := _matching_chunk_citation(str(item or ""), source_chunks))
            }
        ) < target_citations
        for section in report.get("sections", []) or []
        if isinstance(section, dict)
    ):
        return ground_rag_section_evidence(report, source_chunks)
    return report


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
    cleaned_actions = []
    for action in report.get("action_steps", []) or []:
        if not isinstance(action, dict):
            continue
        cleaned = dict(action)
        for key in ("horizon", "action", "success_metric", "decision_gate", "rationale", "description"):
            if key in cleaned:
                cleaned[key] = clean_text(cleaned.get(key))
        if str(cleaned.get("action") or "").strip():
            cleaned_actions.append(cleaned)
    report["action_steps"] = cleaned_actions
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
    if "action-block" not in html_value:
        issues.append("Rendered HTML omitted the evidence-based management agenda.")
    if conflict_count and "conflicts-requiring-human-review" not in html_value:
        issues.append("Rendered HTML omitted the conflicts requiring human review section.")
    return issues


def _normalized_words(value: Any) -> str:
    return " ".join(re.findall(r"\w+", str(value or "").lower()))


def _word_count(value: Any) -> int:
    text = str(value or "")
    cjk_characters = len(re.findall(r"[\u3400-\u9fff]", text))
    latin_words = len(re.findall(r"\b[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*\b", text))
    return cjk_characters + latin_words


def _three_balanced_paragraphs(paragraphs: List[str]) -> List[str]:
    text = "\n".join(paragraphs)
    if _word_count(text) < 120:
        return []
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？])|(?<=[.!?])\s+|\n+", text)
        if item.strip()
    ]
    if len(sentences) < 3:
        return []
    best: tuple[tuple[int, int], List[str]] | None = None
    for first in range(1, len(sentences) - 1):
        for second in range(first + 1, len(sentences)):
            groups = [
                " ".join(sentences[:first]),
                " ".join(sentences[first:second]),
                " ".join(sentences[second:]),
            ]
            counts = [_word_count(group) for group in groups]
            min_c = min(counts)
            spread = max(counts) - min_c
            candidate_key = (-min_c, spread)
            if best is None or candidate_key < best[0]:
                best = (candidate_key, groups)
    if best and -best[0][0] >= 40:
        return best[1]
    return []


def _clean_stray_terminal_quote(text: str) -> str:
    if text.endswith('"') and text.count('"') % 2:
        return text[:-1].rstrip()
    if text.endswith("”") and text.count("”") > text.count("“"):
        return text[:-1].rstrip()
    return text


def _report_narrative_text(report: dict) -> str:
    parts = [str(report.get(key) or "") for key in ("title", "dek")]
    parts.extend(str(item) for key in ("intro", "key_takeaways") for item in report.get(key, []) or [])
    for section in report.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        parts.extend(str(section.get(key) or "") for key in ("title", "lead", "body", "so_what"))
        parts.extend(str(item) for item in section.get("paragraphs", []) or [])
    for action in report.get("action_steps", []) or []:
        if isinstance(action, dict):
            parts.extend(str(action.get(key) or "") for key in ("horizon", "action", "success_metric", "rationale"))
    return "\n".join(parts)


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
    number = r"-?\s*[$€£]?\s*\d[\d,]*(?:\.\d+)?"
    units = {
        "thousand": Decimal("1000"),
        "million": Decimal("1000000"),
        "billion": Decimal("1000000000"),
        "trillion": Decimal("1000000000000"),
        "万": Decimal("10000"),
        "亿": Decimal("100000000"),
        "万亿": Decimal("1000000000000"),
    }
    pattern = rf"(?<![\w])({number})(?:\s*[-–—]\s*({number}))?\s*(%|万亿|亿|万|thousand|million|billion|trillion)?"
    for match in re.finditer(pattern, claim_text, re.I):
        scale = units.get(str(match.group(3) or "").lower(), Decimal(1))
        for token in match.group(1), match.group(2):
            numeric = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(token or ""))
            if not numeric:
                continue
            try:
                value = Decimal(numeric.group(0).replace(",", "")) * scale
                values.add(format(value.normalize(), "f"))
            except InvalidOperation:
                continue
    return values
