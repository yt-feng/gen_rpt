"""
review_system/extractors/claim_extractor.py

Step 1 of the review pipeline.
Calls Groq to extract and classify every claim in the parsed report.
Writes claims.json to the output directory.
Returns a ClaimsAudit dict.
"""
import json
from pathlib import Path
from typing import Dict, Any, List, TYPE_CHECKING

from shared.report_schema import ParsedReport
from review_system.config.prompts import CLAIM_EXTRACTION_SYSTEM, CLAIM_EXTRACTION_USER
from review_system.config.review_config import CLAIM_EXTRACTION_MAX_CHARS
from review_system.utils.logging_utils import get_claims_logger
from review_system.utils.file_utils import write_json_safe

if TYPE_CHECKING:
    from review_system.reviewers.openrouter_review_engine import OpenRouterReviewEngine

log = get_claims_logger()


def extract_claims(
    engine: "OpenRouterReviewEngine",
    parsed: ParsedReport,
    output_dir: Path,
) -> Dict[str, Any]:
    """
    Extract and classify all claims from the parsed report.
    Writes claims.json and returns the ClaimsAudit dict.
    """
    log.info("Starting claim extraction for: %r", parsed.title)

    report_text = parsed.as_prompt_text(max_chars=CLAIM_EXTRACTION_MAX_CHARS)
    prompt = CLAIM_EXTRACTION_USER.format(report_text=report_text)

    try:
        result = engine.chat_json(
            messages=[
                {"role": "system", "content": CLAIM_EXTRACTION_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.1,
        )
    except Exception as e:
        log.error("Claim extraction failed: %s", e)
        result = {"claims": [], "quantification_ratio": 0}

    claims: List[Dict] = result.get("claims", [])
    audit = {
        "claims":                     claims,
        "quantification_ratio":       result.get("quantification_ratio", 0),
        "total_claims":               len(claims),
        "supported_count":            _count(claims, "supported"),
        "partially_supported_count":  _count(claims, "partially_supported"),
        "unsupported_count":          _count(claims, "unsupported"),
        "high_risk_count":            _count(claims, "high_risk"),
        "speculative_count":          _count(claims, "speculative"),
    }

    write_json_safe(output_dir / "claims.json", audit)
    log.info(
        "Claims extracted: total=%d supported=%d partial=%d "
        "unsupported=%d high_risk=%d speculative=%d",
        audit["total_claims"], audit["supported_count"],
        audit["partially_supported_count"], audit["unsupported_count"],
        audit["high_risk_count"], audit["speculative_count"],
    )
    return audit


def _count(claims: List[Dict], classification: str) -> int:
    return sum(1 for c in claims if c.get("classification") == classification)
