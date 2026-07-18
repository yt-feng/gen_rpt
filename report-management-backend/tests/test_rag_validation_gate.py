import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.validation.source import SourceValidationService
from app.services.validation.conflict import ConflictService


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
