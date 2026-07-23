"""
recommendation_engine.py

Steps 3 & 4 of the multi-step audit pipeline.

Step 3 — Issue Detection:
  Detects specific writing flaws, data gaps, weak assumptions,
  narrative gaps, strategic gaps, and audience relevance gaps
  with mandatory location references.

Step 4 — Final Synthesis:
  Combines all audit artifacts into a Recommendations dict containing
  strengths, weaknesses, and a prioritised improvement task list.

All findings must be grounded in actual report text — never generic.
"""
import json
from typing import Dict, Any, List, TYPE_CHECKING

from .text_preprocessor import ParsedReport, truncate_for_prompt

if TYPE_CHECKING:
    from .groq_reviewer import GroqClient


# ---------------------------------------------------------------------------
# STEP 3 — Issue Detection Prompt
# ---------------------------------------------------------------------------

_ISSUE_SYSTEM = (
    "You are a senior institutional research auditor and strategy reviewer. "
    "You must identify specific, evidence-grounded issues in the report below. "
    "Never write generic observations. Every finding must reference the specific "
    "section and text in the report. Return strict JSON only."
)

_ISSUE_USER_TEMPLATE = """\
REPORT TEXT (structured by section and paragraph):
{report_text}

CLAIMS AUDIT (from previous step):
{claims_summary}

AUDIT TASK:
Examine the report text above and identify specific, located issues in ALL of the
following categories. For EACH finding, include an exact location reference in this
format: "Location → [<section>] | Para <N> | \\"<opening words>\\" → \\"<closing words>\\""

You MUST produce findings for EVERY category below. If no real issues exist in a
category, state: {{"finding": "None identified — <brief explanation>", "location_ref": "N/A", "severity": "Low"}}

CATEGORIES:

A. strengths — Things the report does particularly well, with specific evidence.
B. weaknesses — Specific content or structural problems, with location.
C. data_gaps — Places where a specific claim is made but no data supports it.
   Include what data is missing and why it matters.
D. weak_assumptions — Forecasts, timelines, or causal claims that are not
   backed by the evidence in the report. Include what evidence is missing.
E. writing_flaws — Specific instances of: vague language, undefined jargon,
   repeated phrases, weak transitions, overloaded sentences, filler text,
   or unsupported conclusions. Quote the problematic text.
F. narrative_gaps — Places where the report fails to connect analysis to
   conclusions, or where the argument is incomplete or circular.
G. strategic_gaps — Missing "so-what" implications, missing decision
   recommendations, missing risk/opportunity distinctions, generic advice.
H. audience_relevance_gaps — Content that is too technical, too shallow,
   or mismatched for executive, board, or ministerial readers.

Return JSON in this EXACT structure:
{{
  "strengths": [
    {{"finding": "...", "location_ref": "Location → ...", "severity": "Low"}}
  ],
  "weaknesses": [
    {{"finding": "...", "location_ref": "Location → ...", "severity": "High"}}
  ],
  "data_gaps": [
    {{
      "section": "...",
      "claim": "...",
      "location_ref": "Location → ...",
      "missing_data": ["specific item 1", "specific item 2"],
      "severity": "High"
    }}
  ],
  "weak_assumptions": [
    {{
      "forecast_or_claim": "...",
      "location_ref": "Location → ...",
      "missing_evidence": "...",
      "severity": "High"
    }}
  ],
  "writing_flaws": [
    {{
      "flaw_type": "vague statement | undefined jargon | repeated phrase | ...",
      "example": "<exact or near-exact quote from the report>",
      "location_ref": "Location → ...",
      "severity": "Medium"
    }}
  ],
  "narrative_gaps": [
    {{"finding": "...", "location_ref": "Location → ...", "severity": "Medium"}}
  ],
  "strategic_gaps": [
    {{"finding": "...", "location_ref": "Location → ...", "severity": "High"}}
  ],
  "audience_relevance_gaps": [
    {{"finding": "...", "location_ref": "Location → ...", "severity": "Medium"}}
  ]
}}
"""


# ---------------------------------------------------------------------------
# STEP 4 — Synthesis Prompt (Executive Communication + Improvement Tasks)
# ---------------------------------------------------------------------------

_SYNTH_SYSTEM = (
    "You are a senior institutional editor finalising an audit report. "
    "Based on all previous audit findings, determine executive readiness "
    "and produce a prioritised improvement task list. "
    "Return strict JSON only."
)

