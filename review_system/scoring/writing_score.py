"""
review_system/scoring/writing_score.py

Scores Writing & Structure dimension — max 20 points.
"""
from typing import Dict, Any, TYPE_CHECKING

from shared.report_schema import ParsedReport
from review_system.config.review_config import DIMENSION_MAX
from review_system.utils.logging_utils import get_run_logger

if TYPE_CHECKING:
    from review_system.reviewers.openrouter_review_engine import OpenRouterReviewEngine

log = get_run_logger()
_DIM = "writing_and_structure"
_MAX = DIMENSION_MAX[_DIM]


def score(
    engine: "OpenRouterReviewEngine",
    parsed: ParsedReport,
    claims_audit: Dict[str, Any],
    full_scores: Dict[str, Any],
    writing_findings: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Validate and return the writing_and_structure dimension.
    Applies a penalty proportional to the number of Critical/High writing flaws found.
    """
    raw = full_scores.get(_DIM, {})
    dim_score = min(int(raw.get("score", _MAX // 2)), _MAX)

    # Penalty: -1 per Critical flaw, -0.5 per High flaw (floor = 0)
    if writing_findings:
        flaws = writing_findings.get("writing_flaws", [])
        penalty = sum(
            1.0 if f.get("severity") == "Critical" else
            0.5 if f.get("severity") == "High" else 0
            for f in flaws
        )
        if penalty > 0:
            dim_score = max(0, round(dim_score - penalty))
            log.info("Writing penalty: %.1f pts for %d high/critical flaws", penalty, len(flaws))

    log.info("Writing & Structure: %d/%d", dim_score, _MAX)
    return {
        "score":      dim_score,
        "max_points": _MAX,
        "what_works": raw.get("what_works") or [],
        "what_fails": raw.get("what_fails") or [],
    }
