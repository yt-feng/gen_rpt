"""
review_system/analyzers/audience_analyzer.py

Assesses audience relevance: is the content appropriate for
ministerial, board, or sovereign wealth fund readers?
Returns audience-specific readiness flags and gaps.

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
    "You are a senior policy and investment communications specialist. "
    "Evaluate whether a research report is suitable for three executive audiences. "
    "Base your assessment ONLY on the provided report text. Return strict JSON only."
)

_USER = """\
REPORT TEXT:
{report_text}

AUDIENCE PROFILES:
- Minister/Policy Maker: Needs clear policy implications, plain language, no unexplained technical terms,
  explicit regulatory or funding implications.
- Board/Executive Team: Needs strategic framing, financial implications, competitive context,
  risk and opportunity split, actionable recommendations.
- Sovereign Wealth Fund (SWF): Needs investment thesis, risk-adjusted return framing,
  timeline to commercialisation, comparable deal references, capital deployment context.

TASK: For each audience, assess readiness and identify specific gaps.
Quote or cite specific sections where the report fails each audience.
Format: "Location -> [<section>] | Para <N>"

Return JSON:
{{
  "minister_ready": <true|false>,
  "board_ready": <true|false>,
  "swf_ready": <true|false>,
  "minister_reason": "<one sentence grounded in report content>",
  "board_reason": "<one sentence>",
  "swf_reason": "<one sentence>",
  "audience_relevance_gaps": [
    {{
      "audience": "Minister|Board|SWF",
      "finding": "<specific gap>",
      "location_ref": "Location -> ...",
      "severity": "High|Medium|Low"
    }}
  ],
  "flagged_sections": [
    {{"section": "...", "issue": "..."}}
  ]
}}
"""


def run(
    engine: "OpenRouterReviewEngine",
    parsed: ParsedReport,
    claims_audit: Dict[str, Any] = None,
    combined: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run audience analysis.
    If `combined` is provided (lean mode), extract from it — no API call.
    Otherwise, make an individual API call (full mode).
    Returns readiness flags and gaps.
    """
    if combined is not None:
        log.info("Audience analyzer: extracting from combined result (lean mode)")
        return {
            "minister_ready":          combined.get("minister_ready", False),
            "board_ready":             combined.get("board_ready", False),
            "swf_ready":               combined.get("swf_ready", False),
            "minister_reason":         combined.get("minister_reason", "Not assessed."),
            "board_reason":            combined.get("board_reason", "Not assessed."),
            "swf_reason":              combined.get("swf_reason", "Not assessed."),
            "audience_relevance_gaps": combined.get("audience_relevance_gaps", []),
            "flagged_sections":        combined.get("flagged_sections", []),
        }

    log.info("Running audience analyzer (full mode)")
    report_text = parsed.as_prompt_text(max_chars=14_000)
    prompt = _USER.format(report_text=report_text)

    try:
        result = engine.chat_json(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
        )
    except Exception as e:
        log.error("Audience analyzer failed: %s", e)
        result = {}

    return {
        "minister_ready":          result.get("minister_ready", False),
        "board_ready":             result.get("board_ready", False),
        "swf_ready":               result.get("swf_ready", False),
        "minister_reason":         result.get("minister_reason", "Not assessed."),
        "board_reason":            result.get("board_reason", "Not assessed."),
        "swf_reason":              result.get("swf_reason", "Not assessed."),
        "audience_relevance_gaps": result.get("audience_relevance_gaps", []),
        "flagged_sections":        result.get("flagged_sections", []),
    }
