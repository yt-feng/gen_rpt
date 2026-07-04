"""
review_system/config/review_config.py

All configuration constants for the review system.
No business logic — only named constants.
"""

# ---------------------------------------------------------------------------
# Groq API
# ---------------------------------------------------------------------------
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
GROQ_FALLBACK_MODEL = "llama-3.1-70b-versatile"
GROQ_REQUEST_TIMEOUT = 90          # seconds
GROQ_MAX_RETRIES = 12
GROQ_RATE_LIMIT_BASE_WAIT = 15     # seconds, doubles on each retry

# ---------------------------------------------------------------------------
# Scoring dimensions
# ---------------------------------------------------------------------------
DIMENSION_MAX = {
    "research_quality":       30,
    "evidence_and_citations": 25,
    "strategic_clarity":      25,
    "writing_and_structure":  20,
}
TOTAL_MAX_SCORE = 100

# ---------------------------------------------------------------------------
# Grade thresholds  (score >= threshold → grade)
# ---------------------------------------------------------------------------
GRADE_THRESHOLDS = [
    (90, "Gold"),
    (75, "Silver"),
    (60, "Bronze"),
    (0,  "Red"),
]

# ---------------------------------------------------------------------------
# Claim classifications
# ---------------------------------------------------------------------------
CLAIM_CLASSIFICATIONS = [
    "supported",
    "partially_supported",
    "unsupported",
    "high_risk",
    "speculative",
]
HIGH_RISK_CLASSIFICATIONS = {"unsupported", "high_risk"}

# ---------------------------------------------------------------------------
# Evidence caps: if too many bad claims, Evidence score is capped
# ---------------------------------------------------------------------------
EVIDENCE_SCORE_CAP_NO_BIBLIOGRAPHY = 14   # out of 25
EVIDENCE_SCORE_CAP_MANY_UNSUPPORTED = 18  # out of 25

# ---------------------------------------------------------------------------
# Prompt length limits
# ---------------------------------------------------------------------------
CLAIM_EXTRACTION_MAX_CHARS = 24_000
SCORING_MAX_CHARS = 20_000
ISSUE_DETECTION_MAX_CHARS = 20_000
COMBINED_ANALYSIS_MAX_CHARS = 18_000   # slightly smaller to keep combined response under token limit
SYNTHESIS_MAX_CHARS = 8_000

# ---------------------------------------------------------------------------
# Output file names
# ---------------------------------------------------------------------------
OUTPUT_FILES = {
    "review_json":         "review.json",
    "review_md":           "review.md",
    "review_html":         "review.html",
    "claims_json":         "claims.json",
    "findings_json":       "findings.json",
    "scores_json":         "scores.json",
    "audit_manifest":      "audit_manifest.json",
}

# ---------------------------------------------------------------------------
# Log file names  (written inside review_system/logs/)
# ---------------------------------------------------------------------------
LOG_FILES = {
    "run":        "review_run.log",
    "claims":     "claim_extraction.log",
    "generation": "review_generation.log",
    "error":      "error.log",
}

# ---------------------------------------------------------------------------
# Severity ordering for sorting
# ---------------------------------------------------------------------------
SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
