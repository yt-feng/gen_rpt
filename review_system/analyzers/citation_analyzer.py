"""
review_system/analyzers/citation_analyzer.py

Checks source traceability, bibliography presence, and citation quality.
Returns findings: strengths and weaknesses related to citations.

In lean mode, extracts from a pre-fetched combined result (no API call).
In full mode, makes its own API call.
"""
from typing import Dict, Any, Optional, TYPE_CHECKING

from shared.report_schema import ParsedReport
from review_system.utils.logging_utils import get_run_logger

if TYPE_CHECKING:
    from review_system.reviewers.openrouter_review_engine import OpenRouterReviewEngine

log = get_run_logger()

_SYSTEM = (
    "You are a senior research auditor assessing citation quality. "
    "Evaluate ONLY what is in the report text. Return strict JSON."
)

_USER = """\
REPORT TEXT:
{report_text}

CLAIMS AUDIT:
- Total claims: {total_claims}
- Source-referenced claims: {sourced}
- Unsupported/high-risk claims: {bad_claims}

TASK: Assess citation quality across these dimensions:
1. Named sources (institutions, studies, datasets)
2. Bibliography / references section presence
3. Source diversity (academic, industry, government)
4. Traceability of specific statistics

For each dimension, produce a located finding (High/Medium/Low severity).
Cite exact section/quote from the report in every finding.

Return JSON:
{{
  "citation_strengths": [
    {{"finding": "...", "location_ref": "Location -> [<section>] | Para <N> | ...", "severity": "Low"}}
  ],
  "citation_weaknesses": [
    {{"finding": "...", "location_ref": "Location -> [<section>]", "severity": "High"}}
  ],
  "has_bibliography": <true|false>,
  "named_sources_count": <integer>
}}
"""


def run(
    engine: "OpenRouterReviewEngine",
    parsed: ParsedReport,
    claims_audit: Dict[str, Any],
    combined: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run citation analysis.
    If `combined` is provided (lean mode), extract from it — no API call.
    Otherwise, make an individual API call (full mode).
    Returns citation findings dict.
    """
    if combined is not None:
        log.info("Citation analyzer: extracting from combined result (lean mode)")
        return {
            "citation_strengths":  combined.get("citation_strengths", []),
            "citation_weaknesses": combined.get("citation_weaknesses", []),
            "has_bibliography":    combined.get("has_bibliography", False),
            "named_sources_count": combined.get("named_sources_count", 0),
        }

    log.info("Running citation analyzer (full mode)")
    report_text = parsed.as_prompt_text(max_chars=16_000)
    total = claims_audit.get("total_claims", 0)
    sourced = sum(
        1 for c in claims_audit.get("claims", [])
        if c.get("source_referenced")
    )
    bad = claims_audit.get("unsupported_count", 0) + claims_audit.get("high_risk_count", 0)

    prompt = _USER.format(
        report_text=report_text,
        total_claims=total,
        sourced=sourced,
        bad_claims=bad,
    )

    try:
        result = engine.chat_json(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.15,
        )
    except Exception as e:
        log.error("Citation analyzer failed: %s", e)
        result = {}

    return {
        "citation_strengths":  result.get("citation_strengths", []),
        "citation_weaknesses": result.get("citation_weaknesses", []),
        "has_bibliography":    result.get("has_bibliography", False),
        "named_sources_count": result.get("named_sources_count", 0),
    }
