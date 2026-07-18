import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.validation.source import SourceValidationService
from app.services.validation.conflict import ConflictService
from app.services.retrieval_similarity import calculate_keyword_score
from app.core.exceptions import _cors_headers
from app.services.knowledge_processing.workers import embedding as embedding_worker


def _query_result(*documents):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(documents)
    return result


@pytest.mark.asyncio
async def test_legacy_upload_without_source_metadata_is_treated_as_manual_upload():
    document_id = uuid.uuid4()
    document = SimpleNamespace(
        id=document_id,
        file_name="sample.txt",
        processing_status="completed",
        validation_status="pending",
        collection=SimpleNamespace(status="active"),
        sources=[],
    )
    db = AsyncMock()
    db.execute.return_value = _query_result(document)
    policy = SimpleNamespace(rules={"allowed_source_types": ["manual_upload"]})

    results, errors = await SourceValidationService().validate_sources(
        db, [document_id], policy
    )

    assert errors == []
    assert results[document_id]["is_valid"] is True
    assert results[document_id]["source_type"] == "manual_upload"
    assert results[document_id]["publisher"] == "Enterprise Upload"


@pytest.mark.asyncio
async def test_manual_upload_is_rejected_when_policy_explicitly_excludes_it():
    document_id = uuid.uuid4()
    document = SimpleNamespace(
        id=document_id,
        file_name="sample.txt",
        processing_status="completed",
        validation_status="pending",
        collection=SimpleNamespace(status="active"),
        sources=[],
    )
    db = AsyncMock()
    db.execute.return_value = _query_result(document)
    policy = SimpleNamespace(rules={"allowed_source_types": ["government"]})

    results, errors = await SourceValidationService().validate_sources(
        db, [document_id], policy
    )

    assert results[document_id]["is_valid"] is False
    assert "manual_upload" in errors[0]


@pytest.mark.asyncio
async def test_unrelated_key_headings_do_not_create_false_numeric_conflicts():
    chunks = [
        {
            "chunk_id": uuid.uuid4(),
            "document_id": uuid.uuid4(),
            "file_name": "consumer.md",
            "text_content": "Acceptance was 68% among 5,200 respondents.",
            "metadata": {"heading": "Key Findings"},
        },
        {
            "chunk_id": uuid.uuid4(),
            "document_id": uuid.uuid4(),
            "file_name": "regulatory.md",
            "text_content": "Compliance funding is 4.5 million USD.",
            "metadata": {"heading": "Key Restrictions"},
        },
    ]

    conflict_map, conflicts = await ConflictService().detect_conflicts(
        AsyncMock(), chunks, SimpleNamespace()
    )

    assert conflict_map == {}
    assert conflicts == []


def test_keyword_score_retains_relevant_long_evidence_chunks():
    query = "Project SkyNet financial investment urban drone delivery"
    relevant = (
        "OmniLogistics is allocating 45.5 million dollars in capital expenditure "
        "for Project SkyNet, its autonomous urban drone delivery initiative."
    )
    unrelated = "Quarterly hiring policy for the human resources department."

    assert calculate_keyword_score(query, relevant) > 0.5
    assert calculate_keyword_score(query, relevant) > calculate_keyword_score(query, unrelated)


def test_error_responses_preserve_configured_frontend_cors():
    request = SimpleNamespace(
        headers={"origin": "https://gen-rpt-review-frontend.pages.dev"}
    )

    headers = _cors_headers(request)

    assert headers["Access-Control-Allow-Origin"] == request.headers["origin"]
    assert headers["Access-Control-Allow-Credentials"] == "true"


@pytest.mark.asyncio
async def test_query_embedding_uses_short_single_attempt(monkeypatch):
    captured = {}

    async def fake_call(texts, request_timeout=90.0, retry_count=None):
        captured.update(timeout=request_timeout, retries=retry_count)
        return [[0.1, 0.2]]

    monkeypatch.setattr(embedding_worker, "_call_hf_api", fake_call)

    assert await embedding_worker.generate_query_embedding("SkyNet") == [0.1, 0.2]
    assert captured == {"timeout": 8.0, "retries": 1}
