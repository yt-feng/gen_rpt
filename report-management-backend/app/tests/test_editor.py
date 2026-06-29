import pytest
import pytest_asyncio
import uuid
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.models.editor import NodeLock, NodeEditHistory
from app.models.enums import DocStatus, DocChangeType, ReleaseStatus, EditorActionType

from app.services.editor import editor_service

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
    doc = Document(id=doc_id, title="Test Editor Doc", slug=f"doc-{doc_id.hex[:6]}", status=DocStatus.draft, owner_id=user_id)
    
    ver1 = DocumentVersion(id=uuid.uuid4(), document_id=doc_id, version_number=1, change_type=DocChangeType.AI_GENERATION)
    test_db.add_all([doc, ver1])
    await test_db.flush()
    
    sec1 = DocumentSection(id=uuid.uuid4(), version_id=ver1.id, stable_id="sec_1", section_order=1, title="Test Section")
    block1 = DocumentBlock(id=uuid.uuid4(), section_id=sec1.id, stable_id="block_1", block_order=1, block_type="paragraph", markdown="Original")
    test_db.add_all([sec1, block1])
    
    doc.current_version_id = ver1.id
    await test_db.commit()
    
    return doc_id, ver1.id, user_id

@pytest.mark.asyncio
async def test_scenario_4_draft_mode(test_db: AsyncSession, setup_doc):
    doc_id, ver_id, user_id = setup_doc
    
    # Start draft
    draft_version = await editor_service.start_draft_session(test_db, doc_id, user_id)
    assert draft_version.id != ver_id
    assert draft_version.release_status == ReleaseStatus.Draft
    
    # Original document is unchanged (draft isolated)
    doc = await test_db.get(Document, doc_id)
    assert doc.current_version_id == ver_id

@pytest.mark.asyncio
async def test_scenario_1_and_2_edit_paragraph(test_db: AsyncSession, setup_doc):
    doc_id, ver_id, user_id = setup_doc
    draft_version = await editor_service.start_draft_session(test_db, doc_id, user_id)
    
    # Edit paragraph (autosave)
    new_payload = {"markdown": "Edited Paragraph"}
    history = await editor_service.update_node_content(test_db, draft_version.id, "block_1", new_payload, user_id)
    
    assert history.node_stable_id == "block_1"
    assert history.edit_type == EditorActionType.Human
    
    # Verify canonical update in draft
    stmt = select(DocumentBlock).join(DocumentSection).where(
        DocumentSection.version_id == draft_version.id,
        DocumentBlock.stable_id == "block_1"
    )
    res = await test_db.execute(stmt)
    block = res.scalars().first()
    assert block.markdown == "Edited Paragraph"
    
    # Commit draft
    committed = await editor_service.commit_draft_session(test_db, doc_id, draft_version.id, user_id)
    assert committed.release_status == ReleaseStatus.Internal_Review
    
    doc = await test_db.get(Document, doc_id)
    assert doc.current_version_id == committed.id

@pytest.mark.asyncio
async def test_scenario_3_ai_rewrite(test_db: AsyncSession, setup_doc):
    doc_id, ver_id, user_id = setup_doc
    draft_version = await editor_service.start_draft_session(test_db, doc_id, user_id)
    
    # AI rewrite
    history = await editor_service.ai_node_rewrite(test_db, draft_version.id, "block_1", "Make it professional", user_id)
    
    assert history.edit_type == EditorActionType.AI
    assert "AI Rewritten: Make it professional" in history.new_value["markdown"]
    
    # Check block
    stmt = select(DocumentBlock).join(DocumentSection).where(
        DocumentSection.version_id == draft_version.id,
        DocumentBlock.stable_id == "block_1"
    )
    res = await test_db.execute(stmt)
    block = res.scalars().first()
    assert "AI Rewritten" in block.markdown

@pytest.mark.asyncio
async def test_node_locking(test_db: AsyncSession, setup_doc):
    doc_id, ver_id, user_id = setup_doc
    user2_id = uuid.uuid4()
    
    # User 1 acquires lock
    lock1 = await editor_service.acquire_lock(test_db, doc_id, "block_1", user_id, timeout_minutes=5)
    assert lock1.owner_id == user_id
    
    # User 2 tries to acquire lock on same node
    with pytest.raises(ValueError, match="locked by another user"):
        await editor_service.acquire_lock(test_db, doc_id, "block_1", user2_id)
        
    # User 1 releases lock
    released = await editor_service.release_lock(test_db, doc_id, "block_1", user_id)
    assert released is True
    
    # User 2 can now lock
    lock2 = await editor_service.acquire_lock(test_db, doc_id, "block_1", user2_id)
    assert lock2.owner_id == user2_id
