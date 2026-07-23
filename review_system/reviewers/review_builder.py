"""
review_system/reviewers/review_builder.py

Assembles the final ReviewData dict from all pipeline step outputs.
Also runs the synthesis step: executive readiness + improvement tasks.

This is the ONLY place that constructs the final ReviewData shape.
"""
import json
from datetime import datetime
from typing import Dict, Any, List, TYPE_CHECKING

from review_system.config.prompts import SYNTHESIS_SYSTEM, SYNTHESIS_USER
from review_system.config.review_config import SYNTHESIS_MAX_CHARS
from review_system.utils.logging_utils import get_run_logger

if TYPE_CHECKING:
    from review_system.reviewers.groq_review_engine import GroqReviewEngine

log = get_run_logger()

_NONE = {"finding": "None identified.", "location_ref": "N/A", "severity": "Low"}


def _ensure_list(items, fallback_key: str = "finding") -> List[Dict]:
    if not items:
        return [_NONE.copy()]
    return items


def _run_synthesis(
    engine: "GroqReviewEngine",
    pipeline_results: Dict[str, Any],
) -> Dict[str, Any]:
    """Call Groq to produce executive readiness + improvement tasks."""
    scores = pipeline_results["scores"]

    # Collect all issues for the synthesis prompt
    issues = {
        "data_gaps":              pipeline_results["evidence"].get("data_gaps", []),
        "weak_assumptions":       pipeline_results["evidence"].get("weak_assumptions", []),
        "citation_weaknesses":    pipeline_results["citation"].get("citation_weaknesses", []),
        "writing_flaws":          pipeline_results["writing"].get("writing_flaws", []),
        "strategic_gaps":         pipeline_results["strategy"].get("strategic_gaps", []),
        "narrative_gaps":         pipeline_results["structure"].get("narrative_gaps", []),
        "audience_relevance_gaps": pipeline_results["audience"].get("audience_relevance_gaps", []),
    }
    issues_summary = json.dumps(issues, ensure_ascii=False)[:SYNTHESIS_MAX_CHARS]

    bad_claims = (
        pipeline_results["claims_audit"].get("unsupported_count", 0)
        + pipeline_results["claims_audit"].get("high_risk_count", 0)
    )

    prompt = SYNTHESIS_USER.format(
        issues_summary=issues_summary,
        overall_score=scores.get("overall_score", "N/A"),
        grade=scores.get("grade", "N/A"),
        rq=scores.get("research_quality", {}).get("score", "N/A"),
        ec=scores.get("evidence_and_citations", {}).get("score", "N/A"),
        sc=scores.get("strategic_clarity", {}).get("score", "N/A"),
        ws=scores.get("writing_and_structure", {}).get("score", "N/A"),
        bad_claims=bad_claims,
    )

    try:
        result = engine.chat_json(
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
        )
    except Exception as e:
        log.error("Synthesis call failed: %s", e)
        result = {}

    exec_comm = result.get("executive_communication") or {}

    # Fill audience fields from audience_analyzer if synthesis is sparse
    aud = pipeline_results["audience"]
    if not exec_comm.get("minister_reason"):
        exec_comm["minister_ready"]  = aud.get("minister_ready", False)
        exec_comm["board_ready"]     = aud.get("board_ready", False)
        exec_comm["swf_ready"]       = aud.get("swf_ready", False)
        exec_comm["minister_reason"] = aud.get("minister_reason", "Not assessed.")
        exec_comm["board_reason"]    = aud.get("board_reason", "Not assessed.")
        exec_comm["swf_reason"]      = aud.get("swf_reason", "Not assessed.")
        exec_comm["flagged_sections"] = aud.get("flagged_sections", [])

    improvement_tasks = result.get("improvement_tasks") or []
    if not improvement_tasks:
        improvement_tasks = [{
            "priority": "High",
            "section":  "General",
            "issue":    "Synthesis did not produce improvement tasks.",
            "fix":      "Re-run review with a valid OPENROUTER_API_KEY.",
            "expected_impact": "Actionable improvement tasks will be generated.",
        }]

    log.info("Synthesis complete: %d improvement tasks", len(improvement_tasks))
    return {
        "executive_communication": exec_comm,
        "improvement_tasks":       improvement_tasks,
    }


def assemble(
    engine: "GroqReviewEngine",
    pipeline_results: Dict[str, Any],
    report_title: str,
    report_path: str,
) -> Dict[str, Any]:
    """
    Assemble the final ReviewData dict from all pipeline step outputs.
    Runs synthesis (executive readiness + tasks) as the final LLM call.
    """
    log.info("Step 5/5: Assembling final review data")

    synthesis = _run_synthesis(engine, pipeline_results)
    scores = pipeline_results["scores"]
    claims_audit = pipeline_results["claims_audit"]

    # Merge all findings into recommendations
    recommendations: Dict[str, Any] = {
        "strengths": _ensure_list(
            pipeline_results["citation"].get("citation_strengths", [])
        ),
        "weaknesses": _ensure_list(
            pipeline_results["citation"].get("citation_weaknesses", [])
        ),
        "data_gaps":        _ensure_list(pipeline_results["evidence"].get("data_gaps", [])),
        "weak_assumptions": _ensure_list(pipeline_results["evidence"].get("weak_assumptions", [])),
        "writing_flaws":    _ensure_list(pipeline_results["writing"].get("writing_flaws", [])),
        "narrative_gaps":   _ensure_list(pipeline_results["structure"].get("narrative_gaps", [])),
        "strategic_gaps":   _ensure_list(pipeline_results["strategy"].get("strategic_gaps", [])),
        "audience_relevance_gaps": _ensure_list(
            pipeline_results["audience"].get("audience_relevance_gaps", [])
        ),
        "executive_communication": synthesis["executive_communication"],
        "improvement_tasks":       synthesis["improvement_tasks"],
    }

    review_data: Dict[str, Any] = {
        "timestamp":    datetime.utcnow().isoformat() + "Z",
        "report_title": report_title,
        "report_path":  report_path,
        "scores":       scores,
        "claims_audit": claims_audit,
        "recommendations": recommendations,
    }

    log.info(
        "ReviewData assembled | Score=%.1f | Grade=%s | Tasks=%d",
        scores.get("overall_score", 0),
        scores.get("grade", "?"),
        len(synthesis["improvement_tasks"]),
    )
    return review_data
