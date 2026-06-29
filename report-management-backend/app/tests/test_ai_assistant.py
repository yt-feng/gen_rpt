import pytest
import pytest_asyncio
import uuid
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.models.ai import AIProposal
from app.models.enums import DocStatus, DocChangeType, ProposalStatus, AIProviderType

from app.services.ai_assistant import ai_assistant_service
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
    doc = Document(id=doc_id, title="AI Doc", slug=f"ai-{doc_id.hex[:6]}", status=DocStatus.draft, owner_id=user_id)
    
    ver1 = DocumentVersion(id=uuid.uuid4(), document_id=doc_id, version_number=1, change_type=DocChangeType.AI_GENERATION)
    test_db.add_all([doc, ver1])
    await test_db.flush()
    
    sec1 = DocumentSection(id=uuid.uuid4(), version_id=ver1.id, stable_id="sec_1", section_order=1, title="Test Section")
    block1 = DocumentBlock(id=uuid.uuid4(), section_id=sec1.id, stable_id="block_1", block_order=1, block_type="paragraph", markdown="Original content")
    test_db.add_all([sec1, block1])
    
    doc.current_version_id = ver1.id
    await test_db.commit()
    
    return doc_id, ver1.id, user_id

@pytest.mark.asyncio
async def test_scenario_1_rewrite_paragraph_and_accept(test_db: AsyncSession, setup_doc):
    doc_id, ver_id, user_id = setup_doc
    draft_version = await editor_service.start_draft_session(test_db, doc_id, user_id)
    
    # 1. Propose
    proposals = await ai_assistant_service.generate_proposals(
        db=test_db,
        document_id=doc_id,
        version_id=ver_id,
        target_node_stable_ids=["block_1"],
        prompt_text="Make it better",
        provider_type=AIProviderType.groq,
        num_alternatives=1,
        editor_id=user_id
    )
    assert len(proposals) == 1
    prop = proposals[0]
    assert prop.status == ProposalStatus.pending
    
    # Check document remains unchanged in current version
    doc = await test_db.get(Document, doc_id)
    assert doc.current_version_id == ver_id
    
    # 2. Accept
    accepted = await ai_assistant_service.accept_proposal(
        db=test_db,
        proposal_id=prop.id,
        reviewer_id=user_id,
        draft_version_id=draft_version.id
    )
    assert accepted.status == ProposalStatus.accepted
    
    # Verify drafted block changed
    stmt = select(DocumentBlock).join(DocumentSection).where(
        DocumentSection.version_id == draft_version.id,
        DocumentBlock.stable_id == "block_1"
    )
    res = await test_db.execute(stmt)
    block = res.scalars().first()
    assert "AI Groq Rewrite" in block.markdown # Based on our mock logic

@pytest.mark.asyncio
async def test_scenario_2_generate_multiple_alternatives(test_db: AsyncSession, setup_doc):
    doc_id, ver_id, user_id = setup_doc
    draft_version = await editor_service.start_draft_session(test_db, doc_id, user_id)
    
    # Generate 3
    proposals = await ai_assistant_service.generate_proposals(
        db=test_db,
        document_id=doc_id,
        version_id=ver_id,
        target_node_stable_ids=["block_1"],
        prompt_text="Executive summary",
        provider_type=AIProviderType.anthropic,
        num_alternatives=3,
        editor_id=user_id
    )
    assert len(proposals) == 3
    assert all(p.status == ProposalStatus.pending for p in proposals)

@pytest.mark.asyncio
async def test_scenario_4_reject_proposal(test_db: AsyncSession, setup_doc):
    doc_id, ver_id, user_id = setup_doc
    
    proposals = await ai_assistant_service.generate_proposals(
        db=test_db,
        document_id=doc_id,
        version_id=ver_id,
        target_node_stable_ids=["block_1"],
        prompt_text="Make it worse",
        provider_type=AIProviderType.local
    )
    
    rejected = await ai_assistant_service.reject_proposal(
        db=test_db,
        proposal_id=proposals[0].id,
        reviewer_id=user_id
    )
    assert rejected.status == ProposalStatus.rejected
