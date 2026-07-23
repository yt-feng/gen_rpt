"""
groq_reviewer.py

Multi-step audit pipeline orchestrator.

Pipeline steps (never executed in a single pass):
  1. Parse report text into structured sections/paragraphs
  2. Extract and classify all claims (with location refs)
  3. Score report across 4 dimensions
  4. Detect issues (data gaps, writing flaws, strategic gaps, etc.)
  5. Synthesise executive readiness + improvement tasks
  6. Render all output artifacts

Two entry points:
  run_groq_review(output_dir)       — for integrated pipeline mode
  run_groq_review_file(file_path, output_dir) — for standalone CLI mode
"""
from __future__ import annotations

import json
import os
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

from .text_preprocessor import parse_report, ParsedReport
from .claim_extractor import extract_claims
from .score_engine import score_report
from .recommendation_engine import detect_issues, synthesise, assemble_recommendations
from .review_report import generate_review_artifacts


# ---------------------------------------------------------------------------
# Groq client
# ---------------------------------------------------------------------------

class GroqClient:
    """Thin Groq API wrapper with retry/backoff."""

    DEFAULT_MODEL = "llama-3.3-70b-versatile"
    FALLBACK_MODEL = "llama-3.1-70b-versatile"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
    ):
        self.api_key = api_key
        self.model   = model
        self.url     = "https://api.groq.com/openai/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_retries: int = 5,
    ) -> Dict[str, Any]:
        payload = {
            "model":           self.model,
            "messages":        messages,
            "temperature":     temperature,
            "response_format": {"type": "json_object"},
        }

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    self.url,
                    headers=self.headers,
                    json=payload,
                    timeout=90,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return json.loads(content)

            except requests.exceptions.HTTPError as e:
                if resp.status_code == 429 and attempt < max_retries - 1:
                    wait = 10 * (2 ** attempt)   # 10s, 20s, 40s ...
                    print(f"[REVIEW] Rate-limited (429). Retrying in {wait}s ...")
                    time.sleep(wait)
                    last_exc = e
                elif resp.status_code == 400 and "model" in str(e).lower():
                    # Try fallback model
                    print(
                        f"[REVIEW] Model {self.model} unavailable. "
                        f"Falling back to {self.FALLBACK_MODEL}."
                    )
                    self.model = self.FALLBACK_MODEL
                    payload["model"] = self.model
                    last_exc = e
                else:
                    raise
            except (json.JSONDecodeError, KeyError, requests.exceptions.RequestException) as e:
                if attempt < max_retries - 1:
                    wait = 5 * (attempt + 1)
                    print(f"[REVIEW] Request error: {e}. Retrying in {wait}s ...")
                    time.sleep(wait)
                    last_exc = e
                else:
                    raise

        raise RuntimeError(
            f"Groq API failed after {max_retries} attempts. Last error: {last_exc}"
        )


# ---------------------------------------------------------------------------
# Helper: load text from various pipeline artifacts
# ---------------------------------------------------------------------------

def _load_report_text(output_dir: Path) -> Optional[str]:
    """
    Try to load the best available text representation of the report.
    Preference order: report.md > report.html (text stripped) > report_payload.json
    """
    # 1. Markdown — best for parsing
    md_path = output_dir / "report.md"
    if md_path.exists():
        try:
            return md_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # 2. HTML — strip tags
    html_path = output_dir / "report.html"
    if html_path.exists():
        try:
            from html.parser import HTMLParser

            class _TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self._chunks: List[str] = []
                    self._skip = False

                def handle_starttag(self, tag, attrs):
                    if tag in ("script", "style"):
                        self._skip = True

                def handle_endtag(self, tag):
                    if tag in ("script", "style"):
                        self._skip = False

                def handle_data(self, data):
                    if not self._skip:
                        stripped = data.strip()
                        if stripped:
                            self._chunks.append(stripped)

                def get_text(self) -> str:
                    return "\n".join(self._chunks)

            parser = _TextExtractor()
            parser.feed(html_path.read_text(encoding="utf-8", errors="ignore"))
            text = parser.get_text()
            if len(text) > 200:
                return text
        except Exception:
            pass

    # 3. report_payload.json — last resort
    payload_path = output_dir / "report_payload.json"
    if payload_path.exists():
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            # Flatten all string values
            parts = []
            def _flatten(obj: Any) -> None:
                if isinstance(obj, str):
                    parts.append(obj)
                elif isinstance(obj, dict):
                    for v in obj.values():
                        _flatten(v)
                elif isinstance(obj, list):
                    for item in obj:
                        _flatten(item)
            _flatten(payload)
            text = "\n\n".join(parts)
            if len(text) > 200:
                return text
        except Exception:
            pass

    return None


