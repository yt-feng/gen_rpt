"""
review_system/reviewers/review_orchestrator.py

Runs the multi-step analysis pipeline and returns all raw step outputs.
Does NOT assemble the final ReviewData — that is review_builder's job.

Pipeline order (lean mode — default, 3 API calls total):
  1. Claim extraction
  2. Full scoring call (one LLM pass, all 4 dimensions)
  3. Combined analysis (one LLM pass covers all 6 analyzers)
  4. Per-dimension scorer validation + caps (no API call)
  5. Returns collected results dict

Full mode (--full-analysis, 9 API calls):
  Same as above but step 3 is split into 6 individual analyzer calls.
"""
from pathlib import Path
from typing import Dict, Any

from shared.report_schema import ParsedReport
from review_system.reviewers.openrouter_review_engine import OpenRouterReviewEngine
from review_system.extractors.claim_extractor import extract_claims
from review_system.analyzers import (
    run_evidence, run_citation, run_writing,
    run_strategy, run_structure, run_audience,
)
from review_system.scoring import (
    score_research, score_evidence, score_strategic, score_writing,
)
from review_system.config.prompts import (
    SCORING_SYSTEM, SCORING_USER,
    COMBINED_ANALYSIS_SYSTEM, COMBINED_ANALYSIS_USER,
)
from review_system.config.review_config import (
    SCORING_MAX_CHARS, COMBINED_ANALYSIS_MAX_CHARS, GRADE_THRESHOLDS,
)
from review_system.utils.logging_utils import get_run_logger

log = get_run_logger()


def _grade(total: float) -> str:
    for threshold, label in GRADE_THRESHOLDS:
        if total >= threshold:
            return label
    return "Red"


