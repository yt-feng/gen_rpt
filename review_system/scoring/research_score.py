"""
review_system/scoring/research_score.py

Scores Research Quality dimension — max 30 points.
"""
from typing import Dict, Any, TYPE_CHECKING

from shared.report_schema import ParsedReport
from review_system.config.review_config import DIMENSION_MAX, SCORING_MAX_CHARS
from review_system.config.prompts import SCORING_SYSTEM, SCORING_USER
from review_system.utils.logging_utils import get_run_logger

if TYPE_CHECKING:
    from review_system.reviewers.openrouter_review_engine import OpenRouterReviewEngine

log = get_run_logger()
_DIM = "research_quality"
_MAX = DIMENSION_MAX[_DIM]


def score(
    engine: "OpenRouterReviewEngine",
    parsed: ParsedReport,
    claims_audit: Dict[str, Any],
    full_scores: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Extract the research_quality dimension from a full scoring call.
    `full_scores` is the already-fetched scoring dict from the orchestrator.
    This function just validates, clamps, and returns the dimension.
    """
    raw = full_scores.get(_DIM, {})
    dim_score = min(int(raw.get("score", _MAX // 2)), _MAX)

    log.info("Research Quality: %d/%d", dim_score, _MAX)
    return {
        "score":      dim_score,
        "max_points": _MAX,
        "what_works": raw.get("what_works") or [],
        "what_fails": raw.get("what_fails") or [],
    }