def _get_report_title(output_dir: Path, fallback: str = "Untitled Report") -> str:
    """Attempt to extract the report title from manifest or payload."""
    for fname in ("manifest.json", "report_payload.json"):
        fpath = output_dir / fname
        if fpath.exists():
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                for key in ("title", "report_title", "_display_topic", "topic"):
                    if data.get(key):
                        return str(data[key])
            except Exception:
                pass
    return fallback


# ---------------------------------------------------------------------------
# CORE PIPELINE
# ---------------------------------------------------------------------------

def _run_pipeline(
    client: GroqClient,
    parsed: ParsedReport,
    output_dir: Path,
    report_title: str = "Untitled Report",
) -> Dict[str, Any]:
    """
    Execute the five-step review pipeline and write all artifacts.
    Returns the full ReviewData dict.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Claims extraction
    claims_path = output_dir / "claims.json"
    claims_audit = extract_claims(client, parsed, claims_path)

    # Step 2: Scoring
    scores = score_report(client, parsed, claims_audit)

    # Step 3: Issue detection
    issues = detect_issues(client, parsed, claims_audit)

    # Step 4: Synthesis (executive readiness + tasks)
    synthesis = synthesise(client, issues, scores, claims_audit)

    # Step 5: Assemble
    recommendations = assemble_recommendations(issues, synthesis)

    review_data: Dict[str, Any] = {
        "timestamp":    datetime.utcnow().isoformat(),
        "report_title": report_title,
        "scores":       scores,
        "claims_audit": claims_audit,
        "recommendations": recommendations,
    }

    # Step 6: Render artifacts
    generate_review_artifacts(output_dir, review_data)

    print(
        f"[REVIEW] Pipeline complete — "
        f"Score: {scores.get('overall_score')}/100 | "
        f"Grade: {scores.get('grade')}"
    )
    return review_data


# ---------------------------------------------------------------------------
# PUBLIC ENTRY POINTS
# ---------------------------------------------------------------------------

def run_groq_review(output_dir: Path) -> Dict[str, Any]:
    """
    Entry point for the integrated report generation pipeline.

    Expects `output_dir` to already contain report.md (or report.html /
    report_payload.json) produced by the upstream pipeline.
    """
    print("[REVIEW] Starting evidence-based audit (pipeline mode)")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("[REVIEW] OPENROUTER_API_KEY not set. Skipping review.")
        return {}

    text = _load_report_text(output_dir)
    if not text:
        print("[REVIEW] No readable report content found in output_dir. Skipping.")
        return {}

    report_title = _get_report_title(output_dir)
    print(f"[REVIEW] Reviewing: {report_title!r} ({len(text):,} chars)")

    client = GroqClient(api_key=api_key)
    parsed = parse_report(text)
    print(f"[REVIEW] Parsed report: {len(parsed.sections)} sections, {parsed.total_words} words")

    return _run_pipeline(client, parsed, output_dir, report_title=report_title)


def run_groq_review_file(file_path: Path, output_dir: Path) -> Dict[str, Any]:
    """
    Entry point for the standalone CLI reviewer.

    Reviews any text or markdown file directly.
    """
    print(f"[REVIEW] Starting evidence-based audit (file mode): {file_path}")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("[REVIEW] OPENROUTER_API_KEY not set. Skipping review.")
        return {}

    if not file_path.exists() or not file_path.is_file():
        print(f"[REVIEW] File not found: {file_path}")
        return {}

    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[REVIEW] Failed to read {file_path}: {e}")
        return {}

    if len(text.strip()) < 50:
        print("[REVIEW] File content is too short to review.")
        return {}

    report_title = file_path.stem.replace("_", " ").replace("-", " ").title()
    print(f"[REVIEW] Reviewing: {report_title!r} ({len(text):,} chars)")

    client = GroqClient(api_key=api_key)
    parsed = parse_report(text)
    print(f"[REVIEW] Parsed report: {len(parsed.sections)} sections, {parsed.total_words} words")

    return _run_pipeline(client, parsed, output_dir, report_title=report_title)
