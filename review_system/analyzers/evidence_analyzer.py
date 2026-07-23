"""
review_system/analyzers/evidence_analyzer.py

Identifies data gaps and claims made without evidence in the report.
Returns a list of DataGap findings.

In lean mode, extracts from a pre-fetched combined result (no API call).
In full mode, makes its own API call.
"""
from typing import Dict, Any, List, Optional, TYPE_CHECKING

from shared.report_schema import ParsedReport
from review_system.config.prompts import ISSUE_DETECTION_SYSTEM, ISSUE_DETECTION_USER
from review_system.config.review_config import ISSUE_DETECTION_MAX_CHARS
from review_system.utils.logging_utils import get_run_logger

if TYPE_CHECKING:
    from review_system.reviewers.openrouter_review_engine import OpenRouterReviewEngine

log = get_run_logger()


def _build_claims_summary(audit: Dict[str, Any]) -> str:
    lines = [
        f"Total claims: {audit.get('total_claims', 0)}",
        f"Supported: {audit.get('supported_count', 0)}",
        f"Partially supported: {audit.get('partially_supported_count', 0)}",
        f"Unsupported: {audit.get('unsupported_count', 0)}",
        f"High-risk: {audit.get('high_risk_count', 0)}",
        f"Speculative: {audit.get('speculative_count', 0)}",
        f"Quantification ratio: {audit.get('quantification_ratio', 0)}%",
        "",
        "High-risk and unsupported claims:",
    ]
    for c in audit.get("claims", []):
        if c.get("classification") in ("unsupported", "high_risk", "speculative"):
            lines.append(
                f"  - [{c.get('classification','?').upper()}] "
                f"{c.get('claim','')} ({c.get('location_ref','')})"
            )
    return "\n".join(lines)


def run(
    engine: "OpenRouterReviewEngine",
    parsed: ParsedReport,
    claims_audit: Dict[str, Any],
    combined: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run evidence analysis.
    If `combined` is provided (lean mode), extract from it — no API call.
    Otherwise, make an individual API call (full mode).
    Returns dict with keys: data_gaps, weak_assumptions.
    """
    if combined is not None:
        log.info("Evidence analyzer: extracting from combined result (lean mode)")
        return {
            "data_gaps":        combined.get("data_gaps", []),
            "weak_assumptions": combined.get("weak_assumptions", []),
        }

    log.info("Running evidence analyzer (full mode)")
    report_text = parsed.as_prompt_text(max_chars=ISSUE_DETECTION_MAX_CHARS)
    claims_summary = _build_claims_summary(claims_audit)

    prompt = ISSUE_DETECTION_USER.format(
        report_text=report_text,
        claims_summary=claims_summary,
    )

    try:
        result = engine.chat_json(
            messages=[
                {"role": "system", "content": ISSUE_DETECTION_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
        )
    except Exception as e:
        log.error("Evidence analyzer failed: %s", e)
        result = {}

    return {
        "data_gaps":        result.get("data_gaps", []),
        "weak_assumptions": result.get("weak_assumptions", []),
    }
