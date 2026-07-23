"""
review_system/analyzers/structure_analyzer.py

Checks narrative integrity: argument flow, section coherence,
logical transitions between sections, conclusion strength.

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
    "You are a senior editor reviewing narrative structure and logical flow. "
    "Identify specific structural or narrative deficiencies. "
    "Ground every finding in the actual report text. Return strict JSON only."
)

_USER = """\
REPORT SECTIONS AND STRUCTURE:
{report_text}

SECTION LIST: {section_list}

TASK: Evaluate narrative structure and logical coherence.
Every finding must have a location reference.
Format: "Location -> [<section>] | Para <N> | \"<open>\" -> \"<close>\""

CHECK FOR:
1. Circular arguments: Conclusion restates premise without new evidence
2. Broken flow: Section ends without connecting to the next
3. Orphaned analysis: Analytical point introduced but never resolved
4. Weak introduction: Opening does not frame what the report does
5. Weak conclusion: Ending does not synthesise key findings

Return JSON:
{{
  "narrative_gaps": [
    {{
      "gap_type": "circular|broken_flow|orphaned|weak_intro|weak_conclusion",
      "finding": "<specific description>",
      "location_ref": "Location -> ...",
      "severity": "High|Medium|Low"
    }}
  ],
  "overall_narrative_coherence": "Strong|Moderate|Weak"
}}
"""


def run(
    engine: "OpenRouterReviewEngine",
    parsed: ParsedReport,
    claims_audit: Dict[str, Any] = None,
    combined: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run structure analysis.
    If `combined` is provided (lean mode), extract from it — no API call.
    Otherwise, make an individual API call (full mode).
    Returns narrative gaps dict.
    """
    if combined is not None:
        log.info("Structure analyzer: extracting from combined result (lean mode)")
        return {
            "narrative_gaps":              combined.get("narrative_gaps", []),
            "overall_narrative_coherence": combined.get("overall_narrative_coherence", "Unknown"),
        }

    log.info("Running structure analyzer (full mode)")
    report_text = parsed.as_prompt_text(max_chars=14_000)
    section_list = ", ".join(parsed.section_titles)
    prompt = _USER.format(report_text=report_text, section_list=section_list)

    try:
        result = engine.chat_json(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
        )
    except Exception as e:
        log.error("Structure analyzer failed: %s", e)
        result = {}

    return {
        "narrative_gaps":              result.get("narrative_gaps", []),
        "overall_narrative_coherence": result.get("overall_narrative_coherence", "Unknown"),
    }
