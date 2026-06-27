from __future__ import annotations

import re
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
