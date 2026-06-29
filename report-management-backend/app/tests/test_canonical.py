import pytest
import pytest_asyncio
import uuid
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.models.iteration import IterationHistory
from app.services.canonical import MarkdownParser, VersionManager
from app.services.rendering import rendering_pipeline
from app.services.iteration import iteration_engine

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

@pytest.mark.asyncio
async def test_full_report_ingestion(test_db: AsyncSession):
    """Test Case 1: Full report ingestion into Canonical format -> HTML -> Markdown -> PDF."""
    
    doc = Document(
        id=uuid.uuid4(),
        title="Fusion Report",
        slug="fusion-test",
        document_type="Thought Leadership",
        status="draft"
    )
    test_db.add(doc)
    await test_db.flush()

    ver = DocumentVersion(
        id=uuid.uuid4(),
        document_id=doc.id,
        version_number=1,
        change_type="AI_GENERATION"
    )
    test_db.add(ver)
    doc.current_version_id = ver.id
    await test_db.flush()

    # Parse sample markdown
    md_content = "# Executive Summary\n\nFusion energy is the future.\n\n# Market\n\n$10B TAM by 2030.\n\n- Point 1\n- Point 2"
    sections = MarkdownParser.parse_to_canonical(md_content, ver.id)
    for sec in sections:
        test_db.add(sec)
        for b in sec.blocks:
            b.section_id = sec.id
            test_db.add(b)
    
    await test_db.commit()

    # Verify rendering
    html_output = await rendering_pipeline.render_html(test_db, ver.id)
    assert "<article" in html_output
    assert "Executive Summary" in html_output
    assert "Fusion energy" in html_output

    md_output = await rendering_pipeline.render_markdown(test_db, ver.id)
    assert "Executive Summary" in md_output
    
    val = await rendering_pipeline.validate_html(html_output)
    assert val["valid"] is True

@pytest.mark.asyncio
async def test_modify_one_paragraph(test_db: AsyncSession):
    """Test Case 2: Modify one paragraph (ensure only one node changes, unchanged nodes are linked)."""
    
    doc_id = uuid.uuid4()
    doc = Document(id=doc_id, title="Test Doc", slug=f"doc-{doc_id.hex[:6]}", status="draft")
    ver1 = DocumentVersion(id=uuid.uuid4(), document_id=doc_id, version_number=1, change_type="AI_GENERATION")
    
    test_db.add_all([doc, ver1])
    await test_db.flush()
    
    sec = DocumentSection(id=uuid.uuid4(), version_id=ver1.id, stable_id="sec_1", section_order=1, title="Test")
    block1 = DocumentBlock(id=uuid.uuid4(), section_id=sec.id, stable_id="block_1", block_order=1, block_type="paragraph", markdown="Hello")
    block2 = DocumentBlock(id=uuid.uuid4(), section_id=sec.id, stable_id="block_2", block_order=2, block_type="paragraph", markdown="World")
    
    test_db.add_all([sec, block1, block2])
    await test_db.commit()
    
    actor_id = uuid.uuid4()
    
    # Human edit block 1
    new_ver = await iteration_engine.human_edit_node(
        test_db, doc_id, ver1.id, "block_1", "Hello Edited", actor_id
    )
    
    # Assert version 2 created
    assert new_ver.version_number == 2
    assert new_ver.change_type == "HUMAN_EDIT"
    
    # Assert nodes
    stmt = select(DocumentBlock).join(DocumentSection).where(
        DocumentSection.version_id == new_ver.id
    ).order_by(DocumentBlock.block_order)
    new_blocks = (await test_db.execute(stmt)).scalars().all()
    
    assert len(new_blocks) == 2
    assert new_blocks[0].markdown == "Hello Edited"
    assert new_blocks[1].markdown == "World"  # Linked correctly

    # Assert IterationHistory
    hist_stmt = select(IterationHistory).where(IterationHistory.version_id == new_ver.id)
    hist = (await test_db.execute(hist_stmt)).scalars().first()
    assert hist.stable_id == "block_1"
    assert hist.actor_type == "Human"
    assert hist.previous_content["markdown"] == "Hello"

@pytest.mark.asyncio
async def test_ai_regenerate_section(test_db: AsyncSession):
    """Test Case 3 & 5: AI regenerates a node (context preservation & stability check)."""
    doc_id = uuid.uuid4()
    doc = Document(id=doc_id, title="Test AI Doc", slug=f"ai-{doc_id.hex[:6]}", status="draft")
    ver1 = DocumentVersion(id=uuid.uuid4(), document_id=doc_id, version_number=1, change_type="AI_GENERATION")
    
    test_db.add_all([doc, ver1])
    await test_db.flush()
    
    sec = DocumentSection(id=uuid.uuid4(), version_id=ver1.id, stable_id="sec_1", section_order=1, title="Summary")
    block1 = DocumentBlock(id=uuid.uuid4(), section_id=sec.id, stable_id="block_1", block_order=1, block_type="paragraph", markdown="Old summary")
    
    test_db.add_all([sec, block1])
    await test_db.commit()
    
    actor_id = uuid.uuid4()
    
    new_ver = await iteration_engine.regenerate_node(
        test_db, doc_id, ver1.id, "block_1", "Make it longer", actor_id
    )
    
    # Assert blocks
    stmt = select(DocumentBlock).join(DocumentSection).where(DocumentSection.version_id == new_ver.id)
    b = (await db.execute(stmt)).scalars().first()
    
    assert "Old summary" in b.markdown
    assert "AI Refined based on: Make it longer" in b.markdown
    
    # Assert IterationHistory has prompt
    hist_stmt = select(IterationHistory).where(IterationHistory.version_id == new_ver.id)
    hist = (await db.execute(hist_stmt)).scalars().first()
    assert hist.prompt == "Make it longer"
    assert hist.actor_type == "AI"
