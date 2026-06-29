import pytest
import uuid
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.models.iteration import IterationHistory
from app.services.canonical import MarkdownParser, VersionManager
from app.services.rendering import rendering_pipeline
from app.services.iteration import iteration_engine

@pytest.mark.asyncio
async def test_full_report_ingestion(db: AsyncSession):
    """Test Case 1: Full report ingestion into Canonical format -> HTML -> Markdown -> PDF."""
    
    doc = Document(
        id=uuid.uuid4(),
        title="Fusion Report",
        slug="fusion-test",
        document_type="Thought Leadership",
        status="draft"
    )
    db.add(doc)
    await db.flush()

    ver = DocumentVersion(
        id=uuid.uuid4(),
        document_id=doc.id,
        version_number=1,
        change_type="AI_GENERATION"
    )
    db.add(ver)
    doc.current_version_id = ver.id
    await db.flush()

    # Parse sample markdown
    md_content = "# Executive Summary\n\nFusion energy is the future.\n\n# Market\n\n$10B TAM by 2030.\n\n- Point 1\n- Point 2"
    sections = MarkdownParser.parse_to_canonical(md_content, ver.id)
    for sec in sections:
        db.add(sec)
        for b in sec.blocks:
            b.section_id = sec.id
            db.add(b)
    
    await db.commit()

    # Verify rendering
    html_output = await rendering_pipeline.render_html(db, ver.id)
    assert "<article" in html_output
    assert "Executive Summary" in html_output
    assert "Fusion energy" in html_output

    md_output = await rendering_pipeline.render_markdown(db, ver.id)
    assert "Executive Summary" in md_output
    
    val = await rendering_pipeline.validate_html(html_output)
    assert val["valid"] is True

@pytest.mark.asyncio
async def test_modify_one_paragraph(db: AsyncSession):
    """Test Case 2: Modify one paragraph (ensure only one node changes, unchanged nodes are linked)."""
    
    doc_id = uuid.uuid4()
    doc = Document(id=doc_id, title="Test Doc", slug=f"doc-{doc_id.hex[:6]}", status="draft")
    ver1 = DocumentVersion(id=uuid.uuid4(), document_id=doc_id, version_number=1, change_type="AI_GENERATION")
    
    db.add_all([doc, ver1])
    await db.flush()
    
    sec = DocumentSection(version_id=ver1.id, stable_id="sec_1", section_order=1, title="Test")
    block1 = DocumentBlock(section_id=sec.id, stable_id="block_1", block_order=1, block_type="paragraph", markdown="Hello")
    block2 = DocumentBlock(section_id=sec.id, stable_id="block_2", block_order=2, block_type="paragraph", markdown="World")
    
    db.add_all([sec, block1, block2])
    await db.commit()
    
    actor_id = uuid.uuid4()
    
    # Human edit block 1
    new_ver = await iteration_engine.human_edit_node(
        db, doc_id, ver1.id, "block_1", "Hello Edited", actor_id
    )
    
    # Assert version 2 created
    assert new_ver.version_number == 2
    assert new_ver.change_type == "HUMAN_EDIT"
    
    # Assert nodes
    stmt = select(DocumentBlock).join(DocumentSection).where(
        DocumentSection.version_id == new_ver.id
    ).order_by(DocumentBlock.block_order)
    new_blocks = (await db.execute(stmt)).scalars().all()
    
    assert len(new_blocks) == 2
    assert new_blocks[0].markdown == "Hello Edited"
    assert new_blocks[1].markdown == "World"  # Linked correctly

    # Assert IterationHistory
    hist_stmt = select(IterationHistory).where(IterationHistory.version_id == new_ver.id)
    hist = (await db.execute(hist_stmt)).scalars().first()
    assert hist.stable_id == "block_1"
    assert hist.actor_type == "Human"
    assert hist.previous_content["markdown"] == "Hello"

@pytest.mark.asyncio
async def test_ai_regenerate_section(db: AsyncSession):
    """Test Case 3 & 5: AI regenerates a node (context preservation & stability check)."""
    doc_id = uuid.uuid4()
    doc = Document(id=doc_id, title="Test AI Doc", slug=f"ai-{doc_id.hex[:6]}", status="draft")
    ver1 = DocumentVersion(id=uuid.uuid4(), document_id=doc_id, version_number=1, change_type="AI_GENERATION")
    
    db.add_all([doc, ver1])
    await db.flush()
    
    sec = DocumentSection(version_id=ver1.id, stable_id="sec_1", section_order=1, title="Summary")
    block1 = DocumentBlock(section_id=sec.id, stable_id="block_1", block_order=1, block_type="paragraph", markdown="Old summary")
    
    db.add_all([sec, block1])
    await db.commit()
    
    actor_id = uuid.uuid4()
    
    new_ver = await iteration_engine.regenerate_node(
        db, doc_id, ver1.id, "block_1", "Make it longer", actor_id
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
