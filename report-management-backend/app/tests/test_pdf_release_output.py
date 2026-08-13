from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.pdf_release import (
    _checksum,
    _render_checksum,
    _sanitize_release_html,
    _validate_pdf_bytes,
)


def test_release_html_removes_internal_and_source_language_evidence():
    html = """
    <html><body>
      <h1>A RAG-first flood resilience report</h1>
      <section>
        <p>Normal management analysis remains visible.</p>
        <ul class="evidence-list">
          <li>{'chunk_id': 'internal-42', 'excerpt': '洪水基础设施', 'why_it_matters': 'Internal evidence'}</li>
          <li>[Chunk: internal-42] "洪水基础设施" — Supporting document evidence.</li>
          <li>洪水基础设施损失严重。</li>
          <li>Municipal review — Procurement should use staged decision gates.</li>
        </ul>
      </section>
    </body></html>
    """

    cleaned = _sanitize_release_html(html, language="en")

    assert "Normal management analysis remains visible." in cleaned
    assert "Municipal review" in cleaned
    assert "evidence-led flood resilience report" in cleaned
    for forbidden in ("RAG-first", "chunk_id", "why_it_matters", "Supporting document evidence", "洪水"):
        assert forbidden not in cleaned


def test_renderer_revision_invalidates_legacy_cached_pdf():
    html = "<html><body><p>Clean report.</p></body></html>"
    assert _render_checksum(html) != _checksum(html.encode("utf-8"))
    assert _render_checksum(html) == _render_checksum(html)


def test_pdf_validation_rejects_extractable_internal_evidence():
    dirty_reader = SimpleNamespace(
        pages=[SimpleNamespace(extract_text=lambda: "{'chunk_id': 'internal-42', 'why_it_matters': 'debug'}")]
    )
    with patch("pypdf.PdfReader", return_value=dirty_reader):
        with pytest.raises(RuntimeError, match="internal metadata"):
            _validate_pdf_bytes(b"%PDF-dirty")

    clean_reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "Clean management analysis.")])
    with patch("pypdf.PdfReader", return_value=clean_reader):
        _validate_pdf_bytes(b"%PDF-clean")
