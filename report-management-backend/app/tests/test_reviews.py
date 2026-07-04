import pytest
import pytest_asyncio
import uuid
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.models.review import ReviewAssignment, HumanReview, ReviewComment
from app.models.enums import (
    DocStatus, DocChangeType, ReviewAssignmentStatus, 
    ReviewerRole, CommentActionType, ReviewDecisionType
)
from app.services.review import review_service
from app.services.canonical import VersionManager
from app.services.snapshot import snapshot_engine

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(
    TEST_DATABASE_URL, 
    echo=False,
    poolclass=StaticPool,
    connect_args={'check_same_thread': False}
)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture(scope="function")
async def test_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with TestingSessionLocal() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest_asyncio.fixture
async def setup_doc(test_db: AsyncSession):
    doc_id = uuid.uuid4()
    user_id = uuid.uuid4()
    doc = Document(id=doc_id, title="Test Hitl Doc", slug=f"doc-{doc_id.hex[:6]}", status=DocStatus.ai_reviewed, owner_id=user_id)
    
    ver1 = DocumentVersion(id=uuid.uuid4(), document_id=doc_id, version_number=1, change_type=DocChangeType.AI_GENERATION)
    test_db.add_all([doc, ver1])
    await test_db.flush()
    
    sec1 = DocumentSection(id=uuid.uuid4(), version_id=ver1.id, stable_id="sec_1", section_order=1, title="Test")
    block1 = DocumentBlock(id=uuid.uuid4(), section_id=sec1.id, stable_id="block_1", block_order=1, block_type="paragraph", markdown="Original")
    test_db.add_all([sec1, block1])
    
    doc.current_version_id = ver1.id
    await test_db.commit()
    
    return doc_id, ver1.id, user_id

@pytest.mark.asyncio
async def test_scenario_1_assign_reviewer(test_db: AsyncSession, setup_doc):
    doc_id, _, _ = setup_doc
    reviewer_id = uuid.uuid4()
    
    # Assign reviewer
    assignment = await review_service.assign_reviewer(test_db, doc_id, reviewer_id, ReviewerRole.primary)
    
    assert assignment.reviewer_id == reviewer_id
    assert assignment.status == ReviewAssignmentStatus.pending
    
    doc = await test_db.get(Document, doc_id)
    assert doc.status == DocStatus.assigned

@pytest.mark.asyncio
async def test_scenario_2_inline_comment(test_db: AsyncSession, setup_doc):
    doc_id, ver_id, _ = setup_doc
    reviewer_id = uuid.uuid4()
    
    comment = await review_service.add_comment(
        test_db, doc_id, "block_1", "Please verify this.", CommentActionType.comment, reviewer_id
    )
    
    assert comment.node_stable_id == "block_1"
    assert comment.document_id == doc_id
    assert comment.action_type == CommentActionType.comment
    
    # Thread creation
    reply = await review_service.add_comment(
        test_db, doc_id, "", "I verified it.", CommentActionType.comment, uuid.uuid4(), parent_comment_id=comment.id
    )
    
    assert reply.parent_comment_id == comment.id

@pytest.mark.asyncio
async def test_scenario_3_ai_regeneration(test_db: AsyncSession, setup_doc):
    doc_id, ver_id, _ = setup_doc
    reviewer_id = uuid.uuid4()
    
    comment = await review_service.add_comment(
        test_db, doc_id, "block_1", "Rewrite this better.", CommentActionType.ai_request, reviewer_id
    )
    
    # Trigger regeneration
    new_version = await review_service.handle_ai_request_from_comment(test_db, comment.id)
    
    assert new_version.id != ver_id
    assert new_version.change_type == DocChangeType.AI_REGENERATION
    
    # Verify only targeted node changed
    stmt = select(DocumentBlock).join(DocumentSection).where(
        DocumentSection.version_id == new_version.id, DocumentBlock.stable_id == "block_1"
    )
    result = await test_db.execute(stmt)
    block = result.scalars().first()
    
    assert "Revised content based on feedback: Rewrite this better." in block.markdown
    
    doc = await test_db.get(Document, doc_id)
    assert doc.current_version_id == new_version.id
    assert doc.status == DocStatus.in_review

@pytest.mark.asyncio
async def test_scenario_4_manual_edit(test_db: AsyncSession, setup_doc):
    doc_id, ver_id, _ = setup_doc
    reviewer_id = uuid.uuid4()
    
    # A manual edit is basically creating a new version via VersionManager and modifying it
    new_version = await VersionManager.create_new_version(
        test_db, doc_id, ver_id, DocChangeType.HUMAN_EDIT, reviewer_id, "Manual Edit"
    )
    
    stmt = select(DocumentBlock).join(DocumentSection).where(
        DocumentSection.version_id == new_version.id, DocumentBlock.stable_id == "block_1"
    )
    result = await test_db.execute(stmt)
    block = result.scalars().first()
    block.markdown = "Manually Edited"
    
    doc = await test_db.get(Document, doc_id)
    doc.current_version_id = new_version.id
    await test_db.commit()
    
    # Snapshot
    await snapshot_engine.generate_snapshot(test_db, doc_id, new_version.id)
    
    assert new_version.change_type == DocChangeType.HUMAN_EDIT
    await test_db.refresh(new_version)
    assert new_version.checksum is not None

@pytest.mark.asyncio
async def test_scenario_5_multiple_reviewers(test_db: AsyncSession, setup_doc):
    doc_id, _, _ = setup_doc
    reviewer1 = uuid.uuid4()
    reviewer2 = uuid.uuid4()
    
    await review_service.assign_reviewer(test_db, doc_id, reviewer1, ReviewerRole.primary)
    await review_service.assign_reviewer(test_db, doc_id, reviewer2, ReviewerRole.secondary)
    
    q1 = await review_service.get_reviewer_queue(test_db, reviewer1)
    q2 = await review_service.get_reviewer_queue(test_db, reviewer2)
    
    assert len(q1) == 1
    assert len(q2) == 1
    
    # Independent drafts
    await review_service.save_review_draft(test_db, doc_id, reviewer1, ReviewDecisionType.approved, "Looks good")
    await review_service.save_review_draft(test_db, doc_id, reviewer2, ReviewDecisionType.needs_revision, "Fix block 1")
    
    stmt = select(HumanReview).where(HumanReview.reviewer.in_([reviewer1, reviewer2]))
    result = await test_db.execute(stmt)
    reviews = result.scalars().all()
    assert len(reviews) == 2

@pytest.mark.asyncio
async def test_scenario_6_approval(test_db: AsyncSession, setup_doc):
    doc_id, _, _ = setup_doc
    reviewer = uuid.uuid4()
    
    await review_service.assign_reviewer(test_db, doc_id, reviewer, ReviewerRole.primary)
    
    review = await review_service.complete_review(
        test_db, doc_id, reviewer, ReviewDecisionType.approved, "Final Approval"
    )
    
    assert review.is_draft == False
    assert review.decision == ReviewDecisionType.approved
    
    doc = await test_db.get(Document, doc_id)
    assert doc.status == DocStatus.ready_for_publish
