"""
review_system/scoring/strategic_score.py

Scores Strategic Clarity dimension — max 25 points.
"""
from typing import Dict, Any, TYPE_CHECKING

from shared.report_schema import ParsedReport
from review_system.config.review_config import DIMENSION_MAX
from review_system.utils.logging_utils import get_run_logger

if TYPE_CHECKING:
    from review_system.reviewers.openrouter_review_engine import OpenRouterReviewEngine

log = get_run_logger()
_DIM = "strategic_clarity"
_MAX = DIMENSION_MAX[_DIM]


def score(
    engine: "OpenRouterReviewEngine",
    parsed: ParsedReport,
    claims_audit: Dict[str, Any],
    full_scores: Dict[str, Any],
    strategy_findings: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Validate and return the strategic_clarity dimension.
    Applies a soft penalty if strategy analyzer found no explicit recommendations.
    """
    raw = full_scores.get(_DIM, {})
    dim_score = min(int(raw.get("score", _MAX // 2)), _MAX)

    # Soft penalty: no explicit recommendations and score >18
    if strategy_findings:
        if not strategy_findings.get("has_explicit_recommendations") and dim_score > 18:
            dim_score = 18
            log.info("Strategic soft cap: no explicit recommendations -> %d", dim_score)

    log.info("Strategic Clarity: %d/%d", dim_score, _MAX)
    return {
        "score":      dim_score,
        "max_points": _MAX,
        "what_works": raw.get("what_works") or [],
        "what_fails": raw.get("what_fails") or [],
    }
