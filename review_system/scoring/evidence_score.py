"""
review_system/scoring/evidence_score.py

Scores Evidence & Citations dimension — max 25 points.
Applies hard caps when bibliography is absent or unsupported claims are numerous.
"""
from typing import Dict, Any, TYPE_CHECKING

from shared.report_schema import ParsedReport
from review_system.config.review_config import (
    DIMENSION_MAX,
    EVIDENCE_SCORE_CAP_NO_BIBLIOGRAPHY,
    EVIDENCE_SCORE_CAP_MANY_UNSUPPORTED,
)
from review_system.utils.logging_utils import get_run_logger

if TYPE_CHECKING:
    from review_system.reviewers.openrouter_review_engine import OpenRouterReviewEngine

log = get_run_logger()
_DIM = "evidence_and_citations"
_MAX = DIMENSION_MAX[_DIM]


def score(
    engine: "OpenRouterReviewEngine",
    parsed: ParsedReport,
    claims_audit: Dict[str, Any],
    full_scores: Dict[str, Any],
    citation_findings: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Validate, cap, and return the evidence_and_citations dimension.
    Hard caps applied:
      - No bibliography: <= 14/25
      - >=3 unsupported/high-risk claims: <= 18/25
    """
    raw = full_scores.get(_DIM, {})
    dim_score = min(int(raw.get("score", _MAX // 2)), _MAX)

    # Apply caps
    bad_count = (
        claims_audit.get("unsupported_count", 0)
        + claims_audit.get("high_risk_count", 0)
    )
    has_bib = citation_findings.get("has_bibliography", True) if citation_findings else True

    if not has_bib:
        dim_score = min(dim_score, EVIDENCE_SCORE_CAP_NO_BIBLIOGRAPHY)
        log.info("Evidence cap applied: no bibliography -> max %d", EVIDENCE_SCORE_CAP_NO_BIBLIOGRAPHY)

    if bad_count >= 3:
        dim_score = min(dim_score, EVIDENCE_SCORE_CAP_MANY_UNSUPPORTED)
        log.info("Evidence cap applied: %d bad claims -> max %d", bad_count, EVIDENCE_SCORE_CAP_MANY_UNSUPPORTED)

    log.info("Evidence & Citations: %d/%d", dim_score, _MAX)
    return {
        "score":      dim_score,
        "max_points": _MAX,
        "what_works": raw.get("what_works") or [],
        "what_fails": raw.get("what_fails") or [],
    }
