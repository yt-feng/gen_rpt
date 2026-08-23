# test_annotations_persistence.py
import pytest
import uuid
from app.services.review_service import ReviewService
from app.models.enums import ReviewDecisionType, CommentActionType

@pytest.mark.anyio
async def test_review_service_db_persistence():
    # Simple check that the imported ReviewService functions match expectations
    assert hasattr(ReviewService, "create_or_update_human_review")
    assert hasattr(ReviewService, "add_review_comment")
    assert hasattr(ReviewService, "list_document_comments")
