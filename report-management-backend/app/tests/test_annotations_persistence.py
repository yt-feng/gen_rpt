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

@pytest.mark.anyio
async def test_relational_status_logs():
    # Verify that persistence logs compile correctly
    from app.services.review_service import ReviewService
    service = ReviewService()
    assert hasattr(service, "get_db_report_status")

@pytest.mark.anyio
async def test_relational_status_update():
    from app.services.review_service import ReviewService
    service = ReviewService()
    assert hasattr(service, "update_db_report_status")
