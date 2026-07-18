from app.services.retrieval_context import build_validated_context


def _chunk(chunk_id: str, text: str, confidence: float = 0.9) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "doc-1",
        "text": text,
        "confidence": confidence,
    }


def test_validated_context_deduplicates_and_respects_chunk_limit():
    chunks = [
        _chunk("one", "same enterprise evidence"),
        _chunk("two", "same   enterprise evidence"),
        _chunk("three", "different evidence"),
    ]

    result = build_validated_context(
        chunks,
        document_names={"doc-1": "source.pdf"},
        token_budget=200,
        max_chunks=2,
    )

    assert [chunk["chunk_id"] for chunk in result["selected_chunks"]] == ["one", "three"]
    assert result["estimated_tokens"] <= 200
    assert result["context_string"].count("same enterprise evidence") == 1


def test_validated_context_truncates_large_evidence_to_budget():
    result = build_validated_context(
        [_chunk("large", "word " * 1000)],
        token_budget=50,
        max_chunks=1,
    )

    assert len(result["selected_chunks"]) == 1
    assert result["estimated_tokens"] <= 50
    assert len(result["context_string"]) < 1000