_SYNTH_USER_TEMPLATE = """\
AUDIT FINDINGS SUMMARY:
{issues_summary}

SCORING SUMMARY:
- Overall Score: {overall_score}/100 | Grade: {grade}
- Research Quality: {rq}/30
- Evidence & Citations: {ec}/25
- Strategic Clarity: {sc}/25
- Writing & Structure: {ws}/20

HIGH-RISK / UNSUPPORTED CLAIMS COUNT: {bad_claims}

Based on all of the above, determine:

1. Executive Communication Readiness
   For each audience (Minister, Board, SWF), decide YES or NO and provide
   a ONE-SENTENCE reason grounded in the audit findings.
   Identify specific sections that are problematic for each audience.

2. Improvement Tasks
   Produce up to 10 specific, actionable improvement tasks.
   - Each must have: priority (Critical/High/Medium/Low), affected section,
     specific issue description, concrete fix, and expected impact.
   - Order by priority (Critical first).
   - Do NOT produce generic tasks like "Add more evidence."
     Instead: "Section [X] claims [Y] — add citation to [specific source type]."

Return JSON in this EXACT structure:
{{
  "executive_communication": {{
    "minister_ready": <true|false>,
    "board_ready": <true|false>,
    "swf_ready": <true|false>,
    "minister_reason": "<one sentence>",
    "board_reason": "<one sentence>",
    "swf_reason": "<one sentence>",
    "flagged_sections": [
      {{"section": "...", "issue": "..."}}
    ]
  }},
  "improvement_tasks": [
    {{
      "priority": "Critical|High|Medium|Low",
      "section": "<section title>",
      "issue": "<specific issue description>",
      "fix": "<concrete recommended fix>",
      "expected_impact": "<what will improve>"
    }}
  ]
}}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_issues(
    client: "GroqClient",
    parsed_report: ParsedReport,
    claims_audit: Dict[str, Any],
) -> Dict[str, Any]:
    """Step 3: Run issue detection across all categories."""
    print("[REVIEW] Detecting issues: data gaps, writing flaws, strategic gaps...")

    report_text = truncate_for_prompt(parsed_report, max_chars=20_000)
    claims_summary = _summarise_claims(claims_audit)

    prompt = _ISSUE_USER_TEMPLATE.format(
        report_text=report_text,
        claims_summary=claims_summary,
    )

    try:
        result = client.chat_json(
            [
                {"role": "system", "content": _ISSUE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
    except Exception as e:
        print(f"[REVIEW] Issue detection failed: {e}")
        result = _empty_issues()

    # Ensure no category is missing
    for key in ("strengths", "weaknesses", "data_gaps", "weak_assumptions",
                "writing_flaws", "narrative_gaps", "strategic_gaps",
                "audience_relevance_gaps"):
        if not result.get(key):
            result[key] = [_none_identified(key)]

    return result


def synthesise(
    client: "GroqClient",
    issues: Dict[str, Any],
    scores: Dict[str, Any],
    claims_audit: Dict[str, Any],
) -> Dict[str, Any]:
    """Step 4: Produce executive_communication and improvement_tasks."""
    print("[REVIEW] Synthesising executive readiness and improvement tasks...")

    issues_summary = json.dumps(issues, ensure_ascii=False, indent=2)[:8_000]

    prompt = _SYNTH_USER_TEMPLATE.format(
        issues_summary=issues_summary,
        overall_score=scores.get("overall_score", "N/A"),
        grade=scores.get("grade", "N/A"),
        rq=scores.get("research_quality", {}).get("score", "N/A"),
        ec=scores.get("evidence_and_citations", {}).get("score", "N/A"),
        sc=scores.get("strategic_clarity", {}).get("score", "N/A"),
        ws=scores.get("writing_and_structure", {}).get("score", "N/A"),
        bad_claims=(
            claims_audit.get("unsupported_count", 0)
            + claims_audit.get("high_risk_count", 0)
        ),
    )

    try:
        result = client.chat_json(
            [
                {"role": "system", "content": _SYNTH_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
    except Exception as e:
        print(f"[REVIEW] Synthesis failed: {e}")
        result = _fallback_synthesis()

    # Ensure improvement_tasks is never empty
    if not result.get("improvement_tasks"):
        result["improvement_tasks"] = [
            {
                "priority": "High",
                "section": "General",
                "issue": "Synthesis step did not produce tasks.",
                "fix": "Re-run the review pipeline with a valid OPENROUTER_API_KEY.",
                "expected_impact": "Actionable improvement tasks will be generated.",
            }
        ]

    return result


def assemble_recommendations(
    issues: Dict[str, Any],
    synthesis: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge issues and synthesis into a single Recommendations dict."""
    return {
        "strengths":              issues.get("strengths", []),
        "weaknesses":             issues.get("weaknesses", []),
        "data_gaps":              issues.get("data_gaps", []),
        "weak_assumptions":       issues.get("weak_assumptions", []),
        "writing_flaws":          issues.get("writing_flaws", []),
        "narrative_gaps":         issues.get("narrative_gaps", []),
        "strategic_gaps":         issues.get("strategic_gaps", []),
        "audience_relevance_gaps": issues.get("audience_relevance_gaps", []),
        "executive_communication": synthesis.get("executive_communication", {}),
        "improvement_tasks":      synthesis.get("improvement_tasks", []),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _summarise_claims(audit: Dict[str, Any]) -> str:
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
                f"  - [{c.get('classification').upper()}] "
                f"{c.get('claim', '')} ({c.get('location_ref', '')})"
            )
    return "\n".join(lines)


def _none_identified(category: str) -> Dict:
    return {
        "finding": f"None identified — no significant {category.replace('_', ' ')} "
                   "detected in the report text reviewed.",
        "location_ref": "N/A",
        "severity": "Low",
    }


def _empty_issues() -> Dict:
    categories = [
        "strengths", "weaknesses", "data_gaps", "weak_assumptions",
        "writing_flaws", "narrative_gaps", "strategic_gaps", "audience_relevance_gaps",
    ]
    return {k: [] for k in categories}


def _fallback_synthesis() -> Dict:
    return {
        "executive_communication": {
            "minister_ready": False,
            "board_ready": False,
            "swf_ready": False,
            "minister_reason": "Review synthesis unavailable.",
            "board_reason": "Review synthesis unavailable.",
            "swf_reason": "Review synthesis unavailable.",
            "flagged_sections": [],
        },
        "improvement_tasks": [],
    }