def _fetch_raw_scores(
    engine: OpenRouterReviewEngine,
    parsed: ParsedReport,
    claims_audit: Dict[str, Any],
) -> Dict[str, Any]:
    """One LLM call to get all 4 dimension scores."""
    report_text = parsed.as_prompt_text(max_chars=SCORING_MAX_CHARS)
    prompt = SCORING_USER.format(
        report_text=report_text,
        total_claims=claims_audit.get("total_claims", 0),
        supported=claims_audit.get("supported_count", 0),
        partial=claims_audit.get("partially_supported_count", 0),
        unsupported=claims_audit.get("unsupported_count", 0),
        high_risk=claims_audit.get("high_risk_count", 0),
        speculative=claims_audit.get("speculative_count", 0),
        quant_ratio=claims_audit.get("quantification_ratio", 0),
    )
    try:
        return engine.chat_json(
            messages=[
                {"role": "system", "content": SCORING_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.1,
        )
    except Exception as e:
        log.error("Raw scoring call failed: %s", e)
        return {}


def _run_combined_analysis(
    engine: OpenRouterReviewEngine,
    parsed: ParsedReport,
    claims_audit: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Single LLM call covering all 6 analyser dimensions:
    evidence, citation, writing, strategy, structure, audience.
    Lean mode default — saves 5 API calls vs full mode.
    """
    report_text = parsed.as_prompt_text(max_chars=COMBINED_ANALYSIS_MAX_CHARS)
    section_list = ", ".join(parsed.section_titles)

    # Build a readable list of bad claims for the prompt
    bad_lines = []
    for c in claims_audit.get("claims", []):
        if c.get("classification") in ("unsupported", "high_risk", "speculative"):
            bad_lines.append(
                f"  [{c.get('classification','?').upper()}] "
                f"{c.get('claim', '')} ({c.get('location_ref', '')})"
            )
    bad_claims_list = "\n".join(bad_lines) if bad_lines else "  None"

    sourced = sum(
        1 for c in claims_audit.get("claims", [])
        if c.get("source_referenced")
    )

    prompt = COMBINED_ANALYSIS_USER.format(
        report_text=report_text,
        section_list=section_list,
        total_claims=claims_audit.get("total_claims", 0),
        supported=claims_audit.get("supported_count", 0),
        partial=claims_audit.get("partially_supported_count", 0),
        unsupported=claims_audit.get("unsupported_count", 0),
        high_risk=claims_audit.get("high_risk_count", 0),
        speculative=claims_audit.get("speculative_count", 0),
        sourced=sourced,
        quant_ratio=claims_audit.get("quantification_ratio", 0),
        bad_claims_list=bad_claims_list,
    )

    try:
        return engine.chat_json(
            messages=[
                {"role": "system", "content": COMBINED_ANALYSIS_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
        )
    except Exception as e:
        log.error("Combined analysis call failed: %s", e)
        return {}


def run_pipeline(
    engine: OpenRouterReviewEngine,
    parsed: ParsedReport,
    output_dir: Path,
    lean_mode: bool = True,
) -> Dict[str, Any]:
    """
    Execute the full review pipeline.

    lean_mode=True  (default): 3 API calls — claim extraction + scoring + combined analysis.
    lean_mode=False (--full-analysis): 9 API calls — original 6 separate analyzer calls.

    Returns a dict with keys:
      claims_audit, raw_scores, evidence, citation, writing,
      strategy, structure, audience, scores
    """
    log.info("Pipeline starting for: %r (lean_mode=%s)", parsed.title, lean_mode)

    # ── Step 1: Claims ──────────────────────────────────────────────────────
    log.info("Step 1/4: Extracting claims")
    claims_audit = extract_claims(engine, parsed, output_dir)

    # ── Step 2: Raw scoring (one LLM pass) ──────────────────────────────────
    log.info("Step 2/4: Scoring across 4 dimensions")
    raw_scores = _fetch_raw_scores(engine, parsed, claims_audit)

    # ── Step 3: Analysis ────────────────────────────────────────────────────
    if lean_mode:
        log.info("Step 3/4: Running combined analysis (lean mode — 1 API call)")
        combined = _run_combined_analysis(engine, parsed, claims_audit)

        # Extract each analyzer's output from the single combined result
        evidence_r  = run_evidence(engine,   parsed, claims_audit, combined=combined)
        citation_r  = run_citation(engine,   parsed, claims_audit, combined=combined)
        writing_r   = run_writing(engine,    parsed, claims_audit, combined=combined)
        strategy_r  = run_strategy(engine,   parsed, claims_audit, combined=combined)
        structure_r = run_structure(engine,  parsed, claims_audit, combined=combined)
        audience_r  = run_audience(engine,   parsed, claims_audit, combined=combined)
    else:
        log.info("Step 3/4: Running 6 individual analyzers (full mode — 6 API calls)")
        evidence_r  = run_evidence(engine,   parsed, claims_audit)
        citation_r  = run_citation(engine,   parsed, claims_audit)
        writing_r   = run_writing(engine,    parsed, claims_audit)
        strategy_r  = run_strategy(engine,   parsed, claims_audit)
        structure_r = run_structure(engine,  parsed, claims_audit)
        audience_r  = run_audience(engine,   parsed, claims_audit)

    # ── Step 4: Per-dimension scoring with caps ──────────────────────────────
    log.info("Step 4/4: Applying scoring caps and dimension finalisation")
    rq_dim = score_research(engine, parsed, claims_audit, raw_scores)
    ec_dim = score_evidence(engine, parsed, claims_audit, raw_scores, citation_r)
    sc_dim = score_strategic(engine, parsed, claims_audit, raw_scores, strategy_r)
    ws_dim = score_writing(engine, parsed, claims_audit, raw_scores, writing_r)

    total = round(
        rq_dim["score"] + ec_dim["score"] + sc_dim["score"] + ws_dim["score"], 1
    )
    grade = _grade(total)

    scores: Dict[str, Any] = {
        "overall_score":         total,
        "grade":                 grade,
        "research_quality":      rq_dim,
        "evidence_and_citations": ec_dim,
        "strategic_clarity":     sc_dim,
        "writing_and_structure": ws_dim,
    }

    log.info(
        "Scores: Research=%d/30 | Evidence=%d/25 | Strategic=%d/25 | Writing=%d/20 "
        "| Total=%.1f/100 | Grade=%s",
        rq_dim["score"], ec_dim["score"], sc_dim["score"], ws_dim["score"],
        total, grade,
    )

    return {
        "claims_audit": claims_audit,
        "raw_scores":   raw_scores,
        "evidence":     evidence_r,
        "citation":     citation_r,
        "writing":      writing_r,
        "strategy":     strategy_r,
        "structure":    structure_r,
        "audience":     audience_r,
        "scores":       scores,
    }
